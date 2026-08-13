package com.example.meeting.knowledge.api;

import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.trace.TraceIds;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.knowledge.application.KnowledgeDocumentGateway;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/knowledge-documents")
public class KnowledgeDocumentController {

  private final KnowledgeDocumentGateway gateway;
  private final ApiResponseFactory responseFactory;

  public KnowledgeDocumentController(
      KnowledgeDocumentGateway gateway, ApiResponseFactory responseFactory) {
    this.gateway = gateway;
    this.responseFactory = responseFactory;
  }

  @GetMapping
  public ApiSuccess<KnowledgeDocumentListView> list(
      @RequestParam(required = false) String keyword,
      @RequestParam(required = false) String documentType,
      @RequestParam(defaultValue = "1") int page,
      @RequestParam(defaultValue = "20") int size,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(
        gateway.list(keyword, documentType, page, size, actor, TraceIds.from(request)), request);
  }

  @GetMapping("/{documentId}")
  public ApiSuccess<KnowledgeDocumentView> get(
      @PathVariable String documentId,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(gateway.get(documentId, actor, TraceIds.from(request)), request);
  }
}
