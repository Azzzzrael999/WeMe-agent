package com.example.meeting.room.application;

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
    Map<Long, MutableRoom> rooms = new LinkedHashMap<>();
    for (RoomWithFeatureRow row : meetingRoomMapper.findActiveRoomsWithFeatures()) {
      MutableRoom room = rooms.computeIfAbsent(row.getRoomId(), ignored -> new MutableRoom(row));
      if (row.getFeatureCode() != null) {
        room.features.add(new RoomFeatureView(row.getFeatureCode(), row.getFeatureName()));
      }
    }
    List<RoomItemView> items = rooms.values().stream().map(MutableRoom::toView).toList();
    return new RoomListView(items, items.size());
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
          features);
    }
  }
}
