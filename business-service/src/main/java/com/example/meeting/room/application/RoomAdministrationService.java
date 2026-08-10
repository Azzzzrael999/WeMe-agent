package com.example.meeting.room.application;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.replan.application.ReplanCaseLifecycleService;
import com.example.meeting.room.api.CreateRoomRequest;
import com.example.meeting.room.api.RoomItemView;
import com.example.meeting.room.api.UpdateRoomRequest;
import com.example.meeting.room.api.UpdateRoomStatusRequest;
import com.example.meeting.room.domain.MeetingRoom;
import com.example.meeting.room.infrastructure.MeetingRoomMapper;
import com.example.meeting.room.infrastructure.RoomFeatureReference;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class RoomAdministrationService {

  private static final String ACTIVE = "ACTIVE";

  private final MeetingRoomMapper meetingRoomMapper;
  private final RoomQueryService roomQueryService;
  private final ReplanCaseLifecycleService replanCaseLifecycleService;

  public RoomAdministrationService(
      MeetingRoomMapper meetingRoomMapper,
      RoomQueryService roomQueryService,
      ReplanCaseLifecycleService replanCaseLifecycleService) {
    this.meetingRoomMapper = meetingRoomMapper;
    this.roomQueryService = roomQueryService;
    this.replanCaseLifecycleService = replanCaseLifecycleService;
  }

  @Transactional
  public RoomItemView create(CreateRoomRequest request) {
    RoomInput input = roomInput(request);
    if (meetingRoomMapper.findByCode(input.code()) != null) {
      throw new BusinessException(ErrorCode.ROOM_CODE_CONFLICT);
    }
    List<RoomFeatureReference> features = resolveFeatures(input.featureCodes());
    MeetingRoom room = newRoom(input);
    try {
      meetingRoomMapper.insertRoom(room);
    } catch (DataIntegrityViolationException exception) {
      throw new BusinessException(ErrorCode.ROOM_CODE_CONFLICT);
    }
    replaceFeatures(room.getId(), features);
    return roomQueryService.findRoomForAdministration(room.getId());
  }

  @Transactional
  public RoomItemView update(long roomId, UpdateRoomRequest request) {
    requireRoom(roomId);
    RoomInput input = roomInput(request);
    MeetingRoom sameCode = meetingRoomMapper.findByCode(input.code());
    if (sameCode != null && !sameCode.getId().equals(roomId)) {
      throw new BusinessException(ErrorCode.ROOM_CODE_CONFLICT);
    }
    List<RoomFeatureReference> features = resolveFeatures(input.featureCodes());
    MeetingRoom room = newRoom(input);
    room.setId(roomId);
    try {
      if (meetingRoomMapper.updateRoomWithVersion(room, request.expectedVersion()) != 1) {
        throw new BusinessException(ErrorCode.ROOM_STATE_CONFLICT);
      }
    } catch (DataIntegrityViolationException exception) {
      throw new BusinessException(ErrorCode.ROOM_CODE_CONFLICT);
    }
    replaceFeatures(roomId, features);
    return roomQueryService.findRoomForAdministration(roomId);
  }

  @Transactional
  public RoomItemView updateStatus(long roomId, UpdateRoomStatusRequest request) {
    MeetingRoom room = requireRoom(roomId);
    String reason = request.reason() == null ? null : request.reason().trim();
    if ("INACTIVE".equals(request.status()) && (reason == null || reason.isBlank())) {
      throw validation("reason", "REQUIRED", "停用会议室时必须填写原因");
    }
    if (meetingRoomMapper.updateStatusWithVersion(
            roomId, request.status(), request.expectedVersion())
        != 1) {
      throw new BusinessException(ErrorCode.ROOM_STATE_CONFLICT);
    }
    if ("ACTIVE".equals(room.getStatus()) && "INACTIVE".equals(request.status())) {
      replanCaseLifecycleService.createForRoomFailure(room, reason, request.expectedVersion() + 1);
    } else if ("INACTIVE".equals(room.getStatus()) && "ACTIVE".equals(request.status())) {
      replanCaseLifecycleService.restoreForRoom(roomId);
    }
    return roomQueryService.findRoomForAdministration(roomId);
  }

  private MeetingRoom requireRoom(long roomId) {
    MeetingRoom room = meetingRoomMapper.selectById(roomId);
    if (room == null) {
      throw new BusinessException(ErrorCode.ROOM_NOT_FOUND);
    }
    return room;
  }

  private List<RoomFeatureReference> resolveFeatures(List<String> rawCodes) {
    List<String> featureCodes = normalizeFeatureCodes(rawCodes);
    if (featureCodes.isEmpty()) {
      return List.of();
    }
    List<RoomFeatureReference> features = meetingRoomMapper.findFeaturesByCodes(featureCodes);
    Set<String> foundCodes = new LinkedHashSet<>();
    for (RoomFeatureReference feature : features) {
      foundCodes.add(feature.getCode());
    }
    for (String featureCode : featureCodes) {
      if (!foundCodes.contains(featureCode)) {
        throw validation("featureCodes", "UNKNOWN_FEATURE", "存在未知会议室设备");
      }
    }
    return features;
  }

  private void replaceFeatures(long roomId, List<RoomFeatureReference> features) {
    meetingRoomMapper.deleteFeaturesByRoomId(roomId);
    if (!features.isEmpty()) {
      meetingRoomMapper.insertFeatures(
          roomId, features.stream().map(RoomFeatureReference::getId).toList());
    }
  }

  private RoomInput roomInput(CreateRoomRequest request) {
    return new RoomInput(
        normalize(request.code()),
        normalize(request.name()),
        normalize(request.building()),
        normalize(request.floor()),
        request.capacity(),
        normalize(request.roomType()).toUpperCase(Locale.ROOT),
        request.isHot(),
        normalizeFeatureCodes(request.featureCodes()));
  }

  private RoomInput roomInput(UpdateRoomRequest request) {
    return new RoomInput(
        normalize(request.code()),
        normalize(request.name()),
        normalize(request.building()),
        normalize(request.floor()),
        request.capacity(),
        normalize(request.roomType()).toUpperCase(Locale.ROOT),
        request.isHot(),
        normalizeFeatureCodes(request.featureCodes()));
  }

  private MeetingRoom newRoom(RoomInput input) {
    MeetingRoom room = new MeetingRoom();
    room.setCode(input.code());
    room.setName(input.name());
    room.setBuilding(input.building());
    room.setFloor(input.floor());
    room.setCapacity(input.capacity());
    room.setRoomType(input.roomType());
    room.setIsHot(input.isHot());
    room.setStatus(ACTIVE);
    room.setVersion(0);
    return room;
  }

  private List<String> normalizeFeatureCodes(List<String> values) {
    Set<String> unique = new LinkedHashSet<>();
    for (String value : values) {
      unique.add(normalize(value).toUpperCase(Locale.ROOT));
    }
    return new ArrayList<>(unique);
  }

  private String normalize(String value) {
    return value.trim();
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }

  private record RoomInput(
      String code,
      String name,
      String building,
      String floor,
      int capacity,
      String roomType,
      boolean isHot,
      List<String> featureCodes) {}
}
