package com.example.meeting.booking.application;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.web.ApiErrorDetail;
import java.util.List;
import java.util.concurrent.locks.ReentrantLock;
import java.util.function.Supplier;
import org.springframework.stereotype.Component;

@Component
public class IdempotencyKeyCoordinator {

  private static final int STRIPE_COUNT = 256;
  private final ReentrantLock[] stripes = new ReentrantLock[STRIPE_COUNT];

  public IdempotencyKeyCoordinator() {
    for (int index = 0; index < stripes.length; index++) {
      stripes[index] = new ReentrantLock();
    }
  }

  public String normalize(String idempotencyKey) {
    if (idempotencyKey == null || idempotencyKey.isBlank()) {
      throw validation("Idempotency-Key", "REQUIRED", "必须提供 Idempotency-Key");
    }
    String normalized = idempotencyKey.trim();
    if (normalized.length() > 80) {
      throw validation("Idempotency-Key", "TOO_LONG", "Idempotency-Key 最长 80 字符");
    }
    return normalized;
  }

  public <T> T execute(long userId, String operation, String key, Supplier<T> action) {
    String identity = userId + "|" + operation + "|" + key;
    ReentrantLock lock = stripes[Math.floorMod(identity.hashCode(), stripes.length)];
    lock.lock();
    try {
      return action.get();
    } finally {
      lock.unlock();
    }
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }
}
