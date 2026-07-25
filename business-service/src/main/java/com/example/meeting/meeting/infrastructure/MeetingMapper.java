package com.example.meeting.meeting.infrastructure;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.example.meeting.meeting.domain.MeetingRecord;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface MeetingMapper extends BaseMapper<MeetingRecord> {

  @Select(
      """
            SELECT id, meeting_no, title, meeting_type, organizer_id, room_id,
                   start_at, end_at, status, source, run_id, request_no, version,
                   created_at, updated_at, cancelled_at
            FROM meeting
            WHERE id = #{id}
            FOR UPDATE
            """)
  Optional<MeetingRecord> findByIdForUpdate(@Param("id") long id);

  @Select(
      """
            SELECT m.id, m.meeting_no, m.title, m.meeting_type,
                   m.organizer_id, organizer.display_name AS organizer_name,
                   m.room_id, room.code AS room_code, room.name AS room_name,
                   m.start_at, m.end_at, m.status, m.source, m.version,
                   m.created_at, m.updated_at, m.cancelled_at
            FROM meeting m
            JOIN sys_user organizer ON organizer.id = m.organizer_id
            JOIN meeting_room room ON room.id = m.room_id
            WHERE m.id = #{id}
            """)
  Optional<MeetingViewRow> findViewById(@Param("id") long id);

  @Select(
      """
            <script>
            SELECT DISTINCT m.id, m.meeting_no, m.title, m.meeting_type,
                   m.organizer_id, organizer.display_name AS organizer_name,
                   m.room_id, room.code AS room_code, room.name AS room_name,
                   m.start_at, m.end_at, m.status, m.source, m.version,
                   m.created_at, m.updated_at, m.cancelled_at
            FROM meeting m
            JOIN sys_user organizer ON organizer.id = m.organizer_id
            JOIN meeting_room room ON room.id = m.room_id
            WHERE 1 = 1
            <if test="admin == false">
              AND (
                m.organizer_id = #{userId}
                OR EXISTS (
                  SELECT 1 FROM meeting_participant visible_participant
                  WHERE visible_participant.meeting_id = m.id
                    AND visible_participant.employee_id = #{userId}
                )
              )
            </if>
            <if test="from != null">
              AND m.end_at &gt; #{from}
            </if>
            <if test="to != null">
              AND m.start_at &lt; #{to}
            </if>
            <if test="status != null">
              AND m.status = #{status}
            </if>
            ORDER BY m.start_at DESC, m.id DESC
            LIMIT #{limit} OFFSET #{offset}
            </script>
            """)
  List<MeetingViewRow> findVisibleMeetings(
      @Param("userId") long userId,
      @Param("admin") boolean admin,
      @Param("from") LocalDateTime from,
      @Param("to") LocalDateTime to,
      @Param("status") String status,
      @Param("limit") int limit,
      @Param("offset") long offset);

  @Select(
      """
            <script>
            SELECT COUNT(*)
            FROM meeting m
            JOIN sys_user organizer ON organizer.id = m.organizer_id
            JOIN meeting_room room ON room.id = m.room_id
            WHERE 1 = 1
            <if test="admin == false">
              AND (
                m.organizer_id = #{userId}
                OR EXISTS (
                  SELECT 1 FROM meeting_participant visible_participant
                  WHERE visible_participant.meeting_id = m.id
                    AND visible_participant.employee_id = #{userId}
                )
              )
            </if>
            <if test="from != null">
              AND m.end_at &gt; #{from}
            </if>
            <if test="to != null">
              AND m.start_at &lt; #{to}
            </if>
            <if test="status != null">
              AND m.status = #{status}
            </if>
            </script>
            """)
  long countVisibleMeetings(
      @Param("userId") long userId,
      @Param("admin") boolean admin,
      @Param("from") LocalDateTime from,
      @Param("to") LocalDateTime to,
      @Param("status") String status);

  @Update(
      """
            UPDATE meeting
            SET title = #{title}, meeting_type = #{meetingType}, room_id = #{roomId},
                start_at = #{startAt}, end_at = #{endAt}, version = version + 1,
                updated_at = #{updatedAt}
            WHERE id = #{id} AND status = 'CONFIRMED' AND version = #{expectedVersion}
            """)
  int updateConfirmedMeeting(
      @Param("id") long id,
      @Param("title") String title,
      @Param("meetingType") String meetingType,
      @Param("roomId") long roomId,
      @Param("startAt") LocalDateTime startAt,
      @Param("endAt") LocalDateTime endAt,
      @Param("expectedVersion") int expectedVersion,
      @Param("updatedAt") LocalDateTime updatedAt);

  @Update(
      """
            UPDATE meeting
            SET status = 'CANCELLED', version = version + 1,
                cancelled_at = #{cancelledAt}, updated_at = #{cancelledAt}
            WHERE id = #{id} AND status = 'CONFIRMED'
            """)
  int cancelConfirmedMeeting(@Param("id") long id, @Param("cancelledAt") LocalDateTime cancelledAt);
}
