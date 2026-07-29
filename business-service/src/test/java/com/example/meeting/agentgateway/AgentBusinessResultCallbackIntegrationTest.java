package com.example.meeting.agentgateway;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.example.meeting.agentgateway.client.AgentBusinessResultCallback;
import com.example.meeting.common.security.AgentContextIdentity;
import com.example.meeting.common.security.AgentContextTokenService;
import com.example.meeting.mq.BookingResultPayload;
import com.example.meeting.outbox.EventEnvelope;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.time.OffsetDateTime;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

@SpringBootTest
@ActiveProfiles("test")
class AgentBusinessResultCallbackIntegrationTest {

  private static final String SERVICE_TOKEN = "test-only-internal-service-token";
  private static final String REQUEST_NO = "BR_CALLBACK_FIXTURE";
  private static final String RUN_ID = "run_callback_fixture";
  private static final String TRACE_ID = "trc_callback_fixture";
  private static final AtomicInteger RESPONSE_STATUS = new AtomicInteger();
  private static final AtomicReference<CapturedCallbackRequest> CAPTURED_REQUEST =
      new AtomicReference<>();
  private static final HttpServer UPSTREAM = createUpstream();

  @Autowired private AgentBusinessResultCallback callback;
  @Autowired private AgentContextTokenService agentContextTokenService;
  @Autowired private JdbcTemplate jdbcTemplate;
  @Autowired private ObjectMapper objectMapper;

  @DynamicPropertySource
  static void configureAgentService(DynamicPropertyRegistry registry) {
    registry.add(
        "app.agent-service.url", () -> "http://127.0.0.1:" + UPSTREAM.getAddress().getPort());
    registry.add("app.agent-service.callback-enabled", () -> true);
  }

  @BeforeEach
  void prepareRequest() {
    RESPONSE_STATUS.set(204);
    CAPTURED_REQUEST.set(null);
    jdbcTemplate.update("DELETE FROM booking_request WHERE request_no = ?", REQUEST_NO);
    jdbcTemplate.update(
        """
        INSERT INTO booking_request (
          request_no, user_id, run_id, trace_id, tool_call_id, operation, payload_json,
          status, meeting_id, error_code, error_message, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        REQUEST_NO,
        1001L,
        RUN_ID,
        TRACE_ID,
        "tool_callback_fixture",
        "CREATE",
        "{}",
        "SUCCESS",
        9001L);
  }

  @AfterAll
  static void stopUpstream() {
    UPSTREAM.stop(0);
  }

  @Test
  void signsOwnerContextAndForwardsBookingResultAfterItHasBeenPersisted() throws Exception {
    callback.deliver(eventJson());

    CapturedCallbackRequest captured = CAPTURED_REQUEST.get();
    assertThat(captured).isNotNull();
    assertThat(captured.path()).isEqualTo("/internal/v1/agent-runs/" + RUN_ID + "/business-result");
    assertThat(captured.method()).isEqualTo("POST");
    assertThat(captured.upgrade()).isNull();
    assertThat(captured.serviceToken()).isEqualTo(SERVICE_TOKEN);
    assertThat(captured.traceId()).isEqualTo(TRACE_ID);
    assertThat(captured.runId()).isEqualTo(RUN_ID);

    AgentContextIdentity context =
        agentContextTokenService.parse(captured.authorization().substring("Bearer ".length()));
    assertThat(context.userId()).isEqualTo(1001L);
    assertThat(context.roles()).containsExactly("EMPLOYEE");
    assertThat(context.traceId()).isEqualTo(TRACE_ID);
    assertThat(context.runId()).isEqualTo(RUN_ID);

    JsonNode body = objectMapper.readTree(captured.body());
    assertThat(body.get("eventId").asText()).isEqualTo("evt_callback_fixture");
    assertThat(body.get("requestNo").asText()).isEqualTo(REQUEST_NO);
    assertThat(body.get("status").asText()).isEqualTo("SUCCESS");
    assertThat(body.get("meetingId").asLong()).isEqualTo(9001L);
  }

  @Test
  void rejectsNonSuccessCallbackResponsesSoTheMqConsumerCanRetry() {
    RESPONSE_STATUS.set(503);

    assertThatThrownBy(() -> callback.deliver(eventJson()))
        .isInstanceOf(IllegalStateException.class)
        .hasMessage("Agent business result callback was rejected");
  }

  private String eventJson() {
    EventEnvelope event =
        new EventEnvelope(
            "evt_callback_fixture",
            "BOOKING_RESULT",
            "BOOKING_REQUEST",
            REQUEST_NO,
            TRACE_ID,
            RUN_ID,
            OffsetDateTime.parse("2026-08-19T15:30:00+08:00"),
            1,
            objectMapper.valueToTree(new BookingResultPayload(REQUEST_NO, "SUCCESS", 9001L, null)));
    try {
      return objectMapper.writeValueAsString(event);
    } catch (IOException exception) {
      throw new IllegalStateException("Cannot serialize callback fixture", exception);
    }
  }

  private static HttpServer createUpstream() {
    try {
      HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
      server.createContext(
          "/internal/v1/agent-runs/" + RUN_ID + "/business-result",
          AgentBusinessResultCallbackIntegrationTest::handle);
      server.start();
      return server;
    } catch (IOException exception) {
      throw new IllegalStateException("Cannot start the Agent callback test upstream", exception);
    }
  }

  private static void handle(HttpExchange exchange) throws IOException {
    byte[] body;
    try (InputStream input = exchange.getRequestBody()) {
      body = input.readAllBytes();
    }
    CAPTURED_REQUEST.set(
        new CapturedCallbackRequest(
            exchange.getRequestURI().getPath(),
            exchange.getRequestMethod(),
            exchange.getRequestHeaders().getFirst("Upgrade"),
            exchange.getRequestHeaders().getFirst("Authorization"),
            exchange.getRequestHeaders().getFirst("X-Service-Token"),
            exchange.getRequestHeaders().getFirst("X-Trace-Id"),
            exchange.getRequestHeaders().getFirst("X-Run-Id"),
            body));
    int status = RESPONSE_STATUS.get();
    exchange.sendResponseHeaders(status, -1);
    try (OutputStream output = exchange.getResponseBody()) {
      output.flush();
    }
  }

  private record CapturedCallbackRequest(
      String path,
      String method,
      String upgrade,
      String authorization,
      String serviceToken,
      String traceId,
      String runId,
      byte[] body) {}
}
