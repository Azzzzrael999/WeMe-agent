package com.example.meeting.agentgateway.audit;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.json.StoredJson;
import com.example.meeting.common.security.AgentToolContext;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.concurrent.locks.Lock;
import java.util.concurrent.locks.ReentrantLock;
import java.util.function.Supplier;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

@Service
public class AgentToolAuditService {

  private static final int LOCK_COUNT = 256;

  private final AgentToolAuditMapper mapper;
  private final ObjectMapper objectMapper;
  private final Clock clock;
  private final TransactionTemplate requiresNew;
  private final Lock[] locks = new Lock[LOCK_COUNT];

  public AgentToolAuditService(
      AgentToolAuditMapper mapper,
      ObjectMapper objectMapper,
      Clock clock,
      PlatformTransactionManager transactionManager) {
    this.mapper = mapper;
    this.objectMapper = objectMapper;
    this.clock = clock;
    this.requiresNew = new TransactionTemplate(transactionManager);
    this.requiresNew.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
    for (int index = 0; index < locks.length; index++) {
      locks[index] = new ReentrantLock();
    }
  }

  public <T> T execute(
      AgentToolContext context,
      String toolName,
      String riskLevel,
      Object request,
      Class<T> responseType,
      Supplier<T> action) {
    String requestHash = hashJson(request);
    Lock lock =
        locks[
            Math.floorMod(
                (context.runId() + '|' + context.toolCallId() + '|' + toolName).hashCode(),
                LOCK_COUNT)];
    lock.lock();
    try {
      AgentToolAuditRecord existing =
          requiresNew.execute(
              ignored -> mapper.find(context.runId(), context.toolCallId(), toolName).orElse(null));
      if (existing != null) {
        return replay(existing, requestHash, responseType);
      }
      AgentToolAuditRecord audit = new AgentToolAuditRecord();
      audit.setTraceId(context.traceId());
      audit.setRunId(context.runId());
      audit.setToolCallId(context.toolCallId());
      audit.setToolName(toolName);
      audit.setUserId(context.userId());
      audit.setRiskLevel(riskLevel);
      audit.setRequestHash(requestHash);
      audit.setResultCode("PROCESSING");
      audit.setDurationMs(0L);
      audit.setCreatedAt(LocalDateTime.now(clock));
      try {
        requiresNew.executeWithoutResult(ignored -> mapper.insert(audit));
      } catch (DuplicateKeyException exception) {
        AgentToolAuditRecord concurrent =
            requiresNew.execute(
                ignored ->
                    mapper
                        .find(context.runId(), context.toolCallId(), toolName)
                        .orElseThrow(() -> exception));
        return replay(concurrent, requestHash, responseType);
      }
      long started = System.nanoTime();
      try {
        T response = action.get();
        requiresNew.executeWithoutResult(
            ignored ->
                mapper.complete(
                    audit.getId(), "SUCCESS", writeJson(response), elapsedMillis(started)));
        return response;
      } catch (BusinessException exception) {
        requiresNew.executeWithoutResult(
            ignored ->
                mapper.complete(
                    audit.getId(), exception.errorCode().name(), null, elapsedMillis(started)));
        throw exception;
      } catch (RuntimeException exception) {
        requiresNew.executeWithoutResult(
            ignored ->
                mapper.complete(audit.getId(), "INTERNAL_ERROR", null, elapsedMillis(started)));
        throw exception;
      }
    } finally {
      lock.unlock();
    }
  }

  private <T> T replay(AgentToolAuditRecord audit, String requestHash, Class<T> responseType) {
    if (!MessageDigest.isEqual(
        audit.getRequestHash().getBytes(StandardCharsets.UTF_8),
        requestHash.getBytes(StandardCharsets.UTF_8))) {
      throw new BusinessException(ErrorCode.IDEMPOTENCY_KEY_REUSED);
    }
    if (!"SUCCESS".equals(audit.getResultCode()) || audit.getResponseJson() == null) {
      throw new BusinessException(ErrorCode.DEPENDENCY_UNAVAILABLE, "相同 Tool 调用仍在处理或上次未成功");
    }
    try {
      return StoredJson.read(objectMapper, audit.getResponseJson(), responseType);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Stored Tool response is invalid", exception);
    }
  }

  private String hashJson(Object value) {
    try {
      byte[] json = objectMapper.writeValueAsBytes(value);
      return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(json));
    } catch (JsonProcessingException | NoSuchAlgorithmException exception) {
      throw new IllegalStateException("Cannot hash Tool request", exception);
    }
  }

  private String writeJson(Object value) {
    try {
      return objectMapper.writeValueAsString(value);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Cannot persist Tool response", exception);
    }
  }

  private long elapsedMillis(long started) {
    return Math.max(0, (System.nanoTime() - started) / 1_000_000);
  }
}
