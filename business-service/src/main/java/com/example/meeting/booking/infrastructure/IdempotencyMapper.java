package com.example.meeting.booking.infrastructure;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.example.meeting.booking.domain.IdempotencyRecord;
import java.time.LocalDateTime;
import java.util.Optional;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface IdempotencyMapper extends BaseMapper<IdempotencyRecord> {

  @Select(
      """
            SELECT id, user_id, operation, idempotency_key, request_hash,
                   status, response_json, expires_at
            FROM idempotency_record
            WHERE user_id = #{userId} AND operation = #{operation}
              AND idempotency_key = #{idempotencyKey}
            """)
  Optional<IdempotencyRecord> findByKey(
      @Param("userId") long userId,
      @Param("operation") String operation,
      @Param("idempotencyKey") String idempotencyKey);

  @Select(
      """
            SELECT id, user_id, operation, idempotency_key, request_hash,
                   status, response_json, expires_at
            FROM idempotency_record
            WHERE user_id = #{userId} AND operation = #{operation}
              AND idempotency_key = #{idempotencyKey}
            FOR UPDATE
            """)
  Optional<IdempotencyRecord> findByKeyForUpdate(
      @Param("userId") long userId,
      @Param("operation") String operation,
      @Param("idempotencyKey") String idempotencyKey);

  @Delete(
      """
            DELETE FROM idempotency_record
            WHERE user_id = #{userId} AND operation = #{operation}
              AND idempotency_key = #{idempotencyKey} AND expires_at <= #{now}
            """)
  int deleteExpired(
      @Param("userId") long userId,
      @Param("operation") String operation,
      @Param("idempotencyKey") String idempotencyKey,
      @Param("now") LocalDateTime now);

  @Update(
      """
            UPDATE idempotency_record
            SET status = 'SUCCEEDED', response_json = #{responseJson}
            WHERE id = #{id} AND status = 'PROCESSING'
            """)
  int markSucceeded(@Param("id") long id, @Param("responseJson") String responseJson);
}
