package com.example.meeting.agentgateway;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.example.meeting.common.security.AgentContextIdentity;
import com.example.meeting.common.security.AgentContextTokenService;
import com.example.meeting.common.security.JwtService;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BiFunction;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class AgentGatewaySseProxyIntegrationTest {

  private static final String SERVICE_TOKEN = "test-only-internal-service-token";
  private static final String TRACE_ID = "trc_sse_proxy";
  private static final AtomicReference<UpstreamResponse> UPSTREAM_RESPONSE =
      new AtomicReference<>();
  private static final AtomicReference<CapturedUpstreamRequest> CAPTURED_REQUEST =
      new AtomicReference<>();
  private static final HttpServer UPSTREAM = createUpstream();

  @Autowired private MockMvc mockMvc;
  @Autowired private JwtService jwtService;
  @Autowired private AgentContextTokenService agentContextTokenService;
  @Autowired private ObjectMapper objectMapper;

  @DynamicPropertySource
  static void configureAgentServiceUrl(DynamicPropertyRegistry registry) {
    registry.add(
        "app.agent-service.url", () -> "http://127.0.0.1:" + UPSTREAM.getAddress().getPort());
  }

  @BeforeEach
  void resetUpstream() {
    CAPTURED_REQUEST.set(null);
    UPSTREAM_RESPONSE.set(
        new UpstreamResponse(
            503,
            MediaType.APPLICATION_JSON_VALUE,
            (runId, traceId) ->
                "{\"code\":\"UPSTREAM_UNAVAILABLE\"}".getBytes(StandardCharsets.UTF_8)));
  }

  @AfterAll
  static void stopUpstream() {
    UPSTREAM.stop(0);
  }

  @Test
  void relaysStandardSseByteForByteAndKeepsJavaIssuedContextConsistent() throws Exception {
    UPSTREAM_RESPONSE.set(
        new UpstreamResponse(
            200,
            "text/event-stream; charset=utf-8",
            AgentGatewaySseProxyIntegrationTest::standardSse));

    MvcResult started =
        streamRequest()
            .andExpect(status().isOk())
            .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM))
            .andExpect(request().asyncStarted())
            .andExpect(header().string("X-Trace-Id", TRACE_ID))
            .andExpect(header().exists("X-Run-Id"))
            .andReturn();

    String runId = started.getResponse().getHeader("X-Run-Id");
    started.getAsyncResult(5_000);

    assertThat(started.getResponse().getStatus()).isEqualTo(200);
    assertThat(started.getResponse().getContentAsByteArray())
        .isEqualTo(standardSse(runId, TRACE_ID));

    CapturedUpstreamRequest captured = CAPTURED_REQUEST.get();
    assertThat(captured).isNotNull();
    assertThat(captured.method()).isEqualTo("POST");
    assertThat(captured.accept()).isEqualTo(MediaType.TEXT_EVENT_STREAM_VALUE);
    assertThat(captured.upgrade()).isNull();
    assertThat(captured.serviceToken()).isEqualTo(SERVICE_TOKEN);
    assertThat(captured.traceId()).isEqualTo(TRACE_ID);
    assertThat(captured.runId()).isEqualTo(runId);

    AgentContextIdentity context =
        agentContextTokenService.parse(captured.authorization().substring("Bearer ".length()));
    assertThat(context.userId()).isEqualTo(1001L);
    assertThat(context.traceId()).isEqualTo(TRACE_ID);
    assertThat(context.runId()).isEqualTo(runId);

    JsonNode body = objectMapper.readTree(captured.body());
    assertThat(body.get("threadId").asText()).isEqualTo("thread_fixture");
    assertThat(body.get("message").asText()).isEqualTo("帮张三和李四安排架构评审");
    assertThat(body.get("clientRequestId").asText()).isEqualTo("client-sse-fixture");
  }

  @Test
  void upstreamNon2xxReturnsJsonInsteadOfFabricatedSse() throws Exception {
    UPSTREAM_RESPONSE.set(
        new UpstreamResponse(
            503,
            MediaType.TEXT_EVENT_STREAM_VALUE,
            (runId, traceId) -> standardSse(runId, traceId)));

    streamRequest()
        .andExpect(request().asyncNotStarted())
        .andExpect(status().isServiceUnavailable())
        .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
        .andExpect(header().doesNotExist("X-Run-Id"))
        .andExpect(jsonPath("$.code").value("AGENT_UNAVAILABLE"));
  }

  @Test
  void upstreamSuccessWithoutSseContentTypeReturnsStableUnavailableError() throws Exception {
    UPSTREAM_RESPONSE.set(
        new UpstreamResponse(
            200,
            MediaType.APPLICATION_JSON_VALUE,
            (runId, traceId) -> "{\"status\":\"RUNNING\"}".getBytes(StandardCharsets.UTF_8)));

    streamRequest()
        .andExpect(request().asyncNotStarted())
        .andExpect(status().isServiceUnavailable())
        .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
        .andExpect(header().doesNotExist("X-Run-Id"))
        .andExpect(jsonPath("$.code").value("AGENT_UNAVAILABLE"));
  }

  private org.springframework.test.web.servlet.ResultActions streamRequest() throws Exception {
    return mockMvc.perform(
        post("/api/v1/agent/runs/stream")
            .header("Authorization", "Bearer " + userAccessToken())
            .header("X-Trace-Id", TRACE_ID)
            .contentType(MediaType.APPLICATION_JSON)
            .accept(MediaType.TEXT_EVENT_STREAM)
            .content(
                """
                {"threadId":"thread_fixture","message":"帮张三和李四安排架构评审","clientRequestId":"client-sse-fixture"}
                """));
  }

  private String userAccessToken() {
    return jwtService.issue(1001, "zhangsan", java.util.List.of("EMPLOYEE"));
  }

  private static HttpServer createUpstream() {
    try {
      HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
      server.createContext(
          "/internal/v1/agent-runs/stream", AgentGatewaySseProxyIntegrationTest::handle);
      server.start();
      return server;
    } catch (IOException exception) {
      throw new IllegalStateException("Cannot start the Agent SSE test upstream", exception);
    }
  }

  private static void handle(HttpExchange exchange) throws IOException {
    byte[] body;
    try (InputStream input = exchange.getRequestBody()) {
      body = input.readAllBytes();
    }
    String runId = exchange.getRequestHeaders().getFirst("X-Run-Id");
    String traceId = exchange.getRequestHeaders().getFirst("X-Trace-Id");
    CAPTURED_REQUEST.set(
        new CapturedUpstreamRequest(
            exchange.getRequestMethod(),
            exchange.getRequestHeaders().getFirst("Accept"),
            exchange.getRequestHeaders().getFirst("Upgrade"),
            exchange.getRequestHeaders().getFirst("Authorization"),
            exchange.getRequestHeaders().getFirst("X-Service-Token"),
            traceId,
            runId,
            body));

    UpstreamResponse response = UPSTREAM_RESPONSE.get();
    byte[] responseBody = response.bodyFactory().apply(runId, traceId);
    exchange.getResponseHeaders().set("Content-Type", response.contentType());
    exchange.sendResponseHeaders(response.status(), responseBody.length);
    try (OutputStream output = exchange.getResponseBody()) {
      output.write(responseBody);
    }
  }

  private static byte[] standardSse(String runId, String traceId) {
    String payload =
        "event: run.started\r\n"
            + "data: {\"runId\":\""
            + runId
            + "\",\"threadId\":\"thread_fixture\",\"traceId\":\""
            + traceId
            + "\",\"status\":\"RUNNING\"}\r\n\r\n"
            + "event: agent.step\r\n"
            + "data: {\"runId\":\""
            + runId
            + "\",\"stepId\":\"step_fixture\",\"sequenceNo\":1,\"agentName\":\"supervisor\",\"nodeName\":\"supervisor_route\",\"status\":\"SUCCEEDED\",\"summary\":\"已路由到需求解析\",\"durationMs\":3}\r\n\r\n"
            + "event: tool.call\r\n"
            + "data: {\"runId\":\""
            + runId
            + "\",\"toolCallId\":\"tool_fixture\",\"toolName\":\"resolve_employees\",\"riskLevel\":\"READ\",\"status\":\"SUCCEEDED\",\"summary\":\"已解析 2 名员工\",\"durationMs\":12}\r\n\r\n"
            + "event: run.completed\r\n"
            + "data: {\"runId\":\""
            + runId
            + "\",\"status\":\"SUCCEEDED\",\"answerSummary\":\"已完成结构化解析和只读查询\",\"citations\":[]}\r\n\r\n";
    return payload.getBytes(StandardCharsets.UTF_8);
  }

  private record CapturedUpstreamRequest(
      String method,
      String accept,
      String upgrade,
      String authorization,
      String serviceToken,
      String traceId,
      String runId,
      byte[] body) {}

  private record UpstreamResponse(
      int status, String contentType, BiFunction<String, String, byte[]> bodyFactory) {}
}
