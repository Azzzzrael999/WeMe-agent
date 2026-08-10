package com.example.meeting.meeting.lifecycle;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.example.meeting.common.security.JwtService;
import com.example.meeting.meeting.lifecycle.application.MeetingLifecycleScheduler;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class MeetingLifecycleIntegrationTest {

  private static final ZoneId ZONE = ZoneId.of("Asia/Shanghai");
  private static final AtomicInteger UPSTREAM_STATUS = new AtomicInteger(200);
  private static final HttpServer UPSTREAM = createUpstream();

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private JdbcTemplate jdbcTemplate;
  @Autowired private JwtService jwtService;
  @Autowired private MeetingLifecycleScheduler scheduler;

  @DynamicPropertySource
  static void properties(DynamicPropertyRegistry registry) {
    registry.add(
        "app.agent-service.url", () -> "http://127.0.0.1:" + UPSTREAM.getAddress().getPort());
    registry.add("app.lifecycle.scan-interval-millis", () -> "3600000");
  }

  @BeforeEach
  void setUp() {
    cleanBusinessRows();
    UPSTREAM_STATUS.set(200);
  }

  @AfterEach
  void tearDown() {
    cleanBusinessRows();
  }

  @AfterAll
  static void stopUpstream() {
    UPSTREAM.stop(0);
  }

  @Test
  void savesPreparationAtomicallyWithIndependentVersionAndDynamicChecklist() throws Exception {
    LocalDateTime start = LocalDateTime.now(ZONE).plusDays(2).withSecond(0).withNano(0);
    long meetingId = insertMeeting("PREP", 1001, "CONFIRMED", start, start.plusHours(1));
    insertParticipant(meetingId, 1001, "REQUIRED");
    insertParticipant(meetingId, 1003, "REQUIRED");
    String organizer = token(1001, "zhangsan", "EMPLOYEE");
    String participant = token(1003, "lisi", "EMPLOYEE");

    mockMvc
        .perform(
            get("/api/v1/meetings/{meetingId}/lifecycle", meetingId)
                .header("Authorization", bearer(organizer)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.preparation.version").value(0))
        .andExpect(jsonPath("$.data.preparation.checklist.status").value("NEEDS_ATTENTION"))
        .andExpect(jsonPath("$.data.permissions.canEditPreparation").value(true));

    String request =
        """
        {
          "expectedVersion":0,
          "agendaItems":[{"topic":"确认发布范围","ownerEmployeeId":1001,"plannedMinutes":20}],
          "materials":[{"title":"上线方案 V3","ownerEmployeeId":1003,"required":true,"status":"READY","versionLabel":"v3","note":"已完成评审"}]
        }
        """;
    mockMvc
        .perform(
            put("/api/v1/meetings/{meetingId}/preparation", meetingId)
                .header("Authorization", bearer(organizer))
                .contentType(MediaType.APPLICATION_JSON)
                .content(request))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.preparation.version").value(1))
        .andExpect(jsonPath("$.data.preparation.checklist.status").value("READY"))
        .andExpect(jsonPath("$.data.preparation.agendaItems[0].sequenceNo").value(1))
        .andExpect(jsonPath("$.data.preparation.materials[0].ownerName").value("李四"));

    mockMvc
        .perform(
            put("/api/v1/meetings/{meetingId}/preparation", meetingId)
                .header("Authorization", bearer(participant))
                .contentType(MediaType.APPLICATION_JSON)
                .content(request.replace("\"expectedVersion\":0", "\"expectedVersion\":1")))
        .andExpect(status().isForbidden())
        .andExpect(jsonPath("$.code").value("FORBIDDEN"));

    mockMvc
        .perform(
            put("/api/v1/meetings/{meetingId}/preparation", meetingId)
                .header("Authorization", bearer(organizer))
                .contentType(MediaType.APPLICATION_JSON)
                .content(request))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("MEETING_CONTENT_STATE_CONFLICT"));

    mockMvc
        .perform(
            put("/api/v1/meetings/{meetingId}/preparation", meetingId)
                .header("Authorization", bearer(organizer))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    request
                        .replace("\"expectedVersion\":0", "\"expectedVersion\":1")
                        .replace("\"ownerEmployeeId\":1001", "\"ownerEmployeeId\":1010")))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT topic FROM meeting_agenda_item WHERE meeting_id = ?",
                String.class,
                meetingId))
        .isEqualTo("确认发布范围");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT preparation_version FROM meeting_lifecycle_profile WHERE meeting_id = ?",
                Integer.class,
                meetingId))
        .isEqualTo(1);
  }

  @Test
  void editsThenAcceptsDraftAtomicallyAndEnforcesActionItemPermissions() throws Exception {
    LocalDateTime end = LocalDateTime.now(ZONE).minusHours(2).withSecond(0).withNano(0);
    long meetingId = insertMeeting("POST", 1001, "COMPLETED", end.minusHours(1), end);
    insertParticipant(meetingId, 1001, "REQUIRED");
    insertParticipant(meetingId, 1003, "REQUIRED");
    insertParticipant(meetingId, 1010, "OPTIONAL");
    String organizer = token(1001, "zhangsan", "EMPLOYEE");
    String assignee = token(1003, "lisi", "EMPLOYEE");
    String viewer = token(1010, "wangwu", "EMPLOYEE");

    MvcResult created =
        mockMvc
            .perform(
                post("/api/v1/meetings/{meetingId}/post-meeting-drafts", meetingId)
                    .header("Authorization", bearer(organizer))
                    .header("Idempotency-Key", "post-draft-1")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content("{\"transcript\":\"讨论了发布范围、回滚演练与负责人安排。\"}"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data.postMeeting.draft.status").value("PENDING_REVIEW"))
            .andExpect(jsonPath("$.data.postMeeting.draft.version").value(0))
            .andExpect(jsonPath("$.data.postMeeting.minutes").doesNotExist())
            .andReturn();
    JsonNode createdData = data(created);
    long draftId = createdData.at("/postMeeting/draft/id").asLong();
    assertFormalCounts(meetingId, 0, 0, 0);

    mockMvc
        .perform(
            post(
                    "/api/v1/meetings/{meetingId}/post-meeting-drafts/{draftId}/review",
                    meetingId,
                    draftId)
                .header("Authorization", bearer(organizer))
                .contentType(MediaType.APPLICATION_JSON)
                .content(reviewBody("EDIT", 0, "2030-08-20T10:00:00Z")))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
        .andExpect(jsonPath("$.details[0].reason").value("INVALID_TIME_OFFSET"));
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT version FROM post_meeting_draft WHERE id = ?", Integer.class, draftId))
        .isZero();

    mockMvc
        .perform(
            post(
                    "/api/v1/meetings/{meetingId}/post-meeting-drafts/{draftId}/review",
                    meetingId,
                    draftId)
                .header("Authorization", bearer(organizer))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    reviewBody("EDIT", 0, "2030-08-20T18:00:00+08:00")
                        .replace("确认范围与风险", "摘".repeat(10001))))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
        .andExpect(jsonPath("$.details[0].reason").value("TOO_LONG"));
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT version FROM post_meeting_draft WHERE id = ?", Integer.class, draftId))
        .isZero();

    String dueAt =
        LocalDateTime.now(ZONE)
            .plusDays(3)
            .withSecond(0)
            .withNano(0)
            .atZone(ZONE)
            .toOffsetDateTime()
            .format(DateTimeFormatter.ISO_OFFSET_DATE_TIME);
    String edited = reviewBody("EDIT", 0, dueAt);
    mockMvc
        .perform(
            post(
                    "/api/v1/meetings/{meetingId}/post-meeting-drafts/{draftId}/review",
                    meetingId,
                    draftId)
                .header("Authorization", bearer(organizer))
                .contentType(MediaType.APPLICATION_JSON)
                .content(edited))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.postMeeting.draft.status").value("PENDING_REVIEW"))
        .andExpect(jsonPath("$.data.postMeeting.draft.version").value(1))
        .andExpect(jsonPath("$.data.postMeeting.draft.content.minutes.conclusion").value("按计划发布"));
    assertFormalCounts(meetingId, 0, 0, 0);

    MvcResult accepted =
        mockMvc
            .perform(
                post(
                        "/api/v1/meetings/{meetingId}/post-meeting-drafts/{draftId}/review",
                        meetingId,
                        draftId)
                    .header("Authorization", bearer(organizer))
                    .contentType(MediaType.APPLICATION_JSON)
                    .content("{\"action\":\"ACCEPT\",\"expectedVersion\":1}"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data.postMeeting.draft.status").value("ACCEPTED"))
            .andExpect(jsonPath("$.data.postMeeting.minutes.conclusion").value("按计划发布"))
            .andExpect(jsonPath("$.data.postMeeting.actionItems[0].status").value("OPEN"))
            .andReturn();
    assertFormalCounts(meetingId, 1, 1, 1);
    long actionItemId = data(accepted).at("/postMeeting/actionItems/0/id").asLong();

    mockMvc
        .perform(
            post(
                    "/api/v1/meetings/{meetingId}/post-meeting-drafts/{draftId}/review",
                    meetingId,
                    draftId)
                .header("Authorization", bearer(organizer))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"action\":\"ACCEPT\",\"expectedVersion\":1}"))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("POST_MEETING_DRAFT_STATE_CONFLICT"));
    assertFormalCounts(meetingId, 1, 1, 1);

    mockMvc
        .perform(
            patch(
                    "/api/v1/meetings/{meetingId}/action-items/{actionItemId}",
                    meetingId,
                    actionItemId)
                .header("Authorization", bearer(viewer))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"IN_PROGRESS\",\"expectedVersion\":0}"))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("ACTION_ITEM_STATE_CONFLICT"));
    mockMvc
        .perform(
            patch(
                    "/api/v1/meetings/{meetingId}/action-items/{actionItemId}",
                    meetingId,
                    actionItemId)
                .header("Authorization", bearer(assignee))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"IN_PROGRESS\",\"expectedVersion\":0}"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.status").value("IN_PROGRESS"))
        .andExpect(jsonPath("$.data.version").value(1));
  }

  @Test
  void agentFailureLeavesFailedDraftAndNoFormalRecords() throws Exception {
    LocalDateTime end = LocalDateTime.now(ZONE).minusHours(1).withSecond(0).withNano(0);
    long meetingId = insertMeeting("FAIL", 1001, "COMPLETED", end.minusMinutes(30), end);
    insertParticipant(meetingId, 1001, "REQUIRED");
    UPSTREAM_STATUS.set(503);

    mockMvc
        .perform(
            post("/api/v1/meetings/{meetingId}/post-meeting-drafts", meetingId)
                .header("Authorization", bearer(token(1001, "zhangsan", "EMPLOYEE")))
                .header("Idempotency-Key", "post-draft-failure")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"transcript\":\"虚构的会议记录。\"}"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.postMeeting.draft.status").value("FAILED"))
        .andExpect(jsonPath("$.data.postMeeting.draft.errorCode").value("AGENT_UNAVAILABLE"));
    assertFormalCounts(meetingId, 0, 0, 0);
  }

  @Test
  void scheduledScanCompletesMeetingsAndDeduplicatesAllReminderTypes() {
    LocalDateTime now = LocalDateTime.now(ZONE).withSecond(0).withNano(0);
    long finished =
        insertMeeting("ENDED", 1001, "CONFIRMED", now.minusHours(1), now.minusMinutes(1));
    insertParticipant(finished, 1001, "REQUIRED");
    long upcoming =
        insertMeeting("UPCOMING", 1001, "CONFIRMED", now.plusMinutes(20), now.plusMinutes(50));
    insertParticipant(upcoming, 1001, "REQUIRED");
    insertParticipant(upcoming, 1003, "REQUIRED");
    long completed =
        insertMeeting("ACTIONS", 1001, "COMPLETED", now.minusHours(3), now.minusHours(2));
    insertParticipant(completed, 1001, "REQUIRED");
    insertParticipant(completed, 1003, "REQUIRED");
    insertAction(completed, 1, "临期任务", 1003, now.plusHours(1));
    insertAction(completed, 2, "逾期任务", 1003, now.minusHours(1));

    scheduler.scanNow();
    scheduler.scanNow();

    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM meeting WHERE id = ?", String.class, finished))
        .isEqualTo("COMPLETED");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE related_meeting_id = ? AND type = 'MEETING_REMINDER_24H'",
                Integer.class,
                upcoming))
        .isEqualTo(2);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE related_meeting_id = ? AND type = 'MEETING_REMINDER_30M'",
                Integer.class,
                upcoming))
        .isEqualTo(2);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE related_meeting_id = ? AND type = 'PREPARATION_MISSING'",
                Integer.class,
                upcoming))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE related_meeting_id = ? AND type = 'ACTION_ITEM_DUE_SOON'",
                Integer.class,
                completed))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE related_meeting_id = ? AND type = 'ACTION_ITEM_OVERDUE'",
                Integer.class,
                completed))
        .isEqualTo(1);
  }

  private String reviewBody(String action, int version, String dueAt) {
    return """
        {
          "action":"%s",
          "expectedVersion":%d,
          "editedDraft":{
            "minutes":{"background":"发布评审","discussionSummary":"确认范围与风险","conclusion":"按计划发布"},
            "decisions":[{"content":"按计划发布","rationale":"风险可控"}],
            "actionItems":[{"title":"补充回滚演练","description":"完善演练记录","assigneeEmployeeId":1003,"dueAt":"%s"}]
          }
        }
        """
        .formatted(action, version, dueAt);
  }

  private void assertFormalCounts(long meetingId, int minutes, int decisions, int actions) {
    assertThat(count("meeting_minutes", meetingId)).isEqualTo(minutes);
    assertThat(count("meeting_decision", meetingId)).isEqualTo(decisions);
    assertThat(count("meeting_action_item", meetingId)).isEqualTo(actions);
  }

  private int count(String table, long meetingId) {
    return jdbcTemplate.queryForObject(
        "SELECT COUNT(*) FROM " + table + " WHERE meeting_id = ?", Integer.class, meetingId);
  }

  private long insertMeeting(
      String suffix, long organizerId, String status, LocalDateTime startAt, LocalDateTime endAt) {
    jdbcTemplate.update(
        """
        INSERT INTO meeting (
            meeting_no, title, meeting_type, organizer_id, room_id, start_at, end_at,
            status, source, version, created_at, updated_at
        ) VALUES (?, ?, 'ARCHITECTURE_REVIEW', ?, 101, ?, ?, ?, 'MANUAL', 0, ?, ?)
        """,
        "MTG-LIFECYCLE-" + suffix,
        "生命周期会议-" + suffix,
        organizerId,
        startAt,
        endAt,
        status,
        startAt.minusHours(1),
        startAt.minusHours(1));
    return jdbcTemplate.queryForObject(
        "SELECT id FROM meeting WHERE meeting_no = ?", Long.class, "MTG-LIFECYCLE-" + suffix);
  }

  private void insertParticipant(long meetingId, long employeeId, String type) {
    jdbcTemplate.update(
        "INSERT INTO meeting_participant (meeting_id, employee_id, participant_type) VALUES (?, ?, ?)",
        meetingId,
        employeeId,
        type);
  }

  private void insertAction(
      long meetingId, int sequence, String title, long assigneeId, LocalDateTime dueAt) {
    LocalDateTime now = LocalDateTime.now(ZONE);
    jdbcTemplate.update(
        """
        INSERT INTO meeting_action_item (
            meeting_id, sequence_no, title, description, assignee_employee_id, due_at,
            status, version, completed_at, created_at, updated_at
        ) VALUES (?, ?, ?, NULL, ?, ?, 'OPEN', 0, NULL, ?, ?)
        """,
        meetingId,
        sequence,
        title,
        assigneeId,
        dueAt,
        now,
        now);
  }

  private JsonNode data(MvcResult result) throws Exception {
    return objectMapper
        .readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8))
        .get("data");
  }

  private String token(long userId, String username, String role) {
    return jwtService.issue(userId, username, java.util.List.of(role));
  }

  private String bearer(String token) {
    return "Bearer " + token;
  }

  private void cleanBusinessRows() {
    jdbcTemplate.update("DELETE FROM action_item_reminder_delivery");
    jdbcTemplate.update("DELETE FROM meeting_action_item");
    jdbcTemplate.update("DELETE FROM meeting_decision");
    jdbcTemplate.update("DELETE FROM meeting_minutes");
    jdbcTemplate.update("DELETE FROM post_meeting_draft");
    jdbcTemplate.update("DELETE FROM meeting_reminder_delivery");
    jdbcTemplate.update("DELETE FROM meeting_material");
    jdbcTemplate.update("DELETE FROM meeting_agenda_item");
    jdbcTemplate.update("DELETE FROM meeting_lifecycle_profile");
    jdbcTemplate.update("DELETE FROM notification");
    jdbcTemplate.update("DELETE FROM message_outbox");
    jdbcTemplate.update("DELETE FROM idempotency_record");
    jdbcTemplate.update("DELETE FROM employee_busy_slot");
    jdbcTemplate.update("DELETE FROM meeting_room_slot");
    jdbcTemplate.update("DELETE FROM meeting_replan_case");
    jdbcTemplate.update("DELETE FROM meeting_participant");
    jdbcTemplate.update("DELETE FROM meeting");
    jdbcTemplate.update("UPDATE meeting_room SET status = 'ACTIVE' WHERE code <> 'HQ-MAINT-702'");
  }

  private static HttpServer createUpstream() {
    try {
      HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
      server.createContext(
          "/internal/v1/post-meeting/drafts", MeetingLifecycleIntegrationTest::respond);
      server.start();
      return server;
    } catch (IOException exception) {
      throw new ExceptionInInitializerError(exception);
    }
  }

  private static void respond(HttpExchange exchange) throws IOException {
    exchange.getRequestBody().readAllBytes();
    int status = UPSTREAM_STATUS.get();
    String runId = exchange.getRequestHeaders().getFirst("X-Run-Id");
    byte[] body =
        (status == 200
                ? """
                {"agentRunId":"%s","model":"fixture","promptVersion":"post-meeting-analysis-v1","schemaVersion":"post-meeting-draft-v1","draft":{"minutes":{"background":"发布评审","discussionSummary":"确认范围与风险","conclusion":"按计划发布"},"decisions":[{"content":"按计划发布","rationale":null}],"actionItems":[{"title":"补充回滚演练","description":null,"assigneeEmployeeId":1003,"dueAt":"2030-08-20T18:00:00+08:00"}]}}
                """
                    .formatted(runId)
                : "{\"code\":\"UPSTREAM_UNAVAILABLE\"}")
            .getBytes(StandardCharsets.UTF_8);
    exchange.getResponseHeaders().set("Content-Type", MediaType.APPLICATION_JSON_VALUE);
    exchange.sendResponseHeaders(status, body.length);
    exchange.getResponseBody().write(body);
    exchange.close();
  }
}
