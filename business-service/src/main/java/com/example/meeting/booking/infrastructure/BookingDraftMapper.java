package com.example.meeting.booking.infrastructure;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.example.meeting.booking.domain.BookingDraftRecord;
import java.time.LocalDateTime;
import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface BookingDraftMapper extends BaseMapper<BookingDraftRecord> {

  @Select(
      """
      SELECT id, confirmation_token, user_id, run_id, tool_call_id, operation,
             payload_json, payload_hash, status, version, expires_at, created_at, used_at
      FROM booking_draft
      WHERE confirmation_token = #{token}
      """)
  Optional<BookingDraftRecord> findByToken(@Param("token") String token);

  @Select(
      """
      SELECT id, confirmation_token, user_id, run_id, tool_call_id, operation,
             payload_json, payload_hash, status, version, expires_at, created_at, used_at
      FROM booking_draft
      WHERE confirmation_token = #{token}
      FOR UPDATE
      """)
  Optional<BookingDraftRecord> findByTokenForUpdate(@Param("token") String token);

  @Update(
      """
      UPDATE booking_draft
      SET status = 'USED', version = version + 1, used_at = #{usedAt}
      WHERE id = #{id} AND status = 'PENDING' AND version = #{version}
      """)
  int markUsed(
      @Param("id") long id, @Param("version") int version, @Param("usedAt") LocalDateTime usedAt);
}
