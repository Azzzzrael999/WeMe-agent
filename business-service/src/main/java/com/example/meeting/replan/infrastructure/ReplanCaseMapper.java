package com.example.meeting.replan.infrastructure;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.example.meeting.replan.domain.ReplanCaseRecord;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Options;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface ReplanCaseMapper extends BaseMapper<ReplanCaseRecord> {

  @Options(useGeneratedKeys = true, keyProperty = "id")
  int insert(ReplanCaseRecord record);

  @Select(
      """
      SELECT * FROM meeting_replan_case WHERE id = #{id}
      """)
  Optional<ReplanCaseRecord> findById(@Param("id") long id);

  @Select(
      """
      SELECT * FROM meeting_replan_case WHERE id = #{id} FOR UPDATE
      """)
  Optional<ReplanCaseRecord> findByIdForUpdate(@Param("id") long id);

  @Select(
      """
      <script>
      SELECT * FROM meeting_replan_case
      WHERE 1 = 1
      <if test="admin == false">AND organizer_id = #{userId}</if>
      <if test="status != null">AND status = #{status}</if>
      ORDER BY created_at DESC, id DESC
      LIMIT #{limit} OFFSET #{offset}
      </script>
      """)
  List<ReplanCaseRecord> findVisiblePage(
      @Param("userId") long userId,
      @Param("admin") boolean admin,
      @Param("status") String status,
      @Param("limit") int limit,
      @Param("offset") long offset);

  @Select(
      """
      <script>
      SELECT COUNT(*) FROM meeting_replan_case
      WHERE 1 = 1
      <if test="admin == false">AND organizer_id = #{userId}</if>
      <if test="status != null">AND status = #{status}</if>
      </script>
      """)
  long countVisible(
      @Param("userId") long userId, @Param("admin") boolean admin, @Param("status") String status);

  @Update(
      """
      UPDATE meeting_replan_case
      SET status = 'RESOLVED', resolution_type = #{resolutionType},
          resolved_room_id = #{roomId}, resolved_start_at = #{startAt},
          resolved_end_at = #{endAt}, version = version + 1,
          updated_at = #{resolvedAt}, resolved_at = #{resolvedAt}
      WHERE meeting_id = #{meetingId} AND status = 'OPEN'
      """)
  int resolveOpenForMeeting(
      @Param("meetingId") long meetingId,
      @Param("resolutionType") String resolutionType,
      @Param("roomId") long roomId,
      @Param("startAt") LocalDateTime startAt,
      @Param("endAt") LocalDateTime endAt,
      @Param("resolvedAt") LocalDateTime resolvedAt);

  @Update(
      """
      UPDATE meeting_replan_case
      SET status = 'RESOLVED', resolution_type = 'QUICK_ROOM_CHANGE',
          resolved_room_id = #{roomId}, resolved_start_at = #{startAt},
          resolved_end_at = #{endAt}, version = version + 1,
          updated_at = #{resolvedAt}, resolved_at = #{resolvedAt}
      WHERE id = #{caseId} AND meeting_id = #{meetingId}
        AND status = 'OPEN' AND version = #{expectedVersion}
      """)
  int resolveQuick(
      @Param("caseId") long caseId,
      @Param("meetingId") long meetingId,
      @Param("expectedVersion") int expectedVersion,
      @Param("roomId") long roomId,
      @Param("startAt") LocalDateTime startAt,
      @Param("endAt") LocalDateTime endAt,
      @Param("resolvedAt") LocalDateTime resolvedAt);

  @Update(
      """
      UPDATE meeting_replan_case
      SET status = 'CANCELLED', resolution_type = 'MEETING_CANCELLED',
          version = version + 1, updated_at = #{resolvedAt}, resolved_at = #{resolvedAt}
      WHERE meeting_id = #{meetingId} AND status = 'OPEN'
      """)
  int cancelOpenForMeeting(
      @Param("meetingId") long meetingId, @Param("resolvedAt") LocalDateTime resolvedAt);

  @Select(
      """
      SELECT replan.*
      FROM meeting_replan_case replan
      JOIN meeting m ON m.id = replan.meeting_id
      WHERE replan.failed_room_id = #{roomId}
        AND replan.status = 'OPEN'
        AND m.status = 'CONFIRMED'
        AND m.room_id = replan.failed_room_id
      ORDER BY replan.id
      FOR UPDATE
      """)
  List<ReplanCaseRecord> findRestorableByRoom(@Param("roomId") long roomId);

  @Update(
      """
      UPDATE meeting_replan_case
      SET status = 'RESTORED', resolution_type = 'RESOURCE_RESTORED',
          resolved_room_id = failed_room_id, resolved_start_at = original_start_at,
          resolved_end_at = original_end_at, version = version + 1,
          updated_at = #{resolvedAt}, resolved_at = #{resolvedAt}
      WHERE id = #{caseId} AND status = 'OPEN'
      """)
  int restore(@Param("caseId") long caseId, @Param("resolvedAt") LocalDateTime resolvedAt);
}
