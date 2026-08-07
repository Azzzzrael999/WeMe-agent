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
    jdbcTemplate.update("UPDATE meeting_room SET status = 'ACTIVE' WHERE code <> 'HQ-MAINT-702'");
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
  void resolvesSeededNaturalLanguageEmployeeNames() throws Exception {
    performTool(
            "/internal/v1/tools/resolve-employees",
            "{\"names\":[\"张三\",\"李四\"],\"departmentNames\":[]}",
            identity("run_resolve_demo_names", "tool_resolve_demo_names"),
            SERVICE_TOKEN,
            true)
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.employees.length()").value(2))
        .andExpect(jsonPath("$.data.employees[0].employeeId").value(1001))
        .andExpect(jsonPath("$.data.employees[0].displayName").value("张三"))
        .andExpect(jsonPath("$.data.employees[1].employeeId").value(1003))
        .andExpect(jsonPath("$.data.employees[1].displayName").value("李四"))
        .andExpect(jsonPath("$.data.unresolvedNames.length()").value(0));
  }

  @Test
  void resolvesCurrentUsersDepartmentScopeWithoutTrustingCallerIdentity() throws Exception {
    performTool(
            "/internal/v1/tools/resolve-participant-scope",
            "{\"scope\":\"MY_DEPARTMENT\"}",
            identity("run_scope_demo", "tool_scope_demo"),
            SERVICE_TOKEN,
            true)
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.scope").value("MY_DEPARTMENT"))
        .andExpect(jsonPath("$.data.scopeName").value("研发中心"))
        .andExpect(jsonPath("$.data.members.length()").value(4))
        .andExpect(jsonPath("$.data.members[0].status").value("ACTIVE"));
  }

  @Test
  void recentMeetingToolExcludesCancelledMeetings() throws Exception {
    long confirmedMeetingId =
        createManualMeeting(101, "2026-09-01T09:00:00+08:00", "2026-09-01T10:00:00+08:00");
    long cancelledMeetingId =
        createManualMeeting(102, "2026-09-02T09:00:00+08:00", "2026-09-02T10:00:00+08:00");
    jdbcTemplate.update("UPDATE meeting SET status='CANCELLED' WHERE id=?", cancelledMeetingId);

    performTool(
            "/internal/v1/tools/get-recent-meeting",
            "{\"limit\":5}",
            identity("run_recent_confirmed", "tool_recent_confirmed"),
            SERVICE_TOKEN,
            true)
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.meetings.length()").value(1))
        .andExpect(jsonPath("$.data.meetings[0].id").value(confirmedMeetingId))
        .andExpect(jsonPath("$.data.meetings[0].status").value("CONFIRMED"));
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
  void synchronousDraftConflictReturnsStructuredServerSideEvidence() throws Exception {
    createManualMeeting(101, "2026-09-06T10:00:00+08:00", "2026-09-06T11:00:00+08:00");
    ToolIdentity draftIdentity = identity("run_sync_conflict", "tool_sync_conflict_draft");
    MvcResult draftResult =
        performTool(
                "/internal/v1/tools/booking-drafts",
                meetingBody(
                    "同步冲突证据", 101, "2026-09-06T10:00:00+08:00", "2026-09-06T11:00:00+08:00"),
                draftIdentity,
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andReturn();
    String confirmationToken = data(draftResult).get("confirmationToken").asText();

    performTool(
            "/internal/v1/tools/booking-drafts/" + confirmationToken + "/confirm",
            null,
            identity("run_sync_conflict", "tool_sync_conflict_confirm"),
            SERVICE_TOKEN,
            true,
            "idem-sync-conflict")
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("BOOKING_CONFLICT"))
        .andExpect(jsonPath("$.details[0].field").value("conflict.type"))
        .andExpect(jsonPath("$.details[0].reason").value("BOOKING_CONFLICT"))
        .andExpect(jsonPath("$.details[1].field").value("conflict.roomId"))
        .andExpect(jsonPath("$.details[1].reason").value("101"))
        .andExpect(jsonPath("$.details[2].field").value("conflict.slots"))
        .andExpect(jsonPath("$.details[2].reason").value("20,21"));
  }

  @Test
  void editedCreateDraftInvalidatesOldTokenBeforeAnyFormalSideEffect() throws Exception {
    String runId = "run_create_edit";
    MvcResult first =
        performTool(
                "/internal/v1/tools/booking-drafts",
                meetingBody("编辑前草案", 101, "2026-09-09T09:00:00+08:00", "2026-09-09T10:00:00+08:00"),
                identity(runId, "tool_create_before_edit"),
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andReturn();
    String oldToken = data(first).get("confirmationToken").asText();
    MvcResult edited =
        performTool(
                "/internal/v1/tools/booking-drafts",
                meetingBody("编辑后草案", 102, "2026-09-09T10:00:00+08:00", "2026-09-09T11:00:00+08:00"),
                identity(runId, "tool_create_after_edit"),
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data.draft.title").value("编辑后草案"))
            .andExpect(jsonPath("$.data.draft.roomId").value(102))
            .andReturn();
    String newToken = data(edited).get("confirmationToken").asText();

    assertThat(newToken).isNotEqualTo(oldToken);
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class)).isZero();
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting_room_slot", Integer.class))
        .isZero();
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM booking_draft WHERE confirmation_token=?",
                String.class,
                oldToken))
        .isEqualTo("REJECTED");

    performTool(
            "/internal/v1/tools/booking-drafts/" + oldToken + "/confirm",
            null,
            identity(runId, "tool_confirm_old_create"),
            SERVICE_TOKEN,
            true,
            "mutation-create-old-key")
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("DRAFT_ALREADY_USED"));
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class)).isZero();

    performTool(
            "/internal/v1/tools/booking-drafts/" + newToken + "/confirm",
            null,
            identity(runId, "tool_confirm_new_create"),
            SERVICE_TOKEN,
            true,
            "mutation-create-new-key")
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.status").value("SUCCESS"));
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class))
        .isEqualTo(1);
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting_room_slot", Integer.class))
        .isEqualTo(2);
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

    String eventJson = commandEvent(pending.requestNo());
    bookingCommandProcessor.process(eventJson);
    bookingCommandProcessor.process(eventJson);

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
    String resultPayload =
        jdbcTemplate.queryForObject(
            "SELECT payload_json FROM message_outbox WHERE event_type='BOOKING_RESULT'",
            String.class);
    JsonNode resultEvent = objectMapper.readTree(resultPayload);
    if (resultEvent.isTextual()) {
      resultEvent = objectMapper.readTree(resultEvent.asText());
    }
    assertThat(resultEvent.at("/payload/status").asText()).isEqualTo("CONFLICT");
    assertThat(resultEvent.at("/payload/conflict/type").asText()).isEqualTo("BOOKING_CONFLICT");
    assertThat(resultEvent.at("/payload/conflict/roomId").asLong()).isEqualTo(103L);
    assertThat(resultEvent.at("/payload/conflict/slots").isArray()).isTrue();
    assertThat(resultEvent.at("/payload/conflict/slots").size()).isEqualTo(2);
    assertThat(
            jdbcTemplate.queryForObject("SELECT COUNT(*) FROM event_consume_record", Integer.class))
        .isEqualTo(1);
  }

  @Test
  void sequentialHotDraftsRemainPendingAfterPreviousHotConflict() throws Exception {
    String date = "2026-09-05";
    createManualMeeting(103, date + "T10:00:00+08:00", date + "T11:00:00+08:00");
    PendingBooking first = createHotPending(date, "sequential_conflict_first");

    bookingCommandProcessor.process(commandEvent(first.requestNo()));

    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM booking_request WHERE request_no=?",
                String.class,
                first.requestNo()))
        .isEqualTo("CONFLICT");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT is_hot FROM meeting_room WHERE id=103", Boolean.class))
        .isTrue();

    PendingBooking second = createHotPending(date, "sequential_conflict_second");

    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM booking_request WHERE request_no=?",
                String.class,
                second.requestNo()))
        .isEqualTo("PENDING");
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class))
        .isEqualTo(1);
  }

  @Test
  void sequentialHotDraftsRemainPendingAfterPreviousHotSuccess() throws Exception {
    String date = "2026-09-06";
    PendingBooking first = createHotPending(date, "sequential_success_first");

    bookingCommandProcessor.process(commandEvent(first.requestNo()));

    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM booking_request WHERE request_no=?",
                String.class,
                first.requestNo()))
        .isEqualTo("SUCCESS");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT is_hot FROM meeting_room WHERE id=103", Boolean.class))
        .isTrue();

    PendingBooking second =
        createHotPending(
            date, "sequential_success_second", 1, "T13:00:00+08:00", "T14:00:00+08:00");

    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM booking_request WHERE request_no=?",
                String.class,
                second.requestNo()))
        .isEqualTo("PENDING");
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class))
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
    rescheduleBody.put("expectedVersion", 0);
    MvcResult preview =
        performTool(
                "/internal/v1/tools/reschedule-drafts",
                objectMapper.writeValueAsString(rescheduleBody),
                reschedule,
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data.before.id").value(meetingId))
            .andExpect(jsonPath("$.data.before.roomId").value(101))
            .andExpect(jsonPath("$.data.before.version").value(0))
            .andExpect(jsonPath("$.data.after.roomId").value(102))
            .andExpect(jsonPath("$.data.after.title").value("Agent 改期"))
            .andReturn();
    String rescheduleToken = data(preview).get("confirmationToken").asText();
    assertFormalMeetingState(meetingId, 101, "CONFIRMED", 0, 2, 2, 2);

    ToolIdentity rescheduleConfirm = identity("run_mutation", "tool_reschedule_confirm");
    performTool(
            "/internal/v1/tools/reschedule-drafts/" + rescheduleToken + "/confirm",
            null,
            rescheduleConfirm,
            SERVICE_TOKEN,
            true,
            "mutation-reschedule-key")
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.status").value("SUCCESS"))
        .andExpect(jsonPath("$.data.meetingId").value(meetingId));
    performTool(
            "/internal/v1/tools/reschedule-drafts/" + rescheduleToken + "/confirm",
            null,
            rescheduleConfirm,
            SERVICE_TOKEN,
            true,
            "mutation-reschedule-key")
        .andExpect(status().isOk());
    assertFormalMeetingState(meetingId, 102, "CONFIRMED", 1, 1, 2, 2);

    ToolIdentity cancellation = identity("run_mutation", "tool_cancel_draft");
    MvcResult cancellationPreview =
        performTool(
                "/internal/v1/tools/cancellation-previews",
                objectMapper.writeValueAsString(Map.of("meetingId", meetingId)),
                cancellation,
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data.meeting.id").value(meetingId))
            .andExpect(jsonPath("$.data.meeting.roomId").value(102))
            .andExpect(jsonPath("$.data.meeting.status").value("CONFIRMED"))
            .andReturn();
    String cancellationToken = data(cancellationPreview).get("confirmationToken").asText();
    assertFormalMeetingState(meetingId, 102, "CONFIRMED", 1, 1, 2, 2);

    ToolIdentity cancellationConfirm = identity("run_mutation", "tool_cancel_confirm");
    performTool(
            "/internal/v1/tools/cancellation-previews/" + cancellationToken + "/confirm",
            null,
            cancellationConfirm,
            SERVICE_TOKEN,
            true,
            "mutation-cancel-key")
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.status").value("SUCCESS"))
        .andExpect(jsonPath("$.data.meetingId").value(meetingId));
    assertFormalMeetingState(meetingId, 102, "CANCELLED", 2, 1, 0, 0);
  }

  @Test
  void editedMutationDraftInvalidatesOldTokenAndKeepsNewTokenConfirmable() throws Exception {
    long meetingId =
        createManualMeeting(101, "2026-09-07T09:00:00+08:00", "2026-09-07T10:00:00+08:00");
    ToolIdentity firstDraftIdentity = identity("run_mutation_edit", "tool_reschedule_before_edit");
    String firstBody =
        rescheduleBody(
            meetingId, "改期前草案", 102, "2026-09-07T10:00:00+08:00", "2026-09-07T11:00:00+08:00", 0);
    MvcResult firstDraft =
        performTool(
                "/internal/v1/tools/reschedule-drafts",
                firstBody,
                firstDraftIdentity,
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andReturn();
    String oldToken = data(firstDraft).get("confirmationToken").asText();

    MvcResult editedDraft =
        performTool(
                "/internal/v1/tools/reschedule-drafts",
                rescheduleBody(
                    meetingId,
                    "改期后草案",
                    102,
                    "2026-09-07T11:00:00+08:00",
                    "2026-09-07T12:00:00+08:00",
                    0),
                identity("run_mutation_edit", "tool_reschedule_after_edit"),
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data.before.id").value(meetingId))
            .andExpect(jsonPath("$.data.after.title").value("改期后草案"))
            .andReturn();
    String newToken = data(editedDraft).get("confirmationToken").asText();

    assertThat(newToken).isNotEqualTo(oldToken);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM booking_draft WHERE confirmation_token=?",
                String.class,
                oldToken))
        .isEqualTo("REJECTED");
    assertFormalMeetingState(meetingId, 101, "CONFIRMED", 0, 2, 2, 2);

    performTool(
            "/internal/v1/tools/reschedule-drafts/" + oldToken + "/confirm",
            null,
            identity("run_mutation_edit", "tool_confirm_old_token"),
            SERVICE_TOKEN,
            true,
            "mutation-edit-old-key")
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("DRAFT_ALREADY_USED"));
    assertFormalMeetingState(meetingId, 101, "CONFIRMED", 0, 2, 2, 2);

    performTool(
            "/internal/v1/tools/reschedule-drafts/" + newToken + "/confirm",
            null,
            identity("run_mutation_edit", "tool_confirm_new_token"),
            SERVICE_TOKEN,
            true,
            "mutation-edit-new-key")
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.meetingId").value(meetingId));
    assertFormalMeetingState(meetingId, 102, "CONFIRMED", 1, 2, 2, 2);
  }

  @Test
  void cancellationPreviewRejectsConfirmationAfterTargetMeetingChanges() throws Exception {
    long meetingId =
        createManualMeeting(101, "2026-09-08T09:00:00+08:00", "2026-09-08T10:00:00+08:00");
    MvcResult preview =
        performTool(
                "/internal/v1/tools/cancellation-previews",
                objectMapper.writeValueAsString(Map.of("meetingId", meetingId)),
                identity("run_cancel_stale", "tool_cancel_stale_preview"),
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data.meeting.version").value(0))
            .andReturn();
    String token = data(preview).get("confirmationToken").asText();
    assertFormalMeetingState(meetingId, 101, "CONFIRMED", 0, 2, 2, 2);

    mockMvc
        .perform(
            org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put(
                    "/api/v1/meetings/{meetingId}", meetingId)
                .header("Authorization", "Bearer " + userAccessToken())
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    rescheduleBody(
                        meetingId,
                        "预览后手动改期",
                        102,
                        "2026-09-08T10:00:00+08:00",
                        "2026-09-08T11:00:00+08:00",
                        0)))
        .andExpect(status().isOk());
    assertFormalMeetingState(meetingId, 102, "CONFIRMED", 1, 2, 2, 2);

    performTool(
            "/internal/v1/tools/cancellation-previews/" + token + "/confirm",
            null,
            identity("run_cancel_stale", "tool_cancel_stale_confirm"),
            SERVICE_TOKEN,
            true,
            "mutation-cancel-stale-key")
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("MEETING_STATE_CONFLICT"));
    assertFormalMeetingState(meetingId, 102, "CONFIRMED", 1, 2, 2, 2);
  }

  @Test
  void rescheduleConflictReturnsEvidenceAndPreservesOriginalMeeting() throws Exception {
    long targetMeetingId =
        createManualMeeting(101, "2026-09-10T09:00:00+08:00", "2026-09-10T10:00:00+08:00");
    createManualMeeting(102, "2026-09-10T10:00:00+08:00", "2026-09-10T11:00:00+08:00");
    MvcResult draft =
        performTool(
                "/internal/v1/tools/reschedule-drafts",
                rescheduleBody(
                    targetMeetingId,
                    "冲突改期",
                    102,
                    "2026-09-10T10:00:00+08:00",
                    "2026-09-10T11:00:00+08:00",
                    0),
                identity("run_reschedule_conflict", "tool_reschedule_conflict_draft"),
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andReturn();
    String token = data(draft).get("confirmationToken").asText();
    assertFormalMeetingState(targetMeetingId, 101, "CONFIRMED", 0, 2, 2, 2);

    performTool(
            "/internal/v1/tools/reschedule-drafts/" + token + "/confirm",
            null,
            identity("run_reschedule_conflict", "tool_reschedule_conflict_confirm"),
            SERVICE_TOKEN,
            true,
            "mutation-reschedule-conflict-key")
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("BOOKING_CONFLICT"))
        .andExpect(jsonPath("$.details[0].field").value("conflict.type"))
        .andExpect(jsonPath("$.details[1].reason").value("102"))
        .andExpect(jsonPath("$.details[2].reason").value("20,21"));
    assertFormalMeetingState(targetMeetingId, 101, "CONFIRMED", 0, 2, 2, 2);
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
    return createHotPending(
        date, prefix, prefix.contains("conflict") ? 1 : 0, "T10:00:00+08:00", "T11:00:00+08:00");
  }

  private PendingBooking createHotPending(
      String date, String prefix, int expectedExistingMeetings, String startTime, String endTime)
      throws Exception {
    ToolIdentity draftIdentity = identity("run_" + prefix, "tool_" + prefix + "_draft");
    MvcResult draftResult =
        performTool(
                "/internal/v1/tools/booking-drafts",
                meetingBody("热门预约", 103, date + startTime, date + endTime),
                draftIdentity,
                SERVICE_TOKEN,
                true)
            .andExpect(status().isOk())
            .andReturn();
    String confirmationToken = data(draftResult).get("confirmationToken").asText();
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class))
        .isEqualTo(expectedExistingMeetings);

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
            List.of(1002)));
  }

  private String rescheduleBody(
      long meetingId, String title, long roomId, String startAt, String endAt, int expectedVersion)
      throws Exception {
    Map<String, Object> body =
        new LinkedHashMap<>(
            Map.of(
                "meetingId",
                meetingId,
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
                List.of(1002)));
    body.put("expectedVersion", expectedVersion);
    return objectMapper.writeValueAsString(body);
  }

  private void assertFormalMeetingState(
      long meetingId,
      long roomId,
      String status,
      int version,
      int participantCount,
      int roomSlotCount,
      int employeeBusySlotCount) {
    Map<String, Object> meeting =
        jdbcTemplate.queryForMap(
            "SELECT room_id, status, version FROM meeting WHERE id=?", meetingId);
    assertThat(((Number) meeting.get("room_id")).longValue()).isEqualTo(roomId);
    assertThat(meeting.get("status")).isEqualTo(status);
    assertThat(((Number) meeting.get("version")).intValue()).isEqualTo(version);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_participant WHERE meeting_id=?",
                Integer.class,
                meetingId))
        .isEqualTo(participantCount);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_room_slot WHERE meeting_id=?",
                Integer.class,
                meetingId))
        .isEqualTo(roomSlotCount);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM employee_busy_slot WHERE meeting_id=?",
                Integer.class,
                meetingId))
        .isEqualTo(employeeBusySlotCount);
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
