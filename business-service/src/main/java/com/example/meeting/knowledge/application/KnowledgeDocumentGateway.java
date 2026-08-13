package com.example.meeting.knowledge.application;

import com.example.meeting.agentgateway.client.AgentServiceProperties;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AgentContextTokenService;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.security.InternalSecurityProperties;
import com.example.meeting.knowledge.api.KnowledgeDocumentListView;
import com.example.meeting.knowledge.api.KnowledgeDocumentView;
import com.example.meeting.knowledge.api.UpdateKnowledgeDocumentRequest;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Base64;
import java.util.UUID;
import java.util.regex.Pattern;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

@Component
public class KnowledgeDocumentGateway {

  private static final int MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
  private static final Pattern DOCUMENT_ID = Pattern.compile("doc_[a-z0-9_]{1,60}");

  private final AgentServiceProperties properties;
  private final InternalSecurityProperties securityProperties;
  private final AgentContextTokenService tokenService;
  private final ObjectMapper objectMapper;
  private final HttpClient httpClient;

  public KnowledgeDocumentGateway(
      AgentServiceProperties properties,
      InternalSecurityProperties securityProperties,
      AgentContextTokenService tokenService,
      ObjectMapper objectMapper) {
    this.properties = properties;
    this.securityProperties = securityProperties;
    this.tokenService = tokenService;
    this.objectMapper = objectMapper;
    this.httpClient =
        HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(3))
            .version(HttpClient.Version.HTTP_1_1)
            .build();
  }

  public KnowledgeDocumentListView list(
      String keyword,
      String documentType,
      int page,
      int size,
      AuthenticatedUser actor,
      String traceId) {
    if (page < 1 || size < 1 || size > 100) {
      throw new BusinessException(ErrorCode.RAG_DOCUMENT_INVALID);
    }
    StringBuilder path =
        new StringBuilder("/internal/v1/knowledge-documents?page=")
            .append(page)
            .append("&size=")
            .append(size);
    appendQuery(path, "keyword", keyword);
    appendQuery(path, "documentType", documentType);
    return send(path.toString(), "GET", null, actor, traceId, KnowledgeDocumentListView.class);
  }

  public KnowledgeDocumentView get(String documentId, AuthenticatedUser actor, String traceId) {
    return send(
        "/internal/v1/knowledge-documents/" + validatedDocumentId(documentId),
        "GET",
        null,
        actor,
        traceId,
        KnowledgeDocumentView.class);
  }

  public KnowledgeDocumentView upload(
      MultipartFile file, String metadata, AuthenticatedUser actor, String traceId) {
    byte[] content = readUpload(file);
    String fileName = safeFileName(file.getOriginalFilename());
    String mediaType = mediaType(fileName);
    JsonNode metadataNode = null;
    if (metadata != null && !metadata.isBlank()) {
      try {
        metadataNode = objectMapper.readTree(metadata);
      } catch (JsonProcessingException exception) {
        throw new BusinessException(ErrorCode.RAG_DOCUMENT_INVALID);
      }
    }
    if (MediaType.APPLICATION_PDF_VALUE.equals(mediaType) && metadataNode == null) {
      throw new BusinessException(ErrorCode.RAG_DOCUMENT_INVALID);
    }
    UploadRequest body =
        new UploadRequest(
            fileName, mediaType, Base64.getEncoder().encodeToString(content), metadataNode);
    return send(
        "/internal/v1/knowledge-documents",
        "POST",
        body,
        actor,
        traceId,
        KnowledgeDocumentView.class);
  }

  public KnowledgeDocumentView update(
      String documentId,
      UpdateKnowledgeDocumentRequest body,
      AuthenticatedUser actor,
      String traceId) {
    return send(
        "/internal/v1/knowledge-documents/" + validatedDocumentId(documentId),
        "PUT",
        body,
        actor,
        traceId,
        KnowledgeDocumentView.class);
  }

  public DeleteResult delete(
      String documentId, int expectedVersion, AuthenticatedUser actor, String traceId) {
    if (expectedVersion < 0) {
      throw new BusinessException(ErrorCode.RAG_DOCUMENT_INVALID);
    }
    return send(
        "/internal/v1/knowledge-documents/"
            + validatedDocumentId(documentId)
            + "?expectedVersion="
            + expectedVersion,
        "DELETE",
        null,
        actor,
        traceId,
        DeleteResult.class);
  }

  private <T> T send(
      String path,
      String method,
      Object body,
      AuthenticatedUser actor,
      String traceId,
      Class<T> responseType) {
    String runId = "rag_" + UUID.randomUUID().toString().replace("-", "");
    String contextToken = tokenService.issue(actor, traceId, runId);
    try {
      HttpRequest.BodyPublisher publisher =
          body == null
              ? HttpRequest.BodyPublishers.noBody()
              : HttpRequest.BodyPublishers.ofString(
                  objectMapper.writeValueAsString(body), StandardCharsets.UTF_8);
      HttpRequest request =
          HttpRequest.newBuilder()
              .uri(URI.create(properties.url() + path))
              .timeout(Duration.ofSeconds(60))
              .header("Accept", MediaType.APPLICATION_JSON_VALUE)
              .header("Content-Type", MediaType.APPLICATION_JSON_VALUE)
              .header("Authorization", "Bearer " + contextToken)
              .header("X-Service-Token", securityProperties.serviceToken())
              .header("X-Trace-Id", traceId)
              .header("X-Run-Id", runId)
              .method(method, publisher)
              .build();
      HttpResponse<String> response =
          httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
      if (response.statusCode() < 200 || response.statusCode() >= 300) {
        throw mappedError(response.statusCode());
      }
      T parsed = objectMapper.readValue(response.body(), responseType);
      if (parsed == null) {
        throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
      }
      return parsed;
    } catch (BusinessException exception) {
      throw exception;
    } catch (IOException | IllegalArgumentException exception) {
      throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
    } catch (InterruptedException exception) {
      Thread.currentThread().interrupt();
      throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
    }
  }

  private BusinessException mappedError(int statusCode) {
    return switch (statusCode) {
      case 400, 422 -> new BusinessException(ErrorCode.RAG_DOCUMENT_INVALID);
      case 404 -> new BusinessException(ErrorCode.RAG_DOCUMENT_NOT_FOUND);
      case 409 -> new BusinessException(ErrorCode.RAG_DOCUMENT_CONFLICT);
      case 401, 403 -> new BusinessException(ErrorCode.FORBIDDEN);
      default -> new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
    };
  }

  private byte[] readUpload(MultipartFile file) {
    if (file.isEmpty() || file.getSize() > MAX_UPLOAD_BYTES) {
      throw new BusinessException(ErrorCode.RAG_DOCUMENT_INVALID);
    }
    try {
      byte[] content = file.getBytes();
      if (content.length == 0 || content.length > MAX_UPLOAD_BYTES) {
        throw new BusinessException(ErrorCode.RAG_DOCUMENT_INVALID);
      }
      return content;
    } catch (IOException exception) {
      throw new BusinessException(ErrorCode.RAG_DOCUMENT_INVALID);
    }
  }

  private String safeFileName(String value) {
    if (value == null
        || value.isBlank()
        || value.length() > 255
        || value.contains("/")
        || value.contains("\\")) {
      throw new BusinessException(ErrorCode.RAG_DOCUMENT_INVALID);
    }
    return value;
  }

  private String mediaType(String fileName) {
    String lower = fileName.toLowerCase(java.util.Locale.ROOT);
    if (lower.endsWith(".md")) {
      return "text/markdown";
    }
    if (lower.endsWith(".pdf")) {
      return MediaType.APPLICATION_PDF_VALUE;
    }
    throw new BusinessException(ErrorCode.RAG_DOCUMENT_INVALID);
  }

  private String validatedDocumentId(String value) {
    if (value == null || !DOCUMENT_ID.matcher(value).matches()) {
      throw new BusinessException(ErrorCode.RAG_DOCUMENT_INVALID);
    }
    return value;
  }

  private void appendQuery(StringBuilder path, String name, String value) {
    if (value != null && !value.isBlank()) {
      path.append('&')
          .append(name)
          .append('=')
          .append(URLEncoder.encode(value.trim(), StandardCharsets.UTF_8));
    }
  }

  private record UploadRequest(
      String fileName, String mediaType, String contentBase64, JsonNode metadata) {}

  public record DeleteResult(String documentId, String status, int recordVersion) {}
}
