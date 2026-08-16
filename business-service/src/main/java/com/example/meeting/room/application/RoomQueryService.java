package com.example.meeting.room.application;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.room.api.RoomFeatureView;
import com.example.meeting.room.api.RoomItemView;
import com.example.meeting.room.api.RoomListView;
import com.example.meeting.room.infrastructure.MeetingRoomMapper;
import com.example.meeting.room.infrastructure.RoomWithFeatureRow;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class RoomQueryService {

  private final MeetingRoomMapper meetingRoomMapper;

  public RoomQueryService(MeetingRoomMapper meetingRoomMapper) {
    this.meetingRoomMapper = meetingRoomMapper;
  }

  @Transactional(readOnly = true)
  public RoomListView findActiveRooms() {
    List<RoomItemView> items = toViews(meetingRoomMapper.findActiveRoomsWithFeatures());
    return new RoomListView(items, items.size());
  }

  @Transactional(readOnly = true)
  public RoomListView findAllRoomsForTrustedFacts() {
    List<RoomItemView> items = toViews(meetingRoomMapper.findAllRoomsWithFeatures());
    return new RoomListView(items, items.size());
  }

  @Transactional(readOnly = true)
  public RoomListView findVisibleRooms(AuthenticatedUser actor) {
    List<RoomWithFeatureRow> rows =
        actor.roles().contains("ADMIN")
            ? meetingRoomMapper.findAllRoomsWithFeatures()
            : meetingRoomMapper.findActiveRoomsWithFeatures();
    List<RoomItemView> items = toViews(rows);
    return new RoomListView(items, items.size());
  }

  @Transactional(readOnly = true)
  public RoomItemView findVisibleRoom(long roomId, AuthenticatedUser actor) {
    List<RoomItemView> items = toViews(meetingRoomMapper.findRoomWithFeaturesById(roomId));
    if (items.isEmpty()
        || (!actor.roles().contains("ADMIN") && !"ACTIVE".equals(items.getFirst().status()))) {
      throw new BusinessException(ErrorCode.ROOM_NOT_FOUND);
    }
    return items.getFirst();
  }

  @Transactional(readOnly = true)
  public RoomItemView findRoomForAdministration(long roomId) {
    List<RoomItemView> items = toViews(meetingRoomMapper.findRoomWithFeaturesById(roomId));
    if (items.isEmpty()) {
      throw new BusinessException(ErrorCode.ROOM_NOT_FOUND);
    }
    return items.getFirst();
  }

  private List<RoomItemView> toViews(List<RoomWithFeatureRow> rows) {
    Map<Long, MutableRoom> rooms = new LinkedHashMap<>();
    for (RoomWithFeatureRow row : rows) {
      MutableRoom room = rooms.computeIfAbsent(row.getRoomId(), ignored -> new MutableRoom(row));
      if (row.getFeatureCode() != null) {
        room.features.add(new RoomFeatureView(row.getFeatureCode(), row.getFeatureName()));
      }
    }
    return rooms.values().stream().map(MutableRoom::toView).toList();
  }

  private static final class MutableRoom {

    private final RoomWithFeatureRow row;
    private final List<RoomFeatureView> features = new ArrayList<>();

    private MutableRoom(RoomWithFeatureRow row) {
      this.row = row;
    }

    private RoomItemView toView() {
      return new RoomItemView(
          row.getRoomId(),
          row.getCode(),
          row.getName(),
          row.getBuilding(),
          row.getFloor(),
          row.getCapacity(),
          row.getRoomType(),
          Boolean.TRUE.equals(row.getHot()),
          row.getStatus(),
          row.getVersion(),
          features);
    }
  }
}
