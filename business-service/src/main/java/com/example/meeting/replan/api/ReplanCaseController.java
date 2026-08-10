package com.example.meeting.replan.api;

import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.replan.application.ReplanCaseApplicationService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/replan-cases")
public class ReplanCaseController {

  private final ReplanCaseApplicationService service;
  private final ApiResponseFactory responseFactory;

  public ReplanCaseController(
      ReplanCaseApplicationService service, ApiResponseFactory responseFactory) {
    this.service = service;
    this.responseFactory = responseFactory;
  }

  @GetMapping
  public ApiSuccess<ReplanCaseListView> list(
      @RequestParam(required = false) String status,
      @RequestParam(defaultValue = "1") int page,
      @RequestParam(defaultValue = "20") int size,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(service.list(status, page, size, actor), request);
  }

  @GetMapping("/{caseId}")
  public ApiSuccess<ReplanCaseView> get(
      @PathVariable long caseId,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(service.get(caseId, actor), request);
  }

  @GetMapping("/{caseId}/alternatives")
  public ApiSuccess<ReplanAlternativesView> alternatives(
      @PathVariable long caseId,
      @RequestParam(defaultValue = "3") int limit,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(service.alternatives(caseId, limit, actor), request);
  }

  @PostMapping("/{caseId}/resolve")
  public ApiSuccess<ReplanCaseView> resolve(
      @PathVariable long caseId,
      @Valid @RequestBody ResolveReplanCaseRequest body,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(service.resolve(caseId, body, actor), request);
  }
}
