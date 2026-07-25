package com.example.meeting.outbox;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import java.time.LocalDateTime;
import java.util.List;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface MessageOutboxMapper extends BaseMapper<MessageOutboxRecord> {

  @Select(
      """
      SELECT id, event_id, event_type, aggregate_type, aggregate_id, topic, tag,
             trace_id, run_id, payload_json, status, retry_count, next_retry_at,
             created_at, sent_at
      FROM message_outbox
      WHERE (status IN ('NEW', 'RETRY')
             AND (next_retry_at IS NULL OR next_retry_at <= #{now}))
         OR (status = 'SENDING' AND next_retry_at <= #{now})
      ORDER BY id
      LIMIT #{limit}
      """)
  List<MessageOutboxRecord> findReady(@Param("now") LocalDateTime now, @Param("limit") int limit);

  @Update(
      """
      UPDATE message_outbox
      SET status = 'SENDING', next_retry_at = #{leaseUntil}
      WHERE id = #{id}
        AND ((status IN ('NEW', 'RETRY')
              AND (next_retry_at IS NULL OR next_retry_at <= #{now}))
             OR (status = 'SENDING' AND next_retry_at <= #{now}))
      """)
  int claim(
      @Param("id") long id,
      @Param("now") LocalDateTime now,
      @Param("leaseUntil") LocalDateTime leaseUntil);

  @Update(
      """
      UPDATE message_outbox
      SET status = 'SENT', sent_at = #{sentAt}, next_retry_at = NULL
      WHERE id = #{id} AND status = 'SENDING'
      """)
  int markSent(@Param("id") long id, @Param("sentAt") LocalDateTime sentAt);

  @Update(
      """
      UPDATE message_outbox
      SET status = #{status}, retry_count = retry_count + 1, next_retry_at = #{nextRetryAt}
      WHERE id = #{id} AND status = 'SENDING'
      """)
  int markFailed(
      @Param("id") long id,
      @Param("status") String status,
      @Param("nextRetryAt") LocalDateTime nextRetryAt);
}
