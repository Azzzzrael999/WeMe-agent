package com.example.meeting.agentgateway.api;

import com.example.meeting.agentgateway.client.AgentSseProxyService;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.trace.TraceIds;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.fasterxml.jackson.databind.JsonNode;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Pattern;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@Validated
@RestController
@RequestMapping("/api/v1/agent/threads")
public class AgentThreadGatewayController {

  private final AgentSseProxyService proxyService;
  private final ApiResponseFactory responseFactory;

  public AgentThreadGatewayController(
      AgentSseProxyService proxyService, ApiResponseFactory responseFactory) {
    this.proxyService = proxyService;
    this.responseFactory = responseFactory;
  }

  @GetMapping
  public ResponseEntity<ApiSuccess<JsonNode>> list(
      @RequestParam(defaultValue = "1") @Min(1) int page,
      @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size,
      @RequestParam(required = false) @Pattern(regexp = "[A-Z_]{1,32}") String status,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    JsonNode data = proxyService.listThreads(page, size, status, actor, TraceIds.from(request));
    return noStore(data, request);
  }

  @GetMapping("/{threadId}")
  public ResponseEntity<ApiSuccess<JsonNode>> detail(
      @PathVariable @Pattern(regexp = "[A-Za-z0-9_-]{1,64}") String threadId,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    JsonNode data = proxyService.getThread(threadId, actor, TraceIds.from(request));
    return noStore(data, request);
  }

  private ResponseEntity<ApiSuccess<JsonNode>> noStore(JsonNode data, HttpServletRequest request) {
    return ResponseEntity.ok()
        .cacheControl(CacheControl.noStore())
        .body(responseFactory.success(data, request));
  }
}
