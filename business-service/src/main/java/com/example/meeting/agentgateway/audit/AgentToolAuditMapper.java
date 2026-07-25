package com.example.meeting.agentgateway.audit;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface AgentToolAuditMapper extends BaseMapper<AgentToolAuditRecord> {

  @Select(
      """
      SELECT id, trace_id, run_id, tool_call_id, tool_name, user_id, risk_level,
             request_hash, result_code, response_json, duration_ms, created_at
      FROM agent_tool_audit
      WHERE run_id = #{runId} AND tool_call_id = #{toolCallId} AND tool_name = #{toolName}
      """)
  Optional<AgentToolAuditRecord> find(
      @Param("runId") String runId,
      @Param("toolCallId") String toolCallId,
      @Param("toolName") String toolName);

  @Update(
      """
      UPDATE agent_tool_audit
      SET result_code = #{resultCode}, response_json = #{responseJson}, duration_ms = #{durationMs}
      WHERE id = #{id}
      """)
  int complete(
      @Param("id") long id,
      @Param("resultCode") String resultCode,
      @Param("responseJson") String responseJson,
      @Param("durationMs") long durationMs);
}
