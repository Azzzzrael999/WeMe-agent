package com.example.meeting.mq;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface EventConsumeRecordMapper extends BaseMapper<EventConsumeRecord> {

  @Select(
      """
      SELECT COUNT(*) FROM event_consume_record
      WHERE consumer_group = #{consumerGroup} AND event_id = #{eventId}
      """)
  int countConsumed(@Param("consumerGroup") String consumerGroup, @Param("eventId") String eventId);
}
