package com.example.meeting.agentgateway;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import com.example.meeting.common.security.AgentContextTokenService;
import com.example.meeting.common.security.AuthenticatedUser;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

@SpringBootTest(
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {
      "server.address=127.0.0.1",
      "spring.datasource.url=jdbc:h2:mem:mcp_bridge;MODE=MySQL;DATABASE_TO_LOWER=TRUE;CASE_INSENSITIVE_IDENTIFIERS=TRUE;DB_CLOSE_DELAY=-1"
    })
@ActiveProfiles("test")
class AgentMcpIntegrationTest {
  private static final String SECRET =
      "test-only-agent-context-secret-with-at-least-thirty-two-bytes";
  private static final String SERVICE_TOKEN = "test-only-internal-service-token";
  @LocalServerPort private int port;
  @Autowired private ObjectMapper mapper;
  @Autowired private AgentContextTokenService tokens;
  @Autowired private JdbcTemplate jdbc;
  private final HttpClient client = HttpClient.newHttpClient();

  @BeforeEach
  void cleanState() {
    for (String table :
        List.of(
            "agent_tool_audit",
            "event_consume_record",
            "notification",
            "message_outbox",
            "booking_request",
            "booking_draft",
            "idempotency_record",
            "employee_busy_slot",
            "meeting_room_slot",
            "meeting_participant",
            "meeting")) {
      jdbc.update("DELETE FROM " + table);
    }
    jdbc.update("UPDATE meeting_room SET status='ACTIVE' WHERE code <> 'HQ-MAINT-702'");
  }

  @Test
  void realServletAuthenticatesProtocolRequestsAndPublishesElevenTools() throws Exception {
    String run = "run_" + UUID.randomUUID();
    String token = issue(1001, "zhangsan", "EMPLOYEE", run);
    assertThat(
            post(
                    "initialize",
                    Map.of(
                        "protocolVersion",
                        "2025-11-25",
                        "capabilities",
                        Map.of(),
                        "clientInfo",
                        Map.of("name", "smoke", "version", "1")),
                    token,
                    run,
                    false,
                    null)
                .statusCode())
        .isEqualTo(401);
    assertThat(post("tools/list", Map.of(), token, run, true, null).statusCode()).isEqualTo(200);
    JsonNode list = mapper.readTree(post("tools/list", Map.of(), token, run, true, null).body());
    assertThat(list.at("/result/tools").size()).isEqualTo(11);
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM agent_tool_audit", Integer.class))
        .isZero();
    String legacy =
        Jwts.builder()
            .subject("1001")
            .claim("roles", List.of("EMPLOYEE"))
            .claim("runId", run)
            .claim("traceId", "trace_" + run)
            .audience()
            .add("agent-service")
            .and()
            .expiration(Date.from(Instant.now().plusSeconds(600)))
            .signWith(Keys.hmacShaKeyFor(SECRET.getBytes(StandardCharsets.UTF_8)), Jwts.SIG.HS256)
            .compact();
    assertThat(post("tools/list", Map.of(), legacy, run, true, null).statusCode()).isEqualTo(401);
    assertThat(post("tools/list", Map.of(), token, "wrong_run", true, null).statusCode())
        .isEqualTo(401);
    assertThat(
            post("tools/list", Map.of(), token, run, true, "https://untrusted.example")
                .statusCode())
        .isEqualTo(403);
  }

  @Test
  void explicitBeanValidationAndAuditReplayCannotBypassIdentity() throws Exception {
    String run = "run_" + UUID.randomUUID();
    String token = issue(1001, "zhangsan", "EMPLOYEE", run);
    Map<String, Object> call =
        Map.of("name", "resolve_employees", "arguments", Map.of("names", List.of("zhangsan")));
    JsonNode data = mapper.readTree(post("tools/call", call, token, run, true, null).body());
    assertThat(data.at("/result/structuredContent/data/employees/0/employeeId").asLong())
        .isEqualTo(1001);
    assertThat(post("tools/call", call, token, run, true, null).statusCode()).isEqualTo(200);
    assertThat(jdbc.queryForObject("SELECT COUNT(*) FROM agent_tool_audit", Integer.class))
        .isEqualTo(1);
    JsonNode forbidden =
        mapper.readTree(
            post("tools/call", call, issue(1002, "admin", "ADMIN", run), run, true, null).body());
    assertThat(forbidden.at("/result/structuredContent/error/code").asText())
        .isEqualTo("FORBIDDEN");
    Map<String, Object> invalid =
        Map.of("name", "resolve_employees", "arguments", Map.of("names", List.of("")));
    JsonNode failure = mapper.readTree(post("tools/call", invalid, token, run, true, null).body());
    assertThat(failure.at("/result/structuredContent/error/code").asText())
        .isEqualTo("VALIDATION_ERROR");
    Map<String, Object> unknown =
        Map.of("name", "get_recent_meeting", "arguments", Map.of("userId", 1002));
    assertThat(
            mapper
                .readTree(post("tools/call", unknown, token, run, true, null).body())
                .at("/result/structuredContent/error/code")
                .asText())
        .isEqualTo("VALIDATION_ERROR");
  }

  @Test
  void officialPythonSdkAndSynchronousWorkflowBridge() throws Exception {
    String python = System.getenv("MCP_PYTHON_EXECUTABLE");
    assumeTrue(
        python != null && !python.isBlank(),
        "Run scripts/Test-McpToolBridge.ps1 for Python interoperability");
    Path root = Path.of(System.getProperty("user.dir")).getParent();
    String run = "run_python_" + UUID.randomUUID();
    String otherRun = "run_python_other_" + UUID.randomUUID();
    ProcessBuilder builder =
        new ProcessBuilder(
                python,
                root.resolve("scripts/mcp_bridge_smoke.py").toString(),
                "http://127.0.0.1:" + port)
            .directory(root.resolve("agent-service").toFile())
            .redirectErrorStream(true);
    builder.environment().put("PYTHONPATH", root.resolve("agent-service").toString());
    Path outputFile = Files.createTempFile("meeting-mcp-bridge-", ".log");
    builder.redirectOutput(outputFile.toFile());
    Process process = builder.start();
    try {
      process
          .getOutputStream()
          .write(
              mapper.writeValueAsBytes(
                  Map.of(
                      "runId",
                      run,
                      "traceId",
                      "trace_" + run,
                      "token",
                      issue(1001, "zhangsan", "EMPLOYEE", run),
                      "otherRunId",
                      otherRun,
                      "otherTraceId",
                      "trace_" + otherRun,
                      "otherToken",
                      issue(1003, "lisi", "EMPLOYEE", otherRun))));
      process.getOutputStream().close();
      assertThat(process.waitFor(120, TimeUnit.SECONDS)).isTrue();
      String output = Files.readString(outputFile, StandardCharsets.UTF_8);
      System.out.println(output);
      assertThat(process.exitValue()).withFailMessage(output).isZero();
      assertThat(output).contains("MCP_BRIDGE_OK");
      assertThat(
              jdbc.queryForObject(
                  "SELECT COUNT(*) FROM agent_tool_audit WHERE risk_level='WRITE'", Integer.class))
          .isGreaterThanOrEqualTo(4);
      assertThat(
              jdbc.queryForObject(
                  "SELECT COUNT(*) FROM booking_request WHERE status='PENDING'", Integer.class))
          .isEqualTo(1);
    } finally {
      if (process.isAlive()) process.destroyForcibly();
      Files.deleteIfExists(outputFile);
    }
  }

  private String issue(long id, String username, String role, String run) {
    return tokens.issue(new AuthenticatedUser(id, username, List.of(role)), "trace_" + run, run);
  }

  private HttpResponse<String> post(
      String method,
      Map<String, Object> params,
      String token,
      String run,
      boolean serviceToken,
      String origin)
      throws Exception {
    var request =
        HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + port + "/internal/v1/mcp"))
            .timeout(Duration.ofSeconds(20))
            .header("Content-Type", "application/json")
            .header("Accept", "application/json, text/event-stream")
            .header("Authorization", "Bearer " + token)
            .header("X-Run-Id", run)
            .header("X-Trace-Id", "trace_" + run)
            .header("X-Tool-Call-Id", "tool_test")
            .POST(
                HttpRequest.BodyPublishers.ofString(
                    mapper.writeValueAsString(
                        Map.of("jsonrpc", "2.0", "id", 1, "method", method, "params", params))));
    if (serviceToken) request.header("X-Service-Token", SERVICE_TOKEN);
    if (origin != null) request.header("Origin", origin);
    return client.send(request.build(), HttpResponse.BodyHandlers.ofString());
  }
}
