package com.example.meeting.meeting.api;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.hasSize;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
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
class MeetingApiIntegrationTest {

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private JdbcTemplate jdbcTemplate;

  @BeforeEach
  void cleanBookings() {
    jdbcTemplate.update("DELETE FROM notification");
    jdbcTemplate.update("DELETE FROM message_outbox");
    jdbcTemplate.update("DELETE FROM idempotency_record");
    jdbcTemplate.update("DELETE FROM employee_busy_slot");
    jdbcTemplate.update("DELETE FROM meeting_room_slot");
    jdbcTemplate.update("DELETE FROM meeting_participant");
    jdbcTemplate.update("DELETE FROM meeting");
    jdbcTemplate.update("UPDATE meeting_room SET status = 'ACTIVE' WHERE code <> 'HQ-MAINT-702'");
  }

  @Test
  void createsNinetyMinuteMeetingAndReplaysSameIdempotencyKey() throws Exception {
    String token = login("zhangsan");
    String body =
        meetingBody(
            "架构评审",
            101,
            "2026-08-19T15:00:00+08:00",
            "2026-08-19T16:30:00+08:00",
            List.of(1002L, 1002L),
            List.of(1001L),
            false);

    MvcResult first = create(token, "create-90-minutes", body, 200);
    MvcResult replay = create(token, "create-90-minutes", body, 200);
    long meetingId = responseData(first).get("id").longValue();

    assertThat(responseData(replay).get("id").longValue()).isEqualTo(meetingId);
    assertThat(responseData(first).get("status").asText()).isEqualTo("CONFIRMED");
    assertThat(responseData(first).get("source").asText()).isEqualTo("MANUAL");
    assertThat(responseData(first).get("startAt").asText()).endsWith("+08:00");
    assertThat(responseData(first).get("participants")).hasSize(2);
    assertThat(responseData(first).at("/participants/0/employeeId").longValue()).isEqualTo(1001);
    assertThat(responseData(first).at("/participants/0/participantType").asText())
        .isEqualTo("REQUIRED");
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting WHERE id = ?", Integer.class, meetingId))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_room_slot WHERE meeting_id = ?",
                Integer.class,
                meetingId))
        .isEqualTo(3);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM employee_busy_slot WHERE meeting_id = ?",
                Integer.class,
                meetingId))
        .isEqualTo(6);
    assertThat(
            jdbcTemplate.queryForList(
                """
                                SELECT slot_index FROM meeting_room_slot
                                WHERE meeting_id = ? ORDER BY slot_index
                                """,
                Short.class,
                meetingId))
        .containsExactly((short) 30, (short) 31, (short) 32);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE related_meeting_id = ?",
                Integer.class,
                meetingId))
        .isEqualTo(2);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM message_outbox WHERE aggregate_id = ? AND event_type = 'MEETING_CONFIRMED'",
                Integer.class,
                Long.toString(meetingId)))
        .isEqualTo(1);
  }

  @Test
  void rejectsReusedKeyWithDifferentPayloadAndInvalidCreateInputs() throws Exception {
    String token = login("zhangsan");
    String valid =
        meetingBody(
            "设计评审",
            101,
            "2026-08-20T09:00:00+08:00",
            "2026-08-20T10:00:00+08:00",
            List.of(),
            List.of(),
            false);
    create(token, "reused-key", valid, 200);

    create(
            token,
            "reused-key",
            meetingBody(
                "不同标题",
                101,
                "2026-08-20T09:00:00+08:00",
                "2026-08-20T10:00:00+08:00",
                List.of(),
                List.of(),
                false),
            409)
        .getResponse();
    mockMvc
        .perform(
            post("/api/v1/meetings")
                .header("Authorization", bearer(token))
                .contentType(MediaType.APPLICATION_JSON)
                .content(valid))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
        .andExpect(jsonPath("$.details[0].field").value("Idempotency-Key"));

    create(
        token,
        "video-not-supported",
        meetingBody(
            "视频会议",
            101,
            "2026-08-20T10:00:00+08:00",
            "2026-08-20T11:00:00+08:00",
            List.of(),
            List.of(),
            true),
        400);
    create(
        token,
        "participant-not-found",
        meetingBody(
            "非法参与者",
            101,
            "2026-08-20T11:00:00+08:00",
            "2026-08-20T12:00:00+08:00",
            List.of(999999L),
            List.of(),
            false),
        400);
    create(
        token,
        "invalid-boundary",
        meetingBody(
            "非法时间",
            101,
            "2026-08-20T12:15:00+08:00",
            "2026-08-20T13:00:00+08:00",
            List.of(),
            List.of(),
            false),
        400);
  }

  @Test
  void mapsRoomAndRequiredEmployeeUniqueConstraintsToBookingConflict() throws Exception {
    String employeeToken = login("zhangsan");
    String adminToken = login("admin");
    create(
        employeeToken,
        "room-owner",
        meetingBody(
            "房间占用",
            101,
            "2026-08-21T09:00:00+08:00",
            "2026-08-21T10:00:00+08:00",
            List.of(),
            List.of(),
            false),
        200);

    MvcResult roomConflict =
        create(
            adminToken,
            "room-conflict",
            meetingBody(
                "冲突会议",
                101,
                "2026-08-21T09:30:00+08:00",
                "2026-08-21T10:30:00+08:00",
                List.of(),
                List.of(),
                false),
            409);
    assertThat(errorCode(roomConflict)).isEqualTo("BOOKING_CONFLICT");

    create(
        adminToken,
        "employee-owner",
        meetingBody(
            "员工占用",
            102,
            "2026-08-21T13:00:00+08:00",
            "2026-08-21T14:00:00+08:00",
            List.of(1001L),
            List.of(),
            false),
        200);
    MvcResult employeeConflict =
        create(
            employeeToken,
            "employee-conflict",
            meetingBody(
                "员工冲突",
                103,
                "2026-08-21T13:00:00+08:00",
                "2026-08-21T14:00:00+08:00",
                List.of(),
                List.of(),
                false),
            409);
    assertThat(errorCode(employeeConflict)).isEqualTo("BOOKING_CONFLICT");
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class))
        .isEqualTo(2);
  }

  @Test
  void failedUpdateRollsBackAndSuccessfulUpdateUsesExpectedVersion() throws Exception {
    String employeeToken = login("zhangsan");
    String adminToken = login("admin");
    long firstMeeting =
        responseData(
                create(
                    employeeToken,
                    "update-first",
                    meetingBody(
                        "原会议",
                        101,
                        "2026-08-22T15:00:00+08:00",
                        "2026-08-22T16:00:00+08:00",
                        List.of(),
                        List.of(),
                        false),
                    200))
            .get("id")
            .longValue();
    create(
        adminToken,
        "update-blocker",
        meetingBody(
            "阻塞会议",
            102,
            "2026-08-22T16:00:00+08:00",
            "2026-08-22T17:00:00+08:00",
            List.of(),
            List.of(),
            false),
        200);

    mockMvc
        .perform(
            put("/api/v1/meetings/{id}", firstMeeting)
                .header("Authorization", bearer(employeeToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    updateBody(
                        "冲突修改", 102, "2026-08-22T16:00:00+08:00", "2026-08-22T17:00:00+08:00", 0)))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("BOOKING_CONFLICT"));

    JsonNode unchanged = getMeeting(firstMeeting, employeeToken);
    assertThat(unchanged.get("title").asText()).isEqualTo("原会议");
    assertThat(unchanged.get("roomId").longValue()).isEqualTo(101);
    assertThat(unchanged.get("startAt").asText()).isEqualTo("2026-08-22T15:00:00+08:00");
    assertThat(unchanged.get("version").intValue()).isZero();
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_room_slot WHERE meeting_id = ?",
                Integer.class,
                firstMeeting))
        .isEqualTo(2);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM message_outbox WHERE aggregate_id = ? AND event_type = 'MEETING_CHANGED'",
                Integer.class,
                Long.toString(firstMeeting)))
        .isZero();

    mockMvc
        .perform(
            put("/api/v1/meetings/{id}", firstMeeting)
                .header("Authorization", bearer(employeeToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    updateBody(
                        "成功修改", 103, "2026-08-22T17:00:00+08:00", "2026-08-22T18:30:00+08:00", 0)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.version").value(1))
        .andExpect(jsonPath("$.data.roomId").value(103))
        .andExpect(jsonPath("$.data.title").value("成功修改"));
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_room_slot WHERE meeting_id = ?",
                Integer.class,
                firstMeeting))
        .isEqualTo(3);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM message_outbox WHERE aggregate_id = ? AND event_type = 'MEETING_CHANGED'",
                Integer.class,
                Long.toString(firstMeeting)))
        .isEqualTo(1);

    mockMvc
        .perform(
            put("/api/v1/meetings/{id}", firstMeeting)
                .header("Authorization", bearer(employeeToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    updateBody(
                        "过期版本", 103, "2026-08-22T17:00:00+08:00", "2026-08-22T18:30:00+08:00", 0)))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("MEETING_STATE_CONFLICT"));
  }

  @Test
  void cancellationReleasesSlotsAndRepeatedCancellationIsStableConflict() throws Exception {
    String token = login("zhangsan");
    String body =
        meetingBody(
            "待取消会议",
            101,
            "2026-08-23T10:00:00+08:00",
            "2026-08-23T11:00:00+08:00",
            List.of(),
            List.of(),
            false);
    long meetingId = responseData(create(token, "cancel-first", body, 200)).get("id").longValue();

    mockMvc
        .perform(delete("/api/v1/meetings/{id}", meetingId).header("Authorization", bearer(token)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.status").value("CANCELLED"))
        .andExpect(jsonPath("$.data.version").value(1))
        .andExpect(jsonPath("$.data.cancelledAt").value(org.hamcrest.Matchers.endsWith("+08:00")));
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_room_slot WHERE meeting_id = ?",
                Integer.class,
                meetingId))
        .isZero();
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM employee_busy_slot WHERE meeting_id = ?",
                Integer.class,
                meetingId))
        .isZero();
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM message_outbox WHERE aggregate_id = ? AND event_type = 'MEETING_CANCELLED'",
                Integer.class,
                Long.toString(meetingId)))
        .isEqualTo(1);

    mockMvc
        .perform(delete("/api/v1/meetings/{id}", meetingId).header("Authorization", bearer(token)))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("MEETING_STATE_CONFLICT"));
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM message_outbox WHERE aggregate_id = ? AND event_type = 'MEETING_CANCELLED'",
                Integer.class,
                Long.toString(meetingId)))
        .isEqualTo(1);
    create(token, "cancel-replacement", body, 200);
  }

  @Test
  void participantCanListAndViewButCannotModifyOrganizersMeeting() throws Exception {
    String adminToken = login("admin");
    String employeeToken = login("zhangsan");
    long meetingId =
        responseData(
                create(
                    adminToken,
                    "participant-visible",
                    meetingBody(
                        "可见会议",
                        102,
                        "2026-08-24T09:00:00+08:00",
                        "2026-08-24T10:00:00+08:00",
                        List.of(),
                        List.of(1001L),
                        false),
                    200))
            .get("id")
            .longValue();

    mockMvc
        .perform(
            get("/api/v1/meetings")
                .header("Authorization", bearer(employeeToken))
                .param("from", "2026-08-24T00:00:00+08:00")
                .param("to", "2026-08-25T00:00:00+08:00")
                .param("status", "CONFIRMED"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(1))
        .andExpect(jsonPath("$.data.items", hasSize(1)))
        .andExpect(jsonPath("$.data.items[0].id").value(meetingId));
    mockMvc
        .perform(
            get("/api/v1/meetings/{id}", meetingId).header("Authorization", bearer(employeeToken)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.organizerId").value(1002));
    mockMvc
        .perform(
            put("/api/v1/meetings/{id}", meetingId)
                .header("Authorization", bearer(employeeToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    updateBody(
                        "越权修改", 102, "2026-08-24T09:00:00+08:00", "2026-08-24T10:00:00+08:00", 0)))
        .andExpect(status().isForbidden())
        .andExpect(jsonPath("$.code").value("FORBIDDEN"));

    mockMvc
        .perform(
            get("/api/v1/meetings")
                .header("Authorization", bearer(employeeToken))
                .param("from", "not-a-time"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));
    mockMvc
        .perform(
            get("/api/v1/meetings")
                .header("Authorization", bearer(employeeToken))
                .param("status", "PENDING"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));
    mockMvc.perform(get("/api/v1/meetings")).andExpect(status().isUnauthorized());
  }

  @Test
  void paginatesInDatabaseAndReturnsFullFilteredTotal() throws Exception {
    String token = login("zhangsan");
    insertVisibleMeetings(25);

    mockMvc
        .perform(get("/api/v1/meetings").header("Authorization", bearer(token)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(25))
        .andExpect(jsonPath("$.data.items", hasSize(20)));

    mockMvc
        .perform(
            get("/api/v1/meetings")
                .header("Authorization", bearer(token))
                .param("page", "3")
                .param("size", "10"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(25))
        .andExpect(jsonPath("$.data.items", hasSize(5)));
  }

  @Test
  void validatesPaginationAndFourteenDayQueryWindow() throws Exception {
    String token = login("zhangsan");

    assertInvalidListParameter(token, "page", "0");
    assertInvalidListParameter(token, "page", "not-a-number");
    assertInvalidListParameter(token, "size", "0");
    assertInvalidListParameter(token, "size", "101");

    mockMvc
        .perform(
            get("/api/v1/meetings")
                .header("Authorization", bearer(token))
                .param("from", "2026-08-01T00:00:00+08:00")
                .param("to", "2026-08-15T00:00:00+08:00"))
        .andExpect(status().isOk());

    mockMvc
        .perform(
            get("/api/v1/meetings")
                .header("Authorization", bearer(token))
                .param("from", "2026-08-01T00:00:00+08:00")
                .param("to", "2026-08-15T00:00:01+08:00"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
        .andExpect(jsonPath("$.details[0].field").value("to"))
        .andExpect(jsonPath("$.details[0].reason").value("QUERY_WINDOW_TOO_LARGE"));
  }

  private void insertVisibleMeetings(int count) {
    LocalDateTime firstStart = LocalDateTime.of(2026, 8, 27, 8, 0);
    for (int index = 0; index < count; index++) {
      LocalDateTime startAt = firstStart.plusMinutes(index * 30L);
      jdbcTemplate.update(
          """
          INSERT INTO meeting (
              meeting_no, title, meeting_type, organizer_id, room_id,
              start_at, end_at, status, source, version, created_at, updated_at
          ) VALUES (?, ?, 'ARCHITECTURE_REVIEW', 1001, 101, ?, ?, 'CONFIRMED', 'MANUAL', 0, ?, ?)
          """,
          "MTG-PAGE-%03d".formatted(index),
          "分页会议-%03d".formatted(index),
          startAt,
          startAt.plusMinutes(30),
          startAt,
          startAt);
    }
  }

  private void assertInvalidListParameter(String token, String field, String value)
      throws Exception {
    mockMvc
        .perform(get("/api/v1/meetings").header("Authorization", bearer(token)).param(field, value))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
        .andExpect(jsonPath("$.details[0].field").value(field));
  }

  private String meetingBody(
      String title,
      long roomId,
      String startAt,
      String endAt,
      List<Long> required,
      List<Long> optional,
      boolean createVideoConference)
      throws Exception {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("title", title);
    body.put("meetingType", "ARCHITECTURE_REVIEW");
    body.put("roomId", roomId);
    body.put("startAt", startAt);
    body.put("endAt", endAt);
    body.put("requiredParticipantIds", required);
    body.put("optionalParticipantIds", optional);
    body.put("createVideoConference", createVideoConference);
    return objectMapper.writeValueAsString(body);
  }

  private String updateBody(
      String title, long roomId, String startAt, String endAt, int expectedVersion)
      throws Exception {
    Map<String, Object> body =
        objectMapper.readValue(
            meetingBody(title, roomId, startAt, endAt, List.of(), List.of(), false),
            new com.fasterxml.jackson.core.type.TypeReference<>() {});
    body.put("expectedVersion", expectedVersion);
    return objectMapper.writeValueAsString(body);
  }

  private MvcResult create(String token, String idempotencyKey, String body, int expectedStatus)
      throws Exception {
    return mockMvc
        .perform(
            post("/api/v1/meetings")
                .header("Authorization", bearer(token))
                .header("Idempotency-Key", idempotencyKey)
                .contentType(MediaType.APPLICATION_JSON)
                .content(body))
        .andExpect(status().is(expectedStatus))
        .andReturn();
  }

  private JsonNode getMeeting(long meetingId, String token) throws Exception {
    MvcResult result =
        mockMvc
            .perform(get("/api/v1/meetings/{id}", meetingId).header("Authorization", bearer(token)))
            .andExpect(status().isOk())
            .andReturn();
    return responseData(result);
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
    return responseData(result).get("accessToken").asText();
  }

  private JsonNode responseData(MvcResult result) throws Exception {
    String content = result.getResponse().getContentAsString(StandardCharsets.UTF_8);
    return objectMapper.readTree(content).get("data");
  }

  private String errorCode(MvcResult result) throws Exception {
    String content = result.getResponse().getContentAsString(StandardCharsets.UTF_8);
    return objectMapper.readTree(content).get("code").asText();
  }

  private String bearer(String token) {
    return "Bearer " + token;
  }
}
