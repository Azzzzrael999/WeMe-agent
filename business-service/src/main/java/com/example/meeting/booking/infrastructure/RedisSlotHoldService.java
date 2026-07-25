package com.example.meeting.booking.infrastructure;

import com.example.meeting.booking.application.BookingProperties;
import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.booking.domain.SlotHoldReservation;
import com.example.meeting.booking.domain.TimeSlot;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Service;

@Service
public class RedisSlotHoldService {

  private static final Logger LOGGER = LoggerFactory.getLogger(RedisSlotHoldService.class);
  private static final String CONFLICT = "CONFLICT";

  private static final DefaultRedisScript<String> ACQUIRE_SCRIPT =
      new DefaultRedisScript<>(
          """
                    local token = redis.call('GET', KEYS[1])
                    if not token then
                      redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2], 'NX')
                      token = redis.call('GET', KEYS[1])
                    end
                    for index = 2, #KEYS do
                      local current = redis.call('GET', KEYS[index])
                      if current and current ~= token then
                        return 'CONFLICT'
                      end
                    end
                    for index = 2, #KEYS do
                      if not redis.call('GET', KEYS[index]) then
                        redis.call('SET', KEYS[index], token, 'PX', ARGV[2], 'NX')
                      end
                    end
                    return token
                    """,
          String.class);

  private static final DefaultRedisScript<Long> RELEASE_SCRIPT =
      new DefaultRedisScript<>(
          """
                    local released = 0
                    for index = 1, #KEYS do
                      if redis.call('GET', KEYS[index]) == ARGV[1] then
                        released = released + redis.call('DEL', KEYS[index])
                      end
                    end
                    return released
                    """,
          Long.class);

  private final StringRedisTemplate redisTemplate;
  private final BookingProperties properties;

  public RedisSlotHoldService(StringRedisTemplate redisTemplate, BookingProperties properties) {
    this.redisTemplate = redisTemplate;
    this.properties = properties;
  }

  public SlotHoldReservation acquire(
      NormalizedMeetingCommand command, long userId, String logicalOperationKey) {
    if (!properties.redisHoldEnabled()) {
      return SlotHoldReservation.degraded();
    }
    List<String> keys = holdKeys(command, userId, logicalOperationKey);
    String candidateToken = "hold_" + UUID.randomUUID().toString().replace("-", "");
    try {
      String token =
          redisTemplate.execute(
              ACQUIRE_SCRIPT, keys, candidateToken, Long.toString(properties.holdTtlMillis()));
      if (CONFLICT.equals(token)) {
        throw new BusinessException(ErrorCode.BOOKING_CONFLICT);
      }
      if (token == null || token.isBlank()) {
        LOGGER.warn("Redis hold returned no owner token; using database-only booking");
        return SlotHoldReservation.degraded();
      }
      return new SlotHoldReservation(keys, token, true);
    } catch (BusinessException exception) {
      throw exception;
    } catch (DataAccessException exception) {
      LOGGER.warn(
          "Redis hold unavailable; using database-only booking: {}",
          exception.getClass().getSimpleName());
      return SlotHoldReservation.degraded();
    }
  }

  public void release(SlotHoldReservation reservation) {
    if (!reservation.active()) {
      return;
    }
    try {
      redisTemplate.execute(RELEASE_SCRIPT, reservation.keys(), reservation.token());
    } catch (DataAccessException exception) {
      LOGGER.warn(
          "Redis hold release failed and will expire by TTL: {}",
          exception.getClass().getSimpleName());
    }
  }

  List<String> holdKeys(NormalizedMeetingCommand command, long userId, String logicalOperationKey) {
    String bookingDate = command.schedule().slots().getFirst().bookingDate().toString();
    String prefix = "meeting:hold:{" + bookingDate + "}:";
    List<String> keys = new ArrayList<>();
    keys.add(prefix + "operation:" + hash(userId + "|" + logicalOperationKey));
    for (TimeSlot slot : command.schedule().slots()) {
      keys.add(prefix + "room:" + command.roomId() + ":slot:" + slot.slotIndex());
    }
    for (Long employeeId : command.requiredParticipantIds()) {
      for (TimeSlot slot : command.schedule().slots()) {
        keys.add(prefix + "employee:" + employeeId + ":slot:" + slot.slotIndex());
      }
    }
    return keys;
  }

  private String hash(String value) {
    try {
      MessageDigest digest = MessageDigest.getInstance("SHA-256");
      return HexFormat.of()
          .formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)))
          .substring(0, 32);
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 is unavailable", exception);
    }
  }
}
