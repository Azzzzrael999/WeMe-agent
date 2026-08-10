package com.example.meeting.notification.api;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.example.meeting.notification.NotificationMapper;
import com.example.meeting.notification.NotificationRecord;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.LocalDateTime;
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
class NotificationApiIntegrationTest {

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private NotificationMapper notificationMapper;
  @Autowired private JdbcTemplate jdbcTemplate;

  @BeforeEach
  void clearNotifications() {
    jdbcTemplate.update("DELETE FROM notification");
  }

  @Test
  void notificationsAreIsolatedAndReadOperationsAreIdempotent() throws Exception {
    NotificationRecord first = insert(1001, "MEETING_CONFIRMED", "第一条", null);
    NotificationRecord second = insert(1001, "MEETING_CHANGED", "第二条", null);
    NotificationRecord other = insert(1003, "MEETING_CANCELLED", "他人的消息", null);
    String zhangsan = login("zhangsan");
    String lisi = login("lisi");

    mockMvc
        .perform(
            get("/api/v1/notifications")
                .header("Authorization", bearer(zhangsan))
                .param("unreadOnly", "true"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(2))
        .andExpect(jsonPath("$.data.unreadCount").value(2))
        .andExpect(jsonPath("$.data.items[0].content").value("第二条"));

    mockMvc
        .perform(
            patch("/api/v1/notifications/{id}/read", other.getId())
                .header("Authorization", bearer(zhangsan)))
        .andExpect(status().isNotFound())
        .andExpect(jsonPath("$.code").value("NOTIFICATION_NOT_FOUND"));

    for (int attempt = 0; attempt < 2; attempt++) {
      mockMvc
          .perform(
              patch("/api/v1/notifications/{id}/read", first.getId())
                  .header("Authorization", bearer(zhangsan)))
          .andExpect(status().isOk())
          .andExpect(jsonPath("$.data.readAt").isNotEmpty());
    }
    mockMvc
        .perform(
            get("/api/v1/notifications/unread-count").header("Authorization", bearer(zhangsan)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.unreadCount").value(1));

    mockMvc
        .perform(patch("/api/v1/notifications/read-all").header("Authorization", bearer(zhangsan)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.updatedCount").value(1));
    mockMvc
        .perform(patch("/api/v1/notifications/read-all").header("Authorization", bearer(zhangsan)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.updatedCount").value(0));

    mockMvc
        .perform(get("/api/v1/notifications").header("Authorization", bearer(lisi)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.total").value(1))
        .andExpect(jsonPath("$.data.items[0].id").value(other.getId()))
        .andExpect(jsonPath("$.data.items[0].content").value("他人的消息"));
    assertThat(second.getId()).isNotEqualTo(first.getId());
  }

  @Test
  void meetingLifecycleWritesChineseNotificationsForCorrectRecipientSets() throws Exception {
    String token = login("zhangsan");
    MvcResult created =
        mockMvc
            .perform(
                post("/api/v1/meetings")
                    .header("Authorization", bearer(token))
                    .header("Idempotency-Key", "notification-union-create")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(
                        meetingBody(
                            "通知接收人评审",
                            "2026-09-30T20:00:00+08:00",
                            "2026-09-30T20:30:00+08:00",
                            "[1003]",
                            "[1010]")))
            .andExpect(status().isOk())
            .andReturn();
    long meetingId = data(created).get("id").longValue();

    assertRecipientCount(meetingId, "MEETING_CONFIRMED", 1001, 1);
    assertRecipientCount(meetingId, "MEETING_CONFIRMED", 1003, 1);
    assertRecipientCount(meetingId, "MEETING_CONFIRMED", 1010, 1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT content FROM notification WHERE related_meeting_id=? AND user_id=1001 AND type='MEETING_CONFIRMED'",
                String.class,
                meetingId))
        .isEqualTo("会议“通知接收人评审”已确认。");

    mockMvc
        .perform(
            put("/api/v1/meetings/{id}", meetingId)
                .header("Authorization", bearer(token))
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    updateBody(
                        "通知接收人评审（改期）",
                        "2026-09-30T20:30:00+08:00",
                        "2026-09-30T21:00:00+08:00",
                        "[1010]",
                        "[]",
                        0)))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.data.version").value(1));

    assertRecipientCount(meetingId, "MEETING_CHANGED", 1001, 1);
    assertRecipientCount(meetingId, "MEETING_CHANGED", 1003, 1);
    assertRecipientCount(meetingId, "MEETING_CHANGED", 1010, 1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT content FROM notification WHERE related_meeting_id=? AND user_id=1003 AND type='MEETING_CHANGED'",
                String.class,
                meetingId))
        .isEqualTo("会议“通知接收人评审（改期）”的时间或参会信息已更新。");

    mockMvc
        .perform(delete("/api/v1/meetings/{id}", meetingId).header("Authorization", bearer(token)))
        .andExpect(status().isOk());
    assertRecipientCount(meetingId, "MEETING_CANCELLED", 1001, 1);
    assertRecipientCount(meetingId, "MEETING_CANCELLED", 1010, 1);
    assertRecipientCount(meetingId, "MEETING_CANCELLED", 1003, 0);
  }

  private NotificationRecord insert(long userId, String type, String content, Long meetingId) {
    NotificationRecord record = new NotificationRecord();
    record.setUserId(userId);
    record.setType(type);
    record.setTitle("测试通知");
    record.setContent(content);
    record.setRelatedMeetingId(meetingId);
    record.setCreatedAt(LocalDateTime.now().plusNanos(userId));
    notificationMapper.insert(record);
    return record;
  }

  private void assertRecipientCount(long meetingId, String type, long userId, int expectedCount) {
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM notification WHERE related_meeting_id=? AND type=? AND user_id=?",
                Integer.class,
                meetingId,
                type,
                userId))
        .isEqualTo(expectedCount);
  }

  private String meetingBody(
      String title, String startAt, String endAt, String required, String optional) {
    return """
        {
          "title":"%s","meetingType":"INTERNAL","roomId":117,
          "startAt":"%s","endAt":"%s",
          "requiredParticipantIds":%s,"optionalParticipantIds":%s
        }
        """
        .formatted(title, startAt, endAt, required, optional);
  }

  private String updateBody(
      String title, String startAt, String endAt, String required, String optional, int version) {
    return """
        {
          "title":"%s","meetingType":"INTERNAL","roomId":117,
          "startAt":"%s","endAt":"%s",
          "requiredParticipantIds":%s,"optionalParticipantIds":%s,
          "expectedVersion":%d
        }
        """
        .formatted(title, startAt, endAt, required, optional, version);
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
    return objectMapper.readTree(result.getResponse().getContentAsString()).get("data");
  }

  private String bearer(String token) {
    return "Bearer " + token;
  }
}
