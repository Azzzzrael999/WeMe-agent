package com.example.meeting.booking.application;

import com.example.meeting.agentgateway.internal.AgentToolDtos.ConfirmBookingResponse;
import com.example.meeting.booking.domain.IdempotencyRecord;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.json.StoredJson;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.Optional;
import org.springframework.stereotype.Component;

@Component
public class ConfirmationIdempotencySupport {

  public static final String OPERATION = "CONFIRM_AGENT_BOOKING";

  private final ObjectMapper objectMapper;
  private final BookingProperties properties;

  public ConfirmationIdempotencySupport(ObjectMapper objectMapper, BookingProperties properties) {
    this.objectMapper = objectMapper;
    this.properties = properties;
  }

  public String requestHash(String confirmationToken, String payloadHash) {
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256")
                  .digest(
                      (confirmationToken + '|' + payloadHash).getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 is unavailable", exception);
    }
  }

  public Optional<ConfirmBookingResponse> replay(
      IdempotencyRecord existing, String requestHash, LocalDateTime now) {
    if (existing.getExpiresAt().isBefore(now)) {
      return Optional.empty();
    }
    if (!MessageDigest.isEqual(
        existing.getRequestHash().getBytes(StandardCharsets.UTF_8),
        requestHash.getBytes(StandardCharsets.UTF_8))) {
      throw new BusinessException(ErrorCode.IDEMPOTENCY_KEY_REUSED);
    }
    if (!"SUCCEEDED".equals(existing.getStatus()) || existing.getResponseJson() == null) {
      throw new BusinessException(ErrorCode.DEPENDENCY_UNAVAILABLE, "相同确认请求仍在处理中");
    }
    return Optional.of(read(existing.getResponseJson()));
  }

  public IdempotencyRecord newRecord(
      long userId, String idempotencyKey, String requestHash, LocalDateTime now) {
    return newRecord(OPERATION, userId, idempotencyKey, requestHash, now);
  }

  public IdempotencyRecord newRecord(
      String operation, long userId, String idempotencyKey, String requestHash, LocalDateTime now) {
    IdempotencyRecord record = new IdempotencyRecord();
    record.setUserId(userId);
    record.setOperation(operation);
    record.setIdempotencyKey(idempotencyKey);
    record.setRequestHash(requestHash);
    record.setStatus("PROCESSING");
    record.setExpiresAt(now.plusHours(properties.idempotencyTtlHours()));
    return record;
  }

  public String write(ConfirmBookingResponse response) {
    try {
      return objectMapper.writeValueAsString(response);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Cannot serialize confirmation response", exception);
    }
  }

  public ConfirmBookingResponse read(String json) {
    try {
      return StoredJson.read(objectMapper, json, ConfirmBookingResponse.class);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Stored confirmation response is invalid", exception);
    }
  }
}
