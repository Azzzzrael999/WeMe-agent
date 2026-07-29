package com.example.meeting.room.api;

import static org.hamcrest.Matchers.hasSize;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
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
class RoomManagementIntegrationTest {

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private JdbcTemplate jdbcTemplate;

  @BeforeEach
  @AfterEach
  void cleanDaySixRooms() {
    jdbcTemplate.update(
        """
        DELETE FROM employee_busy_slot
        WHERE meeting_id IN (
            SELECT id FROM meeting
            WHERE room_id IN (SELECT id FROM meeting_room WHERE code LIKE 'D6-%')
        )
        """);
    jdbcTemplate.update(
        """
        DELETE FROM meeting_participant
        WHERE meeting_id IN (
            SELECT id FROM meeting
            WHERE room_id IN (SELECT id FROM meeting_room WHERE code LIKE 'D6-%')
        )
        """);
    jdbcTemplate.update(
        """
        DELETE FROM meeting_room_slot
        WHERE room_id IN (SELECT id FROM meeting_room WHERE code LIKE 'D6-%')
        """);
    jdbcTemplate.update(
        """
        DELETE FROM meeting
        WHERE room_id IN (SELECT id FROM meeting_room WHERE code LIKE 'D6-%')
        """);
    jdbcTemplate.update(
        """
        DELETE FROM meeting_room_feature
        WHERE room_id IN (SELECT id FROM meeting_room WHERE code LIKE 'D6-%')
        """);
    jdbcTemplate.update("DELETE FROM meeting_room WHERE code LIKE 'D6-%'");
  }

  @Test
  void adminCanManageRoomAndEmployeeOnlySeesActiveRooms() throws Exception {
    String adminToken = loginAs("admin");
    String employeeToken = loginAs("zhangsan");
    long roomId = createRoom(adminToken, "D6-MGMT", List.of("WHITEBOARD"));

    mockMvc
        .perform(
            get("/api/v1/rooms/{roomId}", roomId).header("Authorization", bearer(employeeToken)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.code").value("D6-MGMT"))
        .andExpect(jsonPath("$.data.version").value(0));

    mockMvc
        .perform(
            put("/api/v1/admin/rooms/{roomId}", roomId)
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(roomBody("D6-MGMT", "Day 6 更新", 12, true, List.of("LARGE_SCREEN"), 0)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.name").value("Day 6 更新"))
        .andExpect(jsonPath("$.data.capacity").value(12))
        .andExpect(jsonPath("$.data.isHot").value(true))
        .andExpect(jsonPath("$.data.version").value(1))
        .andExpect(jsonPath("$.data.features", hasSize(1)))
        .andExpect(jsonPath("$.data.features[0].code").value("LARGE_SCREEN"));

    mockMvc
        .perform(
            patch("/api/v1/admin/rooms/{roomId}/status", roomId)
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"INACTIVE\",\"expectedVersion\":1}"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.status").value("INACTIVE"))
        .andExpect(jsonPath("$.data.version").value(2));

    mockMvc
        .perform(
            get("/api/v1/rooms/{roomId}", roomId).header("Authorization", bearer(employeeToken)))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("ROOM_NOT_FOUND"));
    mockMvc
        .perform(get("/api/v1/rooms").header("Authorization", bearer(employeeToken)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(3));
    mockMvc
        .perform(get("/api/v1/rooms").header("Authorization", bearer(adminToken)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(4));
    mockMvc
        .perform(get("/api/v1/rooms/{roomId}", roomId).header("Authorization", bearer(adminToken)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.status").value("INACTIVE"));
  }

  @Test
  void availabilityUsesHalfHourExclusiveEndSlotsAndValidatesWindow() throws Exception {
    String adminToken = loginAs("admin");
    String employeeToken = loginAs("zhangsan");
    long roomId = createRoom(adminToken, "D6-AVAIL", List.of());
    insertOccupiedSlots(roomId);

    mockMvc
        .perform(
            get("/api/v1/rooms/{roomId}/availability", roomId)
                .header("Authorization", bearer(employeeToken))
                .param("from", "2026-09-17T13:00:00+08:00")
                .param("to", "2026-09-17T14:30:00+08:00"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.roomId").value(roomId))
        .andExpect(jsonPath("$.data.availableSlots", hasSize(3)))
        .andExpect(jsonPath("$.data.availableSlots[0].available").value(false))
        .andExpect(jsonPath("$.data.availableSlots[1].available").value(false))
        .andExpect(jsonPath("$.data.availableSlots[2].available").value(true))
        .andExpect(jsonPath("$.data.availableSlots[2].endAt").value("2026-09-17T14:30:00+08:00"));

    mockMvc
        .perform(
            get("/api/v1/rooms/{roomId}/availability", roomId)
                .header("Authorization", bearer(employeeToken))
                .param("from", "2026-09-17T13:15:00+08:00")
                .param("to", "2026-09-17T14:00:00+08:00"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
        .andExpect(jsonPath("$.details[0].field").value("from"))
        .andExpect(jsonPath("$.details[0].reason").value("INVALID_SLOT_BOUNDARY"));

    mockMvc
        .perform(
            get("/api/v1/rooms/{roomId}/availability", roomId)
                .header("Authorization", bearer(employeeToken))
                .param("from", "2026-09-01T00:00:00+08:00")
                .param("to", "2026-09-15T00:30:00+08:00"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
        .andExpect(jsonPath("$.details[0].field").value("to"))
        .andExpect(jsonPath("$.details[0].reason").value("QUERY_WINDOW_TOO_LARGE"));
  }

  @Test
  void enforcesAdminRoleAndReturnsNotFoundWithoutLeakingInactiveRooms() throws Exception {
    String adminToken = loginAs("admin");
    String employeeToken = loginAs("zhangsan");
    String body = roomBody("D6-AUTH", "权限测试", 8, false, List.of(), null);

    mockMvc
        .perform(post("/api/v1/admin/rooms").contentType(MediaType.APPLICATION_JSON).content(body))
        .andExpect(status().isUnauthorized())
        .andExpect(jsonPath("$.code").value("AUTH_REQUIRED"));
    mockMvc
        .perform(
            post("/api/v1/admin/rooms")
                .header("Authorization", bearer(employeeToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(body))
        .andExpect(status().isForbidden())
        .andExpect(jsonPath("$.code").value("FORBIDDEN"));
    mockMvc
        .perform(
            get("/api/v1/rooms/{roomId}", 999999L).header("Authorization", bearer(employeeToken)))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("ROOM_NOT_FOUND"));
    mockMvc
        .perform(
            put("/api/v1/admin/rooms/{roomId}", 999999L)
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(roomBody("D6-NOT-FOUND", "不存在", 8, false, List.of(), 0)))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("ROOM_NOT_FOUND"));
  }

  @Test
  void validatesFeaturesAndMapsRoomCodeAndVersionConflicts() throws Exception {
    String adminToken = loginAs("admin");
    long roomId = createRoom(adminToken, "D6-CONFLICT", List.of("WHITEBOARD"));

    mockMvc
        .perform(
            post("/api/v1/admin/rooms")
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(roomBody("D6-CONFLICT", "重复编码", 8, false, List.of(), null)))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("ROOM_CODE_CONFLICT"));
    mockMvc
        .perform(
            post("/api/v1/admin/rooms")
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(roomBody("D6-BAD-FEATURE", "错误设备", 8, false, List.of("UNKNOWN"), null)))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
        .andExpect(jsonPath("$.details[0].field").value("featureCodes"))
        .andExpect(jsonPath("$.details[0].reason").value("UNKNOWN_FEATURE"));
    mockMvc
        .perform(
            put("/api/v1/admin/rooms/{roomId}", roomId)
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content(roomBody("D6-CONFLICT", "过期版本", 8, false, List.of(), 7)))
        .andExpect(status().isConflict())
        .andExpect(jsonPath("$.code").value("ROOM_STATE_CONFLICT"));
    mockMvc
        .perform(
            patch("/api/v1/admin/rooms/{roomId}/status", roomId)
                .header("Authorization", bearer(adminToken))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"DISABLED\",\"expectedVersion\":0}"))
        .andExpect(status().isBadRequest())
        .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));
  }

  private long createRoom(String adminToken, String code, List<String> featureCodes)
      throws Exception {
    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/admin/rooms")
                    .header("Authorization", bearer(adminToken))
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(roomBody(code, "Day 6 " + code, 8, false, featureCodes, null)))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data.version").value(0))
            .andReturn();
    return responseData(result).get("id").longValue();
  }

  private void insertOccupiedSlots(long roomId) {
    LocalDateTime firstSlot = LocalDateTime.of(2026, 9, 17, 13, 0);
    String meetingNo = "D6-AVAIL-MEETING-" + roomId;
    jdbcTemplate.update(
        """
        INSERT INTO meeting (
            meeting_no, title, meeting_type, organizer_id, room_id,
            start_at, end_at, status, source, version, created_at, updated_at
        ) VALUES (?, '可用性占用', 'GENERAL', 1001, ?, ?, ?, 'CONFIRMED', 'MANUAL', 0, ?, ?)
        """,
        meetingNo,
        roomId,
        firstSlot,
        firstSlot.plusHours(1),
        firstSlot,
        firstSlot);
    Long meetingId =
        jdbcTemplate.queryForObject(
            "SELECT id FROM meeting WHERE meeting_no = ?", Long.class, meetingNo);
    jdbcTemplate.update(
        """
        INSERT INTO meeting_room_slot (
            meeting_id, room_id, booking_date, slot_index, start_at, end_at
        ) VALUES (?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?)
        """,
        meetingId,
        roomId,
        firstSlot.toLocalDate(),
        26,
        firstSlot,
        firstSlot.plusMinutes(30),
        meetingId,
        roomId,
        firstSlot.toLocalDate(),
        27,
        firstSlot.plusMinutes(30),
        firstSlot.plusHours(1));
  }

  private String roomBody(
      String code,
      String name,
      int capacity,
      boolean hot,
      List<String> featureCodes,
      Integer expectedVersion)
      throws Exception {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("code", code);
    body.put("name", name);
    body.put("building", "研发楼");
    body.put("floor", "3F");
    body.put("capacity", capacity);
    body.put("roomType", "STANDARD");
    body.put("isHot", hot);
    body.put("featureCodes", featureCodes);
    if (expectedVersion != null) {
      body.put("expectedVersion", expectedVersion);
    }
    return objectMapper.writeValueAsString(body);
  }

  private String loginAs(String username) throws Exception {
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
    return objectMapper
        .readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8))
        .get("data");
  }

  private String bearer(String token) {
    return "Bearer " + token;
  }
}
