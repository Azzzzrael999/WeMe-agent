package com.example.meeting.replan.api;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.hasItem;
import static org.hamcrest.Matchers.hasSize;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
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
class ReplanCaseIntegrationTest {

  private static final String START = "2026-10-20T14:00:00+08:00";
  private static final String END = "2026-10-20T15:00:00+08:00";

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private JdbcTemplate jdbcTemplate;

  @BeforeEach
  @AfterEach
  void clean() {
    jdbcTemplate.update("DELETE FROM notification");
    jdbcTemplate.update("DELETE FROM message_outbox");
    jdbcTemplate.update("DELETE FROM idempotency_record");
    jdbcTemplate.update("DELETE FROM meeting_replan_case");
    jdbcTemplate.update("DELETE FROM employee_busy_slot");
    jdbcTemplate.update("DELETE FROM meeting_room_slot");
    jdbcTemplate.update("DELETE FROM meeting_participant");
    jdbcTemplate.update("DELETE FROM meeting");
    jdbcTemplate.update(
        "DELETE FROM meeting_room_feature WHERE room_id IN (SELECT id FROM meeting_room WHERE code LIKE 'RP-T-%')");
    jdbcTemplate.update("DELETE FROM meeting_room WHERE code LIKE 'RP-T-%'");
  }

  @Test
  void disablingRoomAtomicallyCreatesOneCaseAndOrganizerNotification() throws Exception {
    Fixture fixture = fixture();
    String admin = login("admin");
    String organizer = login("zhangsan");
    createMeeting(organizer, fixture.failedRoomId(), "资源失效通知", "rp-disable");
    jdbcTemplate.update("DELETE FROM notification");

    disable(admin, fixture.failedRoomId(), 0, "空调漏水", 200)
        .andExpect(jsonPath("$.data.version").value(1));

    long caseId = onlyCaseId();
    assertThat(count("meeting_replan_case")).isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE type='RESOURCE_UNAVAILABLE' AND user_id=1001 AND related_replan_case_id=?",
                Integer.class,
                caseId))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE type='RESOURCE_UNAVAILABLE' AND user_id<>1001",
                Integer.class))
        .isZero();

    disable(admin, fixture.failedRoomId(), 0, "空调漏水", 409)
        .andExpect(jsonPath("$.code").value("ROOM_STATE_CONFLICT"));
    assertThat(count("meeting_replan_case")).isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE type='RESOURCE_UNAVAILABLE'",
                Integer.class))
        .isEqualTo(1);

    mockMvc
        .perform(
            get("/api/v1/notifications")
                .header("Authorization", bearer(organizer))
                .param("type", "RESOURCE_UNAVAILABLE"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.items", hasSize(1)))
        .andExpect(jsonPath("$.data.items[0].relatedReplanCaseId").value(caseId));
  }

  @Test
  void enforcesCaseVisibilityAndFiltersQuickAlternativesByEveryHardConstraint() throws Exception {
    Fixture fixture = fixture();
    String admin = login("admin");
    String organizer = login("zhangsan");
    String participant = login("lisi");
    createMeeting(organizer, fixture.failedRoomId(), "候选过滤", "rp-filter");
    createMeetingWithRequired(admin, fixture.occupiedRoomId(), "候选占用", "rp-occupied", List.of());
    jdbcTemplate.update("DELETE FROM notification");
    disable(admin, fixture.failedRoomId(), 0, "消防检修", 200);
    long caseId = onlyCaseId();

    mockMvc
        .perform(
            get("/api/v1/replan-cases/{caseId}", caseId)
                .header("Authorization", bearer(participant)))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("REPLAN_CASE_NOT_FOUND"));
    mockMvc
        .perform(
            get("/api/v1/replan-cases/{caseId}", caseId).header("Authorization", bearer(admin)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.id").value(caseId));

    mockMvc
        .perform(
            get("/api/v1/replan-cases")
                .header("Authorization", bearer(organizer))
                .param("status", "OPEN"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(1))
        .andExpect(jsonPath("$.data.items[0].failedRoom.id").value(fixture.failedRoomId()));

    mockMvc
        .perform(
            get("/api/v1/replan-cases/{caseId}/alternatives", caseId)
                .header("Authorization", bearer(organizer)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.sameTime").value(true))
        .andExpect(jsonPath("$.data.caseVersion").value(0))
        .andExpect(jsonPath("$.data.meetingVersion").value(0))
        .andExpect(jsonPath("$.data.changedConstraints", hasItem("仅会议室改变")))
        .andExpect(jsonPath("$.data.items[*].roomId", hasItem((int) fixture.validRoomId())))
        .andExpect(
            jsonPath("$.data.items[*].roomId")
                .value(org.hamcrest.Matchers.not(hasItem((int) fixture.capacityRoomId()))))
        .andExpect(
            jsonPath("$.data.items[*].roomId")
                .value(org.hamcrest.Matchers.not(hasItem((int) fixture.featureRoomId()))))
        .andExpect(
            jsonPath("$.data.items[*].roomId")
                .value(org.hamcrest.Matchers.not(hasItem((int) fixture.inactiveRoomId()))))
        .andExpect(
            jsonPath("$.data.items[*].roomId")
                .value(org.hamcrest.Matchers.not(hasItem((int) fixture.occupiedRoomId()))));
  }

  @Test
  void quickResolveUsesBothVersionsAndClosesCaseWithMeetingTransaction() throws Exception {
    Fixture fixture = fixture();
    String admin = login("admin");
    String organizer = login("zhangsan");
    long meetingId = createMeeting(organizer, fixture.failedRoomId(), "快速换房", "rp-resolve");
    jdbcTemplate.update("DELETE FROM notification");
    disable(admin, fixture.failedRoomId(), 0, "供电异常", 200);
    long caseId = onlyCaseId();

    resolve(caseId, fixture.validRoomId(), 9, 0, organizer, 409)
        .andExpect(jsonPath("$.code").value("REPLAN_CASE_STATE_CONFLICT"));
    resolve(caseId, fixture.featureRoomId(), 0, 0, organizer, 409)
        .andExpect(jsonPath("$.code").value("REPLAN_CANDIDATE_STALE"));

    resolve(caseId, fixture.validRoomId(), 0, 0, organizer, 200)
        .andExpect(jsonPath("$.data.status").value("RESOLVED"))
        .andExpect(jsonPath("$.data.resolutionType").value("QUICK_ROOM_CHANGE"))
        .andExpect(jsonPath("$.data.currentMeeting.roomId").value(fixture.validRoomId()))
        .andExpect(jsonPath("$.data.version").value(1))
        .andExpect(jsonPath("$.data.preservedConstraints", hasItem("会议时间保持不变")));

    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT room_id FROM meeting WHERE id=?", Long.class, meetingId))
        .isEqualTo(fixture.validRoomId());
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_room_slot WHERE meeting_id=? AND room_id=?",
                Integer.class,
                meetingId,
                fixture.validRoomId()))
        .isEqualTo(2);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE related_meeting_id=? AND type='MEETING_CHANGED'",
                Integer.class,
                meetingId))
        .isEqualTo(2);

    resolve(caseId, fixture.validRoomId(), 0, 0, organizer, 409)
        .andExpect(jsonPath("$.code").value("REPLAN_CASE_STATE_CONFLICT"));
  }

  @Test
  void restoreAndCancellationReachTheirOwnTerminalStates() throws Exception {
    Fixture fixture = fixture();
    String admin = login("admin");
    String organizer = login("zhangsan");
    long restoredMeeting = createMeeting(organizer, fixture.failedRoomId(), "恢复会议", "rp-restore");
    jdbcTemplate.update("DELETE FROM notification");
    disable(admin, fixture.failedRoomId(), 0, "临时断电", 200);
    long restoredCase = onlyCaseId();

    enable(admin, fixture.failedRoomId(), 1).andExpect(jsonPath("$.data.status").value("ACTIVE"));
    assertThat(caseStatus(restoredCase)).isEqualTo("RESTORED");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE type='RESOURCE_RESTORED' AND user_id=1001 AND related_replan_case_id=?",
                Integer.class,
                restoredCase))
        .isEqualTo(1);

    long cancelledMeeting =
        createMeeting(
            organizer,
            fixture.failedRoomId(),
            "取消会议",
            "rp-cancel",
            "2026-10-20T16:00:00+08:00",
            "2026-10-20T17:00:00+08:00");
    disable(admin, fixture.failedRoomId(), 2, "再次断电", 200);
    long cancelledCase =
        jdbcTemplate.queryForObject(
            "SELECT id FROM meeting_replan_case WHERE meeting_id=?", Long.class, cancelledMeeting);
    mockMvc
        .perform(
            delete("/api/v1/meetings/{meetingId}", cancelledMeeting)
                .header("Authorization", bearer(organizer)))
        .andExpect(status().isOk());
    assertThat(caseStatus(cancelledCase)).isEqualTo("CANCELLED");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT resolution_type FROM meeting_replan_case WHERE id=?",
                String.class,
                cancelledCase))
        .isEqualTo("MEETING_CANCELLED");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT room_id FROM meeting WHERE id=?", Long.class, restoredMeeting))
        .isEqualTo(fixture.failedRoomId());
  }

  @Test
  void ordinaryMeetingUpdateAlsoResolvesOpenCase() throws Exception {
    Fixture fixture = fixture();
    String admin = login("admin");
    String organizer = login("zhangsan");
    long meetingId = createMeeting(organizer, fixture.failedRoomId(), "普通改期", "rp-update");
    disable(admin, fixture.failedRoomId(), 0, "管道检修", 200);
    long caseId = onlyCaseId();

    mockMvc
        .perform(
            put("/api/v1/meetings/{meetingId}", meetingId)
                .header("Authorization", bearer(organizer))
                .contentType(MediaType.APPLICATION_JSON)
                .content(meetingBody("普通改期", fixture.validRoomId(), START, END, List.of(1003L), 0)))
        .andExpect(status().isOk());

    assertThat(caseStatus(caseId)).isEqualTo("RESOLVED");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT resolution_type FROM meeting_replan_case WHERE id=?", String.class, caseId))
        .isEqualTo("AGENT_RESCHEDULE");
  }

  @Test
  void inactiveReasonIsRequiredWithoutChangingRoomOrCreatingCases() throws Exception {
    Fixture fixture = fixture();
    String admin = login("admin");
    disable(admin, fixture.failedRoomId(), 0, " ", 400)
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
        .andExpect(jsonPath("$.details[0].field").value("reason"));
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT status FROM meeting_room WHERE id=?", String.class, fixture.failedRoomId()))
        .isEqualTo("ACTIVE");
    assertThat(count("meeting_replan_case")).isZero();
  }

  private Fixture fixture() {
    long failed = room("RP-T-FAIL", 8, "ACTIVE", List.of(1L, 2L));
    long valid = room("RP-T-VALID", 8, "ACTIVE", List.of(1L, 2L, 4L));
    long capacity = room("RP-T-SMALL", 1, "ACTIVE", List.of(1L, 2L));
    long feature = room("RP-T-FEATURE", 8, "ACTIVE", List.of(1L));
    long inactive = room("RP-T-INACTIVE", 8, "INACTIVE", List.of(1L, 2L));
    long occupied = room("RP-T-OCCUPIED", 8, "ACTIVE", List.of(1L, 2L));
    return new Fixture(failed, valid, capacity, feature, inactive, occupied);
  }

  private long room(String code, int capacity, String status, List<Long> featureIds) {
    jdbcTemplate.update(
        """
        INSERT INTO meeting_room (
          code,name,building,floor,capacity,room_type,is_hot,status,version
        ) VALUES (?,?, '研发楼','9F',?,'STANDARD',FALSE,?,0)
        """,
        code,
        code,
        capacity,
        status);
    long roomId =
        jdbcTemplate.queryForObject("SELECT id FROM meeting_room WHERE code=?", Long.class, code);
    for (Long featureId : featureIds) {
      jdbcTemplate.update(
          "INSERT INTO meeting_room_feature(room_id,feature_id) VALUES (?,?)", roomId, featureId);
    }
    return roomId;
  }

  private long createMeeting(String token, long roomId, String title, String key) throws Exception {
    return createMeeting(token, roomId, title, key, START, END);
  }

  private long createMeeting(
      String token, long roomId, String title, String key, String startAt, String endAt)
      throws Exception {
    return createMeetingWithRequired(token, roomId, title, key, startAt, endAt, List.of(1003L));
  }

  private long createMeetingWithRequired(
      String token, long roomId, String title, String key, List<Long> required) throws Exception {
    return createMeetingWithRequired(token, roomId, title, key, START, END, required);
  }

  private long createMeetingWithRequired(
      String token,
      long roomId,
      String title,
      String key,
      String startAt,
      String endAt,
      List<Long> required)
      throws Exception {
    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/meetings")
                    .header("Authorization", bearer(token))
                    .header("Idempotency-Key", key + "-" + UUID.randomUUID())
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(meetingBody(title, roomId, startAt, endAt, required, null)))
            .andExpect(status().isOk())
            .andReturn();
    return data(result).get("id").longValue();
  }

  private org.springframework.test.web.servlet.ResultActions disable(
      String token, long roomId, int version, String reason, int expectedStatus) throws Exception {
    return mockMvc
        .perform(
            patch("/api/v1/admin/rooms/{roomId}/status", roomId)
                .header("Authorization", bearer(token))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    objectMapper.writeValueAsString(
                        java.util.Map.of(
                            "status", "INACTIVE",
                            "expectedVersion", version,
                            "reason", reason))))
        .andExpect(status().is(expectedStatus));
  }

  private org.springframework.test.web.servlet.ResultActions enable(
      String token, long roomId, int version) throws Exception {
    return mockMvc
        .perform(
            patch("/api/v1/admin/rooms/{roomId}/status", roomId)
                .header("Authorization", bearer(token))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    objectMapper.writeValueAsString(
                        java.util.Map.of("status", "ACTIVE", "expectedVersion", version))))
        .andExpect(status().isOk());
  }

  private org.springframework.test.web.servlet.ResultActions resolve(
      long caseId,
      long roomId,
      int meetingVersion,
      int caseVersion,
      String token,
      int expectedStatus)
      throws Exception {
    return mockMvc
        .perform(
            post("/api/v1/replan-cases/{caseId}/resolve", caseId)
                .header("Authorization", bearer(token))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    """
                    {"roomId":%d,"expectedMeetingVersion":%d,"expectedCaseVersion":%d}
                    """
                        .formatted(roomId, meetingVersion, caseVersion)))
        .andExpect(status().is(expectedStatus));
  }

  private String meetingBody(
      String title,
      long roomId,
      String startAt,
      String endAt,
      List<Long> required,
      Integer expectedVersion)
      throws Exception {
    java.util.Map<String, Object> body = new java.util.LinkedHashMap<>();
    body.put("title", title);
    body.put("meetingType", "INTERNAL");
    body.put("roomId", roomId);
    body.put("startAt", startAt);
    body.put("endAt", endAt);
    body.put("requiredParticipantIds", required);
    body.put("optionalParticipantIds", List.of());
    if (expectedVersion != null) {
      body.put("expectedVersion", expectedVersion);
    }
    return objectMapper.writeValueAsString(body);
  }

  private long onlyCaseId() {
    return jdbcTemplate.queryForObject("SELECT id FROM meeting_replan_case", Long.class);
  }

  private String caseStatus(long caseId) {
    return jdbcTemplate.queryForObject(
        "SELECT status FROM meeting_replan_case WHERE id=?", String.class, caseId);
  }

  private int count(String table) {
    return jdbcTemplate.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class);
  }

  private String login(String username) throws Exception {
    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/auth/login")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(
                        """
                        {"username":"%s","password":"demo-password"}
                        """
                            .formatted(username)))
            .andExpect(status().isOk())
            .andReturn();
    return data(result).get("accessToken").asText();
  }

  private JsonNode data(MvcResult result) throws Exception {
    return objectMapper
        .readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8))
        .get("data");
  }

  private String bearer(String token) {
    return "Bearer " + token;
  }

  private record Fixture(
      long failedRoomId,
      long validRoomId,
      long capacityRoomId,
      long featureRoomId,
      long inactiveRoomId,
      long occupiedRoomId) {}
}
