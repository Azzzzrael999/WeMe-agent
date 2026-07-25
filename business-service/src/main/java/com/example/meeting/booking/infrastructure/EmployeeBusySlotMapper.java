package com.example.meeting.booking.infrastructure;

import com.example.meeting.agentgateway.internal.EmployeeBusySlotViewRow;
import com.example.meeting.booking.domain.EmployeeBusySlotRecord;
import java.time.LocalDateTime;
import java.util.List;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface EmployeeBusySlotMapper {

  @Insert(
      """
            <script>
            INSERT INTO employee_busy_slot (
                meeting_id, employee_id, booking_date, slot_index, start_at, end_at
            )
            VALUES
            <foreach collection="records" item="item" separator=",">
              (#{item.meetingId}, #{item.employeeId}, #{item.bookingDate},
               #{item.slotIndex}, #{item.startAt}, #{item.endAt})
            </foreach>
            </script>
            """)
  int insertBatch(@Param("records") List<EmployeeBusySlotRecord> records);

  @Delete("DELETE FROM employee_busy_slot WHERE meeting_id = #{meetingId}")
  int deleteByMeetingId(@Param("meetingId") long meetingId);

  @Select(
      """
      <script>
      SELECT employee_id, meeting_id, start_at, end_at
      FROM employee_busy_slot
      WHERE employee_id IN
      <foreach collection="employeeIds" item="employeeId" open="(" separator="," close=")">
        #{employeeId}
      </foreach>
        AND start_at &lt; #{to}
        AND end_at &gt; #{from}
      ORDER BY employee_id, start_at, meeting_id
      </script>
      """)
  List<EmployeeBusySlotViewRow> findBusySlots(
      @Param("employeeIds") List<Long> employeeIds,
      @Param("from") LocalDateTime from,
      @Param("to") LocalDateTime to);
}
