package com.example.meeting.agentgateway.api;

import com.example.meeting.agentgateway.client.AgentSseProxyService;
import com.example.meeting.agentgateway.client.AgentSseProxyService.UpstreamStream;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.trace.TraceIds;
import com.example.meeting.common.web.ApiError;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import java.io.IOException;
import java.io.InputStream;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.CacheControl;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

@RestController
@RequestMapping("/api/v1/agent/runs")
public class AgentGatewayController {

  private static final Logger LOGGER = LoggerFactory.getLogger(AgentGatewayController.class);

  private final AgentSseProxyService proxyService;
  private final ObjectMapper objectMapper;
  private final ApiResponseFactory responseFactory;

  public AgentGatewayController(
      AgentSseProxyService proxyService,
      ObjectMapper objectMapper,
      ApiResponseFactory responseFactory) {
    this.proxyService = proxyService;
    this.objectMapper = objectMapper;
    this.responseFactory = responseFactory;
  }

  @PostMapping("/stream")
  public StreamingResponseBody stream(
      @Valid @RequestBody AgentRunStreamRequest body,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request,
      HttpServletResponse response)
      throws IOException {
    String traceId = TraceIds.from(request);
    UpstreamStream upstream;
    try {
      upstream = proxyService.open(body, actor, traceId);
    } catch (BusinessException exception) {
      writeError(response, exception, traceId);
      return null;
    }
    return relay(upstream, response);
  }

  @PostMapping("/{runId}/resume")
  public StreamingResponseBody resume(
      @PathVariable @jakarta.validation.constraints.Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String runId,
      @Valid @RequestBody AgentRunResumeRequest body,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request,
      HttpServletResponse response)
      throws IOException {
    String traceId = TraceIds.from(request);
    UpstreamStream upstream;
    try {
      upstream = proxyService.resume(runId, body, actor, traceId);
    } catch (BusinessException exception) {
      writeError(response, exception, traceId);
      return null;
    }
    return relay(upstream, response);
  }

  @GetMapping("/{runId}")
  public ResponseEntity<ApiSuccess<JsonNode>> getRun(
      @PathVariable @jakarta.validation.constraints.Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String runId,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    JsonNode run = proxyService.getRun(runId, actor, TraceIds.from(request));
    return noStore(run, request);
  }

  @GetMapping("/{runId}/trace")
  public ResponseEntity<ApiSuccess<JsonNode>> getTrace(
      @PathVariable @jakarta.validation.constraints.Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String runId,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    JsonNode trace = proxyService.getTrace(runId, actor, TraceIds.from(request));
    return noStore(trace, request);
  }

  private StreamingResponseBody relay(UpstreamStream upstream, HttpServletResponse response) {
    response.setStatus(HttpServletResponse.SC_OK);
    response.setContentType(upstream.contentType().toString());
    response.setHeader("Cache-Control", "no-cache");
    response.setHeader("X-Run-Id", upstream.runId());
    return output -> {
      try (InputStream input = upstream.body()) {
        input.transferTo(output);
        output.flush();
      } catch (IOException exception) {
        LOGGER.debug("Agent SSE stream closed before completion");
        throw exception;
      }
    };
  }

  private ResponseEntity<ApiSuccess<JsonNode>> noStore(JsonNode data, HttpServletRequest request) {
    return ResponseEntity.ok()
        .cacheControl(CacheControl.noStore())
        .body(responseFactory.success(data, request));
  }

  private void writeError(HttpServletResponse response, BusinessException exception, String traceId)
      throws IOException {
    response.setStatus(exception.errorCode().status().value());
    response.setContentType(MediaType.APPLICATION_JSON_VALUE);
    objectMapper.writeValue(
        response.getOutputStream(),
        new ApiError(
            exception.errorCode().name(), exception.getMessage(), exception.details(), traceId));
  }
}
