package com.example.meeting.booking.infrastructure;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.example.meeting.booking.domain.BookingRequestRecord;
import java.time.LocalDateTime;
import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface BookingRequestMapper extends BaseMapper<BookingRequestRecord> {

  @Select(
      """
      SELECT id, request_no, user_id, run_id, trace_id, tool_call_id, operation,
             payload_json, status, meeting_id, error_code, error_message, created_at, updated_at
      FROM booking_request WHERE request_no = #{requestNo}
      """)
  Optional<BookingRequestRecord> findByRequestNo(@Param("requestNo") String requestNo);

  @Select(
      """
      SELECT id, request_no, user_id, run_id, trace_id, tool_call_id, operation,
             payload_json, status, meeting_id, error_code, error_message, created_at, updated_at
      FROM booking_request WHERE request_no = #{requestNo}
      FOR UPDATE
      """)
  Optional<BookingRequestRecord> findByRequestNoForUpdate(@Param("requestNo") String requestNo);

  @Update(
      """
      UPDATE booking_request SET status = 'PROCESSING', updated_at = #{now}
      WHERE id = #{id} AND status = 'PENDING'
      """)
  int markProcessing(@Param("id") long id, @Param("now") LocalDateTime now);

  @Update(
      """
      UPDATE booking_request
      SET status = 'SUCCESS', meeting_id = #{meetingId}, error_code = NULL,
          error_message = NULL, updated_at = #{now}
      WHERE id = #{id} AND status = 'PROCESSING'
      """)
  int markSuccess(
      @Param("id") long id, @Param("meetingId") long meetingId, @Param("now") LocalDateTime now);

  @Update(
      """
      UPDATE booking_request
      SET status = 'CONFLICT', error_code = 'BOOKING_CONFLICT',
          error_message = #{message}, updated_at = #{now}
      WHERE id = #{id} AND status IN ('PENDING', 'PROCESSING')
      """)
  int markConflict(
      @Param("id") long id, @Param("message") String message, @Param("now") LocalDateTime now);
}
