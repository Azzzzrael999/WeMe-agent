package com.example.meeting.agentgateway;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.example.meeting.common.security.AgentContextTokenService;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.security.JwtService;
import com.example.meeting.mq.BookingCommandProcessor;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class AgentToolGatewayIntegrationTest {

  private static final String SERVICE_TOKEN = "test-only-internal-service-token";
  private static final String AGENT_SECRET =
      "test-only-agent-context-secret-with-at-least-thirty-two-bytes";

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private JdbcTemplate jdbcTemplate;
  @Autowired private AgentContextTokenService agentContextTokenService;
  @Autowired private JwtService jwtService;
  @Autowired private BookingCommandProcessor bookingCommandProcessor;

  @BeforeEach
  void cleanState() {
    jdbcTemplate.update("DELETE FROM agent_tool_audit");
    jdbcTemplate.update("DELETE FROM event_consume_record");
    jdbcTemplate.update("DELETE FROM notification");
    jdbcTemplate.update("DELETE FROM message_outbox");
    jdbcTemplate.update("DELETE FROM booking_request");
    jdbcTemplate.update("DELETE FROM booking_draft");
    jdbcTemplate.update("DELETE FROM idempotency_record");
    jdbcTemplate.update("DELETE FROM employee_busy_slot");
    jdbcTemplate.update("DELETE FROM meeting_room_slot");
    jdbcTemplate.update("DELETE FROM meeting_participant");
    jdbcTemplate.update("DELETE FROM meeting");
    jdbcTemplate.update("UPDATE meeting_room SET status = 'ACTIVE'");
  }

  @Test
  void enforcesInternalTokensLimitsAuditReplayAndFailureAudit() throws Exception {
    ToolIdentity identity = identity("run_security", "tool_resolve");
    String resolveBody = "{\"names\":[\"zhangsan\"],\"departmentNames\":[]}";

    performTool("/internal/v1/tools/resolve-employees", resolveBody, identity, null, false)
        .andExpect(status().isUnauthorized())
        .andExpect(jsonPath("$.code").value("SERVICE_TOKEN_INVALID"));

    ToolIdentity badAudience =
        new ToolIdentity("trace_bad_aud", "run_bad_aud", "tool_bad_aud", issueBadAudienceToken());
    performTool(
            "/internal/v1/tools/resolve-employees", resolveBody, badAudience, SERVICE_TOKEN, true)
        .andExpect(status().isUnauthorized())
        .andExpect(jsonPath("$.code").value("AGENT_CONTEXT_INVALID"));

    performTool("/internal/v1/tools/resolve-employees", resolveBody, identity, SERVICE_TOKEN, true)
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.employees[0].employeeId").value(1001));
    performTool("/internal/v1/tools/resolve-employees", resolveBody, identity, SERVICE_TOKEN, true)
        .andExpect(status().isOk());
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM agent_tool_audit WHERE run_id='run_security'", Integer.class))
        .isEqualTo(1);

    performTool(
            "/internal/v1/tools/resolve-employees",
            "{\"names\":[\"admin\"],\"departmentNames\":[]}",
            identity,
            SERVICE_TOKEN,
            true)
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("IDEMPOTENCY_KEY_REUSED"));

    ToolIdentity overLimit = identity("run_limit", "tool_limit");
    List<Long> tooMany = new ArrayList<>();
    for (long id = 1; id <= 51; id++) {
      tooMany.add(id);
    }
    performTool(
            "/internal/v1/tools/get-employee-free-busy",
            objectMapper.writeValueAsString(
                Map.of(
                    "employeeIds",
                    tooMany,
                    "from",
                    "2026-08-01T00:00:00+08:00",
                    "to",
                    "2026-08-02T00:00:00+08:00")),
            overLimit,
            SERVICE_TOKEN,
            true)
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));

    ToolIdentity failed = identity("run_failed_audit", "tool_failed_audit");
    performTool(
            "/internal/v1/tools/get-employee-free-busy",
            """
            {"employeeIds":[1001],"from":"2026-08-01T00:00:00+08:00","to":"2026-08-16T00:00:00+08:00"}
            """,
            failed,
            SERVICE_TOKEN,
            true)
        .andExpect(status().isBadRequest());
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT result_code FROM agent_tool_audit WHERE run_id='run_failed_audit'",
                String.class))
        .isEqualTo("VALIDATION_ERROR");
  }

  @Test
  void hotDraftHasNoOccupancyAndConfirmationAtomicallyReturnsPending() throws Exception {
    PendingBooking pending = createHotPending("2026-09-01", "hot_pending");

    assertThat(pending.requestNo()).startsWith("BR");
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class)).isZero();
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting_room_slot", Integer.class))
        .isZero();
    assertThat(
            jdbcTemplate.queryForObject("SELECT COUNT(*) FROM employee_busy_slot", Integer.class))
        .isZero();
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM booking_draft WHERE confirmation_token=?",
                String.class,
                pending.confirmationToken()))
        .isEqualTo("USED");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM booking_request WHERE request_no=? AND status='PENDING'",
                Integer.class,
                pending.requestNo()))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM message_outbox WHERE aggregate_id=? AND event_type='BOOKING_COMMAND' AND status='NEW'",
                Integer.class,
                pending.requestNo()))
        .isEqualTo(1);

    mockMvc
        .perform(
            get("/api/v1/booking-requests/{requestNo}", pending.requestNo())
                .header("Authorization", "Bearer " + userAccessToken()))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.status").value("PENDING"))
        .andExpect(jsonPath("$.data.requestNo").value(pending.requestNo()));
    mockMvc
        .perform(
            get("/api/v1/booking-requests/{requestNo}", pending.requestNo())
                .header("Authorization", "Bearer " + adminAccessToken()))
        .andExpect(status().isOk());
    jdbcTemplate.update(
        "UPDATE booking_request SET user_id=1002 WHERE request_no=?", pending.requestNo());
    mockMvc
        .perform(
            get("/api/v1/booking-requests/{requestNo}", pending.requestNo())
                .header("Authorization", "Bearer " + userAccessToken()))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("BOOKING_REQUEST_NOT_FOUND"));
  }

  @Test
  void bookingCommandSuccessAndDuplicateMessageCreateOneMeeting() throws Exception {
    PendingBooking pending = createHotPending("2026-09-02", "mq_success");
    String eventJson = commandEvent(pending.requestNo());

    bookingCommandProcessor.process(eventJson);
    bookingCommandProcessor.process(eventJson);

    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM booking_request WHERE request_no=?",
                String.class,
                pending.requestNo()))
        .isEqualTo("SUCCESS");
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting WHERE request_no=? AND source='AGENT'",
                Integer.class,
                pending.requestNo()))
        .isEqualTo(1);
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting_room_slot", Integer.class))
        .isEqualTo(2);
    assertThat(
            jdbcTemplate.queryForObject("SELECT COUNT(*) FROM event_consume_record", Integer.class))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM message_outbox WHERE event_type='BOOKING_RESULT'",
                Integer.class))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM message_outbox WHERE event_type='MEETING_CONFIRMED'",
                Integer.class))
        .isEqualTo(1);
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM notification", Integer.class))
        .isEqualTo(2);
  }

  @Test
  void bookingCommandConflictReachesTerminalWithoutDuplicateMeeting() throws Exception {
    createManualMeeting(103, "2026-09-03T10:00:00+08:00", "2026-09-03T11:00:00+08:00");
    PendingBooking pending = createHotPending("2026-09-03", "mq_conflict");

    bookingCommandProcessor.process(commandEvent(pending.requestNo()));

    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM booking_request WHERE request_no=?",
                String.class,
                pending.requestNo()))
        .isEqualTo("CONFLICT");
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM message_outbox WHERE event_type='BOOKING_RESULT'",
                Integer.class))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject("SELECT COUNT(*) FROM event_consume_record", Integer.class))
        .isEqualTo(1);
  }

  @Test
  void rescheduleAndCancellationDraftsHaveNoEffectBeforeIdempotentConfirmation() throws Exception {
    long meetingId =
        createManualMeeting(101, "2026-09-04T09:00:00+08:00", "2026-09-04T10:00:00+08:00");
    ToolIdentity reschedule = identity("run_mutation", "tool_reschedule_draft");
    Map<String, Object> rescheduleBody =
        new LinkedHashMap<>(
            Map.of(
                "meetingId",
                meetingId,
                "title",
                "Agent 改期",
                "meetingType",
                "ARCHITECTURE_REVIEW",
                "roomId",
                102,
                "startAt",
                "2026-09-04T10:00:00+08:00",
                "endAt",
                "2026-09-04T11:00:00+08:00",
                "requiredParticipantIds",
                List.of(),
                "optionalParticipantIds",
                List.of()));
    rescheduleBody.put("createVideoConference", false);
    rescheduleBody.put("expectedVersion", 0);
    MvcResult preview =
        performTool(
                "/internal/v1/tools/reschedule-drafts",
                objectMapper.writeValueAsString(rescheduleBody),
                reschedule,
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andReturn();
    String rescheduleToken = data(preview).get("confirmationToken").asText();
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT room_id FROM meeting WHERE id=?", Long.class, meetingId))
        .isEqualTo(101L);

    ToolIdentity rescheduleConfirm = identity("run_mutation", "tool_reschedule_confirm");
    performTool(
            "/internal/v1/tools/reschedule-drafts/" + rescheduleToken + "/confirm",
            null,
            rescheduleConfirm,
            SERVICE_TOKEN,
            true,
            "mutation-reschedule-key")
        .andExpect(status().isOk());
    performTool(
            "/internal/v1/tools/reschedule-drafts/" + rescheduleToken + "/confirm",
            null,
            rescheduleConfirm,
            SERVICE_TOKEN,
            true,
            "mutation-reschedule-key")
        .andExpect(status().isOk());
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT room_id FROM meeting WHERE id=?", Long.class, meetingId))
        .isEqualTo(102L);

    ToolIdentity cancellation = identity("run_mutation", "tool_cancel_draft");
    MvcResult cancellationPreview =
        performTool(
                "/internal/v1/tools/cancellation-previews",
                objectMapper.writeValueAsString(Map.of("meetingId", meetingId)),
                cancellation,
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andReturn();
    String cancellationToken = data(cancellationPreview).get("confirmationToken").asText();
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM meeting WHERE id=?", String.class, meetingId))
        .isEqualTo("CONFIRMED");

    ToolIdentity cancellationConfirm = identity("run_mutation", "tool_cancel_confirm");
    performTool(
            "/internal/v1/tools/cancellation-previews/" + cancellationToken + "/confirm",
            null,
            cancellationConfirm,
            SERVICE_TOKEN,
            true,
            "mutation-cancel-key")
        .andExpect(status().isOk());
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM meeting WHERE id=?", String.class, meetingId))
        .isEqualTo("CANCELLED");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_room_slot WHERE meeting_id=?",
                Integer.class,
                meetingId))
        .isZero();
  }

  @Test
  void mutationToolCannotPreviewAnotherUsersMeeting() throws Exception {
    long meetingId =
        createManualMeeting(
            adminAccessToken(), 101, "2026-09-05T09:00:00+08:00", "2026-09-05T10:00:00+08:00");

    performTool(
            "/internal/v1/tools/cancellation-previews",
            objectMapper.writeValueAsString(Map.of("meetingId", meetingId)),
            identity("run_forbidden", "tool_forbidden"),
            SERVICE_TOKEN,
            true)
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("MEETING_NOT_FOUND"));
  }

  @Test
  void agentSseBoundaryReturnsStableUnavailableErrorWithoutFabricatedEvents() throws Exception {
    mockMvc
        .perform(
            post("/api/v1/agent/runs/stream")
                .header("Authorization", "Bearer " + userAccessToken())
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .content(
                    """
                    {"threadId":null,"message":"安排会议","clientRequestId":"client-1"}
                    """))
        .andExpect(request().asyncNotStarted())
        .andExpect(status().isServiceUnavailable())
        .andExpect(
            org.springframework.test.web.servlet.result.MockMvcResultMatchers.content()
                .contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
        .andExpect(jsonPath("$.code").value("AGENT_UNAVAILABLE"))
        .andExpect(jsonPath("$.details").isArray())
        .andExpect(jsonPath("$.traceId").isNotEmpty());
  }

  private PendingBooking createHotPending(String date, String prefix) throws Exception {
    ToolIdentity draftIdentity = identity("run_" + prefix, "tool_" + prefix + "_draft");
    MvcResult draftResult =
        performTool(
                "/internal/v1/tools/booking-drafts",
                meetingBody("热门预约", 103, date + "T10:00:00+08:00", date + "T11:00:00+08:00"),
                draftIdentity,
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andReturn();
    String confirmationToken = data(draftResult).get("confirmationToken").asText();
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class))
        .isEqualTo(prefix.contains("conflict") ? 1 : 0);

    ToolIdentity confirmIdentity = identity("run_" + prefix, "tool_" + prefix + "_confirm");
    MvcResult confirmResult =
        performTool(
                "/internal/v1/tools/booking-drafts/" + confirmationToken + "/confirm",
                null,
                confirmIdentity,
                SERVICE_TOKEN,
                true,
                "idem-" + prefix)
            .andExpect(status().isAccepted())
            .andExpect(jsonPath("$.data.status").value("PENDING"))
            .andReturn();
    return new PendingBooking(confirmationToken, data(confirmResult).get("requestNo").asText());
  }

  private long createManualMeeting(long roomId, String startAt, String endAt) throws Exception {
    return createManualMeeting(userAccessToken(), roomId, startAt, endAt);
  }

  private long createManualMeeting(String token, long roomId, String startAt, String endAt)
      throws Exception {
    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/meetings")
                    .header("Authorization", "Bearer " + token)
                    .header("Idempotency-Key", UUID.randomUUID().toString())
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(meetingBody("手动阻塞会议", roomId, startAt, endAt)))
            .andExpect(status().isOk())
            .andReturn();
    return data(result).get("id").asLong();
  }

  private String meetingBody(String title, long roomId, String startAt, String endAt)
      throws Exception {
    return objectMapper.writeValueAsString(
        Map.of(
            "title",
            title,
            "meetingType",
            "ARCHITECTURE_REVIEW",
            "roomId",
            roomId,
            "startAt",
            startAt,
            "endAt",
            endAt,
            "requiredParticipantIds",
            List.of(),
            "optionalParticipantIds",
            List.of(1002),
            "createVideoConference",
            false));
  }

  private org.springframework.test.web.servlet.ResultActions performTool(
      String path,
      String body,
      ToolIdentity identity,
      String serviceToken,
      boolean includeServiceToken)
      throws Exception {
    return performTool(path, body, identity, serviceToken, includeServiceToken, null);
  }

  private org.springframework.test.web.servlet.ResultActions performTool(
      String path,
      String body,
      ToolIdentity identity,
      String serviceToken,
      boolean includeServiceToken,
      String idempotencyKey)
      throws Exception {
    var request =
        post(path)
            .header("Authorization", "Bearer " + identity.token())
            .header("X-Trace-Id", identity.traceId())
            .header("X-Run-Id", identity.runId())
            .header("X-Tool-Call-Id", identity.toolCallId())
            .contentType(MediaType.APPLICATION_JSON);
    if (includeServiceToken) {
      request.header("X-Service-Token", serviceToken);
    }
    if (idempotencyKey != null) {
      request.header("Idempotency-Key", idempotencyKey);
    }
    if (body != null) {
      request.content(body);
    }
    return mockMvc.perform(request);
  }

  private ToolIdentity identity(String runId, String toolCallId) {
    String traceId = "trace_" + runId;
    String token =
        agentContextTokenService.issue(
            new AuthenticatedUser(1001, "zhangsan", List.of("EMPLOYEE")), traceId, runId);
    return new ToolIdentity(traceId, runId, toolCallId, token);
  }

  private String issueBadAudienceToken() {
    Instant now = Instant.now();
    return Jwts.builder()
        .subject("1001")
        .claim("roles", List.of("EMPLOYEE"))
        .claim("traceId", "trace_bad_aud")
        .claim("runId", "run_bad_aud")
        .audience()
        .add("wrong-audience")
        .and()
        .issuedAt(Date.from(now))
        .expiration(Date.from(now.plusSeconds(600)))
        .signWith(Keys.hmacShaKeyFor(AGENT_SECRET.getBytes(StandardCharsets.UTF_8)), Jwts.SIG.HS256)
        .compact();
  }

  private String userAccessToken() {
    return jwtService.issue(1001, "zhangsan", List.of("EMPLOYEE"));
  }

  private String adminAccessToken() {
    return jwtService.issue(1002, "admin", List.of("ADMIN"));
  }

  private String commandEvent(String requestNo) {
    return jdbcTemplate.queryForObject(
        "SELECT payload_json FROM message_outbox WHERE aggregate_id=? AND event_type='BOOKING_COMMAND'",
        String.class,
        requestNo);
  }

  private JsonNode data(MvcResult result) throws Exception {
    return objectMapper
        .readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8))
        .get("data");
  }

  private record ToolIdentity(String traceId, String runId, String toolCallId, String token) {}

  private record PendingBooking(String confirmationToken, String requestNo) {}
}
