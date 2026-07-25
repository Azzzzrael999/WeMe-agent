package com.example.meeting.booking.infrastructure;

import com.example.meeting.booking.domain.MeetingRoomSlotRecord;
import java.time.LocalDateTime;
import java.util.List;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface MeetingRoomSlotMapper {

  @Insert(
      """
            <script>
            INSERT INTO meeting_room_slot (
                meeting_id, room_id, booking_date, slot_index, start_at, end_at
            )
            VALUES
            <foreach collection="records" item="item" separator=",">
              (#{item.meetingId}, #{item.roomId}, #{item.bookingDate},
               #{item.slotIndex}, #{item.startAt}, #{item.endAt})
            </foreach>
            </script>
            """)
  int insertBatch(@Param("records") List<MeetingRoomSlotRecord> records);

  @Delete("DELETE FROM meeting_room_slot WHERE meeting_id = #{meetingId}")
  int deleteByMeetingId(@Param("meetingId") long meetingId);

  @Select(
      """
      <script>
      SELECT DISTINCT room_id
      FROM meeting_room_slot
      WHERE room_id IN
      <foreach collection="roomIds" item="roomId" open="(" separator="," close=")">
        #{roomId}
      </foreach>
        AND start_at &lt; #{to}
        AND end_at &gt; #{from}
      </script>
      """)
  List<Long> findBusyRoomIds(
      @Param("roomIds") List<Long> roomIds,
      @Param("from") LocalDateTime from,
      @Param("to") LocalDateTime to);
}
