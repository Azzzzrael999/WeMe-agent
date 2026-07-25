package com.example.meeting.booking.application;

import com.example.meeting.booking.domain.IdempotencyRecord;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.LocalDateTime;
import java.util.Optional;
import org.springframework.stereotype.Component;

@Component
public class IdempotencySupport {

  private final ObjectMapper objectMapper;

  public IdempotencySupport(ObjectMapper objectMapper) {
    this.objectMapper = objectMapper;
  }

  public Optional<Long> replay(IdempotencyRecord record, String requestHash, LocalDateTime now) {
    if (!record.getExpiresAt().isAfter(now)) {
      return Optional.empty();
    }
    assertSameRequest(record, requestHash);
    if (!"SUCCEEDED".equals(record.getStatus())) {
      throw new BusinessException(ErrorCode.DEPENDENCY_UNAVAILABLE, "相同请求仍在处理中，请稍后重试");
    }
    return Optional.of(parseMeetingId(record.getResponseJson()));
  }

  public long replayRequired(IdempotencyRecord record, String requestHash) {
    assertSameRequest(record, requestHash);
    if (!"SUCCEEDED".equals(record.getStatus())) {
      throw new BusinessException(ErrorCode.DEPENDENCY_UNAVAILABLE, "相同请求仍在处理中，请稍后重试");
    }
    return parseMeetingId(record.getResponseJson());
  }

  public String responseJson(long meetingId) {
    try {
      return objectMapper.writeValueAsString(new IdempotencyResponse(meetingId));
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Cannot serialize idempotency response", exception);
    }
  }

  private void assertSameRequest(IdempotencyRecord record, String requestHash) {
    if (!record.getRequestHash().equals(requestHash)) {
      throw new BusinessException(ErrorCode.IDEMPOTENCY_KEY_REUSED);
    }
  }

  private long parseMeetingId(String responseJson) {
    try {
      JsonNode node = objectMapper.readTree(responseJson);
      if (node.isTextual()) {
        node = objectMapper.readTree(node.asText());
      }
      JsonNode meetingId = node.get("meetingId");
      if (meetingId == null || !meetingId.canConvertToLong()) {
        throw new IllegalStateException("Idempotency response does not contain meetingId");
      }
      return meetingId.longValue();
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Cannot parse idempotency response", exception);
    }
  }

  private record IdempotencyResponse(long meetingId) {}
}
