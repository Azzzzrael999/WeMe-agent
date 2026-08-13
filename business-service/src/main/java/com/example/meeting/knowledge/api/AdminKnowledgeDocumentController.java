package com.example.meeting.knowledge.api;

import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.trace.TraceIds;
import com.example.meeting.common.web.ApiResponseFactory;
import com.example.meeting.common.web.ApiSuccess;
import com.example.meeting.knowledge.application.KnowledgeDocumentGateway;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api/v1/admin/knowledge-documents")
public class AdminKnowledgeDocumentController {

  private final KnowledgeDocumentGateway gateway;
  private final ApiResponseFactory responseFactory;

  public AdminKnowledgeDocumentController(
      KnowledgeDocumentGateway gateway, ApiResponseFactory responseFactory) {
    this.gateway = gateway;
    this.responseFactory = responseFactory;
  }

  @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
  public ApiSuccess<KnowledgeDocumentView> upload(
      @RequestPart("file") MultipartFile file,
      @RequestPart(value = "metadata", required = false) String metadata,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(
        gateway.upload(file, metadata, actor, TraceIds.from(request)), request);
  }

  @PutMapping(path = "/{documentId}", consumes = MediaType.APPLICATION_JSON_VALUE)
  public ApiSuccess<KnowledgeDocumentView> update(
      @PathVariable String documentId,
      @Valid @RequestBody UpdateKnowledgeDocumentRequest body,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(
        gateway.update(documentId, body, actor, TraceIds.from(request)), request);
  }

  @DeleteMapping("/{documentId}")
  public ApiSuccess<KnowledgeDocumentGateway.DeleteResult> delete(
      @PathVariable String documentId,
      @RequestParam int expectedVersion,
      @AuthenticationPrincipal AuthenticatedUser actor,
      HttpServletRequest request) {
    return responseFactory.success(
        gateway.delete(documentId, expectedVersion, actor, TraceIds.from(request)), request);
  }
}
