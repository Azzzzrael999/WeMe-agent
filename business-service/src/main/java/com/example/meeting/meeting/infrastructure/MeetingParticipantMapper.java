package com.example.meeting.meeting.infrastructure;

import com.example.meeting.meeting.domain.MeetingParticipantRecord;
import java.util.List;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface MeetingParticipantMapper {

  @Insert(
      """
            <script>
            INSERT INTO meeting_participant (meeting_id, employee_id, participant_type)
            VALUES
            <foreach collection="records" item="item" separator=",">
              (#{item.meetingId}, #{item.employeeId}, #{item.participantType})
            </foreach>
            </script>
            """)
  int insertBatch(@Param("records") List<MeetingParticipantRecord> records);

  @Delete("DELETE FROM meeting_participant WHERE meeting_id = #{meetingId}")
  int deleteByMeetingId(@Param("meetingId") long meetingId);

  @Select(
      """
            SELECT participant.employee_id, employee.display_name,
                   participant.participant_type
            FROM meeting_participant participant
            JOIN sys_user employee ON employee.id = participant.employee_id
            WHERE participant.meeting_id = #{meetingId}
            ORDER BY CASE participant.participant_type
                       WHEN 'REQUIRED' THEN 0 ELSE 1
                     END,
                     participant.employee_id
            """)
  List<MeetingParticipantViewRow> findViewsByMeetingId(@Param("meetingId") long meetingId);

  @Select(
      """
            SELECT COUNT(*)
            FROM meeting_participant
            WHERE meeting_id = #{meetingId} AND employee_id = #{employeeId}
            """)
  int countParticipant(@Param("meetingId") long meetingId, @Param("employeeId") long employeeId);

  @Select(
      """
            SELECT DISTINCT employee_id
            FROM meeting_participant
            WHERE meeting_id = #{meetingId}
            ORDER BY employee_id
            """)
  List<Long> findEmployeeIdsByMeetingId(@Param("meetingId") long meetingId);
}
