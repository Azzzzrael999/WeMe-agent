package com.example.meeting.booking;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
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
class MeetingConcurrencyIntegrationTest {

  private static final int REQUEST_COUNT = 100;

  @Autowired private MockMvc mockMvc;
  @Autowired private ObjectMapper objectMapper;
  @Autowired private JdbcTemplate jdbcTemplate;

  @BeforeEach
  void cleanBookings() {
    jdbcTemplate.update("DELETE FROM idempotency_record");
    jdbcTemplate.update("DELETE FROM employee_busy_slot");
    jdbcTemplate.update("DELETE FROM meeting_room_slot");
    jdbcTemplate.update("DELETE FROM meeting_participant");
    jdbcTemplate.update("DELETE FROM meeting");
  }

  @Test
  @Timeout(60)
  void oneHundredRequestsForSameRoomAndSlotsLeaveOneConfirmedMeeting() throws Exception {
    String token = login();
    String body = meetingBody("并发房间竞争", "2026-08-25T15:00:00+08:00", "2026-08-25T16:00:00+08:00");

    List<Attempt> attempts = runConcurrently(index -> request(token, "room-race-" + index, body));

    assertThat(attempts).filteredOn(attempt -> attempt.status() == 200).hasSize(1);
    assertThat(attempts).filteredOn(attempt -> attempt.status() == 409).hasSize(99);
    assertThat(attempts.stream().filter(attempt -> attempt.status() == 409).map(Attempt::code))
        .containsOnly("BOOKING_CONFLICT");
    assertFinalDatabaseState(1, 2, 2, 1);
    Integer duplicateRoomSlots =
        jdbcTemplate.queryForObject(
            """
                        SELECT COUNT(*) FROM (
                          SELECT room_id, booking_date, slot_index
                          FROM meeting_room_slot
                          GROUP BY room_id, booking_date, slot_index
                          HAVING COUNT(*) > 1
                        ) duplicate_slots
                        """,
            Integer.class);
    assertThat(duplicateRoomSlots).isZero();
  }

  @Test
  @Timeout(60)
  void oneHundredConcurrentIdempotentRequestsAllReturnSameMeeting() throws Exception {
    String token = login();
    String body = meetingBody("并发幂等", "2026-08-26T10:00:00+08:00", "2026-08-26T11:00:00+08:00");

    List<Attempt> attempts = runConcurrently(index -> request(token, "same-idempotency-key", body));

    assertThat(attempts).allMatch(attempt -> attempt.status() == 200);
    Set<Long> meetingIds =
        attempts.stream().map(Attempt::meetingId).collect(java.util.stream.Collectors.toSet());
    assertThat(meetingIds).hasSize(1).doesNotContain(0L);
    assertFinalDatabaseState(1, 2, 2, 1);
  }

  private List<Attempt> runConcurrently(IndexedAttempt operation) throws Exception {
    ExecutorService executor = Executors.newFixedThreadPool(24);
    CountDownLatch start = new CountDownLatch(1);
    List<Future<Attempt>> futures = new ArrayList<>();
    try {
      for (int index = 0; index < REQUEST_COUNT; index++) {
        int requestIndex = index;
        futures.add(
            executor.submit(
                () -> {
                  start.await();
                  return operation.execute(requestIndex);
                }));
      }
      start.countDown();
      List<Attempt> attempts = new ArrayList<>(REQUEST_COUNT);
      for (Future<Attempt> future : futures) {
        attempts.add(future.get());
      }
      return attempts;
    } finally {
      executor.shutdownNow();
      assertThat(executor.awaitTermination(5, TimeUnit.SECONDS)).isTrue();
    }
  }

  private Attempt request(String token, String idempotencyKey, String body) throws Exception {
    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/meetings")
                    .header("Authorization", "Bearer " + token)
                    .header("Idempotency-Key", idempotencyKey)
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(body))
            .andReturn();
    int status = result.getResponse().getStatus();
    JsonNode envelope =
        objectMapper.readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8));
    long meetingId = status == 200 ? envelope.at("/data/id").asLong() : 0;
    String code = status == 200 ? null : envelope.path("code").asText();
    return new Attempt(status, meetingId, code);
  }

  private void assertFinalDatabaseState(
      int meetings, int roomSlots, int busySlots, int idempotencyRecords) {
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class))
        .isEqualTo(meetings);
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting_room_slot", Integer.class))
        .isEqualTo(roomSlots);
    assertThat(
            jdbcTemplate.queryForObject("SELECT COUNT(*) FROM employee_busy_slot", Integer.class))
        .isEqualTo(busySlots);
    assertThat(
            jdbcTemplate.queryForObject("SELECT COUNT(*) FROM idempotency_record", Integer.class))
        .isEqualTo(idempotencyRecords);
  }

  private String meetingBody(String title, String startAt, String endAt) throws Exception {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("title", title);
    body.put("meetingType", "ARCHITECTURE_REVIEW");
    body.put("roomId", 101);
    body.put("startAt", startAt);
    body.put("endAt", endAt);
    body.put("requiredParticipantIds", List.of());
    body.put("optionalParticipantIds", List.of());
    body.put("createVideoConference", false);
    return objectMapper.writeValueAsString(body);
  }

  private String login() throws Exception {
    MvcResult result =
        mockMvc
            .perform(
                post("/api/v1/auth/login")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content(
                        """
                                                {"username":"zhangsan","password":"demo-password"}
                                                """))
            .andExpect(status().isOk())
            .andReturn();
    JsonNode envelope =
        objectMapper.readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8));
    return envelope.at("/data/accessToken").asText();
  }

  private record Attempt(int status, long meetingId, String code) {}

  @FunctionalInterface
  private interface IndexedAttempt {
    Attempt execute(int index) throws Exception;
  }
}
