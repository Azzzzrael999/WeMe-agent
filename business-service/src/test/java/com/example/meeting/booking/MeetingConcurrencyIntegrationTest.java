package com.example.meeting.booking;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.example.meeting.mq.BookingCommandPayload;
import com.example.meeting.mq.BookingCommandProcessor;
import com.example.meeting.outbox.EventEnvelope;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
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
  @Autowired private BookingCommandProcessor bookingCommandProcessor;

  @BeforeEach
  void cleanBookings() {
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
    assertNoDuplicateRoomSlots();
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

  @Test
  @Timeout(60)
  void crossingMultiSlotRequestsRejectOverlapButAllowEndBoundary() throws Exception {
    String token = login();
    String requestA =
        meetingBody(
            "多槽位交叉 A", 101, "2026-08-27T15:00:00+08:00", "2026-08-27T16:30:00+08:00", List.of());
    String requestB =
        meetingBody(
            "多槽位交叉 B", 101, "2026-08-27T15:30:00+08:00", "2026-08-27T16:00:00+08:00", List.of());

    List<Attempt> contenders =
        runConcurrently(
            2,
            index ->
                index == 0
                    ? request(token, "cross-slot-a", requestA)
                    : request(token, "cross-slot-b", requestB));

    assertThat(contenders).filteredOn(attempt -> attempt.status() == 200).hasSize(1);
    assertThat(contenders).filteredOn(attempt -> attempt.status() == 409).hasSize(1);
    assertThat(contenders.stream().filter(attempt -> attempt.status() == 409).map(Attempt::code))
        .containsOnly("BOOKING_CONFLICT");

    Attempt endBoundary =
        request(
            token,
            "cross-slot-c",
            meetingBody(
                "多槽位交叉 C",
                101,
                "2026-08-27T16:30:00+08:00",
                "2026-08-27T17:00:00+08:00",
                List.of()));
    assertThat(endBoundary.status()).isEqualTo(200);
    assertThat(endBoundary.meetingId()).isPositive();

    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class))
        .isEqualTo(2);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_room_slot WHERE meeting_id = ?",
                Integer.class,
                endBoundary.meetingId()))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_room_slot WHERE booking_date = '2026-08-27' AND slot_index = 33",
                Integer.class))
        .isEqualTo(1);
    assertThat(
            jdbcTemplate.queryForObject("SELECT COUNT(*) FROM idempotency_record", Integer.class))
        .isEqualTo(2);
    assertNoDuplicateRoomSlots();
  }

  @Test
  @Timeout(60)
  void requiredAttendeeRaceAcrossRoomsLeavesOneConfirmedMeeting() throws Exception {
    String token = login();
    List<Attempt> contenders =
        runConcurrently(
            2,
            index ->
                request(
                    token,
                    "required-attendee-race-" + index,
                    meetingBody(
                        "必需参会者竞争 " + index,
                        index == 0 ? 101 : 102,
                        "2026-08-28T13:00:00+08:00",
                        "2026-08-28T14:00:00+08:00",
                        List.of(1002L))));

    assertThat(contenders).filteredOn(attempt -> attempt.status() == 200).hasSize(1);
    assertThat(contenders).filteredOn(attempt -> attempt.status() == 409).hasSize(1);
    assertThat(contenders.stream().filter(attempt -> attempt.status() == 409).map(Attempt::code))
        .containsOnly("BOOKING_CONFLICT");
    assertFinalDatabaseState(1, 2, 4, 1);
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM meeting_participant WHERE participant_type = 'REQUIRED' AND employee_id = 1002",
                Integer.class))
        .isEqualTo(1);
    assertNoDuplicateRequiredEmployeeSlots();
  }

  @Test
  @Timeout(60)
  void pendingHotBookingCommandsRaceToOneSuccessAndTerminalConflicts() throws Exception {
    List<String> events = new ArrayList<>();
    for (int index = 0; index < 5; index++) {
      events.add(insertPendingHotBookingEvent(index));
    }
    assertThat(
            jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM booking_request WHERE status = 'PENDING'", Integer.class))
        .isEqualTo(5);

    runConcurrently(
        events.size(),
        index -> {
          bookingCommandProcessor.process(events.get(index));
          return new Attempt(0, 0, null);
        });

    List<String> statuses =
        jdbcTemplate.queryForList(
            "SELECT status FROM booking_request ORDER BY request_no", String.class);
    assertThat(statuses).hasSize(5).containsOnly("SUCCESS", "CONFLICT");
    assertThat(statuses).filteredOn("SUCCESS"::equals).hasSize(1);
    assertThat(statuses).filteredOn("CONFLICT"::equals).hasSize(4);
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting", Integer.class))
        .isEqualTo(1);
    assertThat(jdbcTemplate.queryForObject("SELECT COUNT(*) FROM meeting_room_slot", Integer.class))
        .isEqualTo(2);
    assertThat(
            jdbcTemplate.queryForObject("SELECT COUNT(*) FROM event_consume_record", Integer.class))
        .isEqualTo(5);
    assertNoDuplicateRoomSlots();
  }

  private List<Attempt> runConcurrently(IndexedAttempt operation) throws Exception {
    return runConcurrently(REQUEST_COUNT, operation);
  }

  private List<Attempt> runConcurrently(int requestCount, IndexedAttempt operation)
      throws Exception {
    ExecutorService executor = Executors.newFixedThreadPool(24);
    CountDownLatch start = new CountDownLatch(1);
    List<Future<Attempt>> futures = new ArrayList<>();
    try {
      for (int index = 0; index < requestCount; index++) {
        int requestIndex = index;
        futures.add(
            executor.submit(
                () -> {
                  start.await();
                  return operation.execute(requestIndex);
                }));
      }
      start.countDown();
      List<Attempt> attempts = new ArrayList<>(requestCount);
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

  private String insertPendingHotBookingEvent(int index) throws Exception {
    String requestNo = "BR-CT05-" + index;
    String traceId = "trace-ct05-" + index;
    String runId = "run-ct05-" + index;
    BookingCommandPayload payload =
        new BookingCommandPayload(
            requestNo,
            1001L,
            "热门竞争 " + index,
            "ARCHITECTURE_REVIEW",
            103,
            OffsetDateTime.parse("2026-08-29T10:00:00+08:00"),
            OffsetDateTime.parse("2026-08-29T11:00:00+08:00"),
            List.of(),
            List.of(),
            false);
    EventEnvelope event =
        new EventEnvelope(
            "event-ct05-" + index,
            "BOOKING_COMMAND",
            "BOOKING_REQUEST",
            requestNo,
            traceId,
            runId,
            OffsetDateTime.parse("2026-08-29T09:00:00+08:00"),
            1,
            objectMapper.valueToTree(payload));
    jdbcTemplate.update(
        """
        INSERT INTO booking_request (
            request_no, user_id, run_id, trace_id, tool_call_id, operation,
            payload_json, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'CREATE', ?, 'PENDING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        requestNo,
        1001L,
        runId,
        traceId,
        "tool-ct05-" + index,
        objectMapper.writeValueAsString(payload));
    return objectMapper.writeValueAsString(event);
  }

  private void assertNoDuplicateRoomSlots() {
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

  private void assertNoDuplicateRequiredEmployeeSlots() {
    Integer duplicateEmployeeSlots =
        jdbcTemplate.queryForObject(
            """
            SELECT COUNT(*) FROM (
              SELECT employee_id, booking_date, slot_index
              FROM employee_busy_slot
              GROUP BY employee_id, booking_date, slot_index
              HAVING COUNT(*) > 1
            ) duplicate_slots
            """,
            Integer.class);
    assertThat(duplicateEmployeeSlots).isZero();
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
    return meetingBody(title, 101, startAt, endAt, List.of());
  }

  private String meetingBody(
      String title, long roomId, String startAt, String endAt, List<Long> requiredParticipantIds)
      throws Exception {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("title", title);
    body.put("meetingType", "ARCHITECTURE_REVIEW");
    body.put("roomId", roomId);
    body.put("startAt", startAt);
    body.put("endAt", endAt);
    body.put("requiredParticipantIds", requiredParticipantIds);
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
