package com.example.meeting.booking.application;

import com.example.meeting.auth.domain.UserAccount;
import com.example.meeting.auth.infrastructure.UserMapper;
import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.room.domain.MeetingRoom;
import com.example.meeting.room.infrastructure.MeetingRoomMapper;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.springframework.stereotype.Component;

@Component
public class BookingValidator {

  private final MeetingRoomMapper meetingRoomMapper;
  private final UserMapper userMapper;

  public BookingValidator(MeetingRoomMapper meetingRoomMapper, UserMapper userMapper) {
    this.meetingRoomMapper = meetingRoomMapper;
    this.userMapper = userMapper;
  }

  public void validate(NormalizedMeetingCommand command) {
    MeetingRoom room = meetingRoomMapper.selectById(command.roomId());
    if (room == null || !"ACTIVE".equals(room.getStatus())) {
      throw validation("roomId", "ROOM_NOT_ACTIVE", "会议室不存在或未启用");
    }
    if (room.getCapacity() < command.participantCount()) {
      throw validation("roomId", "ROOM_CAPACITY_EXCEEDED", "会议室容量不足");
    }

    Set<Long> expectedIds = new HashSet<>(command.requiredParticipantIds());
    expectedIds.addAll(command.optionalParticipantIds());
    List<UserAccount> users = userMapper.selectByIds(expectedIds);
    Set<Long> activeIds = new HashSet<>();
    for (UserAccount user : users) {
      if (user.isActive()) {
        activeIds.add(user.getId());
      }
    }
    if (!activeIds.equals(expectedIds)) {
      throw validation("requiredParticipantIds", "PARTICIPANT_NOT_ACTIVE", "参与者不存在或未启用");
    }
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }
}
