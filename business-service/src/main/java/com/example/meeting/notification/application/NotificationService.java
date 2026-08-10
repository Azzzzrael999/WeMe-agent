package com.example.meeting.notification.application;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.notification.NotificationMapper;
import com.example.meeting.notification.NotificationRecord;
import com.example.meeting.notification.api.NotificationItemView;
import com.example.meeting.notification.api.NotificationListView;
import com.example.meeting.notification.api.ReadAllResultView;
import java.time.Clock;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class NotificationService {

  private static final Set<String> TYPES =
      Set.of(
          "MEETING_CONFIRMED",
          "MEETING_CHANGED",
          "MEETING_CANCELLED",
          "RESOURCE_UNAVAILABLE",
          "RESOURCE_RESTORED",
          "MEETING_REMINDER_24H",
          "MEETING_REMINDER_30M",
          "PREPARATION_MISSING",
          "ACTION_ITEM_DUE_SOON",
          "ACTION_ITEM_OVERDUE");

  private final NotificationMapper mapper;
  private final Clock clock;

  public NotificationService(NotificationMapper mapper, Clock clock) {
    this.mapper = mapper;
    this.clock = clock;
  }

  public NotificationListView list(
      long userId, boolean unreadOnly, String rawType, int page, int size) {
    if (page < 1 || size < 1 || size > 100) {
      throw validation("page", "INVALID_PAGINATION", "分页参数超出允许范围");
    }
    String type = normalizeType(rawType);
    long offset = (long) (page - 1) * size;
    List<NotificationItemView> items =
        mapper.findPage(userId, unreadOnly, type, size, offset).stream().map(this::view).toList();
    return new NotificationListView(
        items, mapper.countPage(userId, unreadOnly, type), mapper.countUnread(userId));
  }

  public long unreadCount(long userId) {
    return mapper.countUnread(userId);
  }

  @Transactional
  public NotificationItemView markRead(long notificationId, long userId) {
    NotificationRecord owned =
        mapper
            .findOwned(notificationId, userId)
            .orElseThrow(() -> new BusinessException(ErrorCode.NOTIFICATION_NOT_FOUND));
    if (owned.getReadAt() == null) {
      mapper.markRead(notificationId, userId, LocalDateTime.now(clock));
    }
    return view(
        mapper
            .findOwned(notificationId, userId)
            .orElseThrow(() -> new BusinessException(ErrorCode.NOTIFICATION_NOT_FOUND)));
  }

  @Transactional
  public ReadAllResultView markAllRead(long userId) {
    LocalDateTime now = LocalDateTime.now(clock);
    return new ReadAllResultView(mapper.markAllRead(userId, now), offset(now));
  }

  private String normalizeType(String rawType) {
    if (rawType == null || rawType.isBlank()) {
      return null;
    }
    String type = rawType.trim().toUpperCase(Locale.ROOT);
    if (!TYPES.contains(type)) {
      throw validation("type", "INVALID_NOTIFICATION_TYPE", "type 不是有效的通知类型");
    }
    return type;
  }

  private NotificationItemView view(NotificationRecord record) {
    return new NotificationItemView(
        record.getId(),
        record.getType(),
        record.getTitle(),
        record.getContent(),
        record.getRelatedMeetingId(),
        record.getRelatedReplanCaseId(),
        offset(record.getReadAt()),
        offset(record.getCreatedAt()));
  }

  private OffsetDateTime offset(LocalDateTime value) {
    return value == null ? null : value.atZone(clock.getZone()).toOffsetDateTime();
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }
}
