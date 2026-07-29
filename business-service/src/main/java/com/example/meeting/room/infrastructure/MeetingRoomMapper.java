package com.example.meeting.room.infrastructure;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.example.meeting.room.domain.MeetingRoom;
import java.util.List;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Options;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface MeetingRoomMapper extends BaseMapper<MeetingRoom> {

  @Select(
      """
            SELECT r.id AS room_id, r.code, r.name, r.building, r.floor,
                   r.capacity, r.room_type, r.is_hot AS hot, r.status, r.version,
                   f.code AS feature_code, f.name AS feature_name
            FROM meeting_room r
            LEFT JOIN meeting_room_feature rf ON rf.room_id = r.id
            LEFT JOIN room_feature f ON f.id = rf.feature_id
            WHERE r.status = 'ACTIVE'
            ORDER BY r.code, f.code
            """)
  List<RoomWithFeatureRow> findActiveRoomsWithFeatures();

  @Select(
      """
            SELECT r.id AS room_id, r.code, r.name, r.building, r.floor,
                   r.capacity, r.room_type, r.is_hot AS hot, r.status, r.version,
                   f.code AS feature_code, f.name AS feature_name
            FROM meeting_room r
            LEFT JOIN meeting_room_feature rf ON rf.room_id = r.id
            LEFT JOIN room_feature f ON f.id = rf.feature_id
            ORDER BY r.code, f.code
            """)
  List<RoomWithFeatureRow> findAllRoomsWithFeatures();

  @Select(
      """
            SELECT r.id AS room_id, r.code, r.name, r.building, r.floor,
                   r.capacity, r.room_type, r.is_hot AS hot, r.status, r.version,
                   f.code AS feature_code, f.name AS feature_name
            FROM meeting_room r
            LEFT JOIN meeting_room_feature rf ON rf.room_id = r.id
            LEFT JOIN room_feature f ON f.id = rf.feature_id
            WHERE r.id = #{roomId}
            ORDER BY f.code
            """)
  List<RoomWithFeatureRow> findRoomWithFeaturesById(@Param("roomId") long roomId);

  @Select("SELECT * FROM meeting_room WHERE code = #{code}")
  MeetingRoom findByCode(@Param("code") String code);

  @Select(
      """
      <script>
      SELECT id, code, name
      FROM room_feature
      WHERE code IN
      <foreach collection="codes" item="code" open="(" separator="," close=")">
        #{code}
      </foreach>
      ORDER BY code
      </script>
      """)
  List<RoomFeatureReference> findFeaturesByCodes(@Param("codes") List<String> codes);

  @Insert(
      """
      INSERT INTO meeting_room (
          code, name, building, floor, capacity, room_type, is_hot, status, version
      ) VALUES (
          #{code}, #{name}, #{building}, #{floor}, #{capacity}, #{roomType}, #{isHot},
          #{status}, #{version}
      )
      """)
  @Options(useGeneratedKeys = true, keyProperty = "id")
  int insertRoom(MeetingRoom room);

  @Update(
      """
      UPDATE meeting_room
      SET code = #{room.code},
          name = #{room.name},
          building = #{room.building},
          floor = #{room.floor},
          capacity = #{room.capacity},
          room_type = #{room.roomType},
          is_hot = #{room.isHot},
          version = version + 1,
          updated_at = CURRENT_TIMESTAMP(3)
      WHERE id = #{room.id}
        AND version = #{expectedVersion}
      """)
  int updateRoomWithVersion(
      @Param("room") MeetingRoom room, @Param("expectedVersion") int expectedVersion);

  @Update(
      """
      UPDATE meeting_room
      SET status = #{status},
          version = version + 1,
          updated_at = CURRENT_TIMESTAMP(3)
      WHERE id = #{roomId}
        AND version = #{expectedVersion}
      """)
  int updateStatusWithVersion(
      @Param("roomId") long roomId,
      @Param("status") String status,
      @Param("expectedVersion") int expectedVersion);

  @Delete("DELETE FROM meeting_room_feature WHERE room_id = #{roomId}")
  int deleteFeaturesByRoomId(@Param("roomId") long roomId);

  @Insert(
      """
      <script>
      INSERT INTO meeting_room_feature (room_id, feature_id)
      VALUES
      <foreach collection="featureIds" item="featureId" separator=",">
        (#{roomId}, #{featureId})
      </foreach>
      </script>
      """)
  int insertFeatures(@Param("roomId") long roomId, @Param("featureIds") List<Long> featureIds);

  @Select(
      """
      SELECT start_at, end_at
      FROM meeting_room_slot
      WHERE room_id = #{roomId}
        AND start_at < #{to}
        AND end_at > #{from}
      ORDER BY start_at
      """)
  List<RoomOccupiedSlotRow> findOccupiedSlots(
      @Param("roomId") long roomId,
      @Param("from") java.time.LocalDateTime from,
      @Param("to") java.time.LocalDateTime to);
}
