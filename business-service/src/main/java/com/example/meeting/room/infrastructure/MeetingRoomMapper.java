package com.example.meeting.room.infrastructure;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.example.meeting.room.domain.MeetingRoom;
import java.util.List;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface MeetingRoomMapper extends BaseMapper<MeetingRoom> {

  @Select(
      """
            SELECT r.id AS room_id, r.code, r.name, r.building, r.floor,
                   r.capacity, r.room_type, r.is_hot AS hot, r.status,
                   f.code AS feature_code, f.name AS feature_name
            FROM meeting_room r
            LEFT JOIN meeting_room_feature rf ON rf.room_id = r.id
            LEFT JOIN room_feature f ON f.id = rf.feature_id
            WHERE r.status = 'ACTIVE'
            ORDER BY r.code, f.code
            """)
  List<RoomWithFeatureRow> findActiveRoomsWithFeatures();
}
