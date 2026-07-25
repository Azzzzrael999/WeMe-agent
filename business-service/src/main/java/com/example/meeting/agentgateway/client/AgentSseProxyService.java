package com.example.meeting.agentgateway.client;

import com.example.meeting.agentgateway.api.AgentRunStreamRequest;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AgentContextTokenService;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.security.InternalSecurityProperties;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;

@Service
public class AgentSseProxyService {

  private final AgentServiceProperties properties;
  private final InternalSecurityProperties securityProperties;
  private final AgentContextTokenService tokenService;
  private final ObjectMapper objectMapper;
  private final HttpClient httpClient;

  public AgentSseProxyService(
      AgentServiceProperties properties,
      InternalSecurityProperties securityProperties,
      AgentContextTokenService tokenService,
      ObjectMapper objectMapper) {
    this.properties = properties;
    this.securityProperties = securityProperties;
    this.tokenService = tokenService;
    this.objectMapper = objectMapper;
    // Uvicorn only serves the internal SSE endpoint over HTTP/1.1.  The JDK client otherwise
    // attempts a clear-text h2c upgrade, which Uvicorn rejects before FastAPI can authenticate it.
    this.httpClient =
        HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(3))
            .version(HttpClient.Version.HTTP_1_1)
            .build();
  }

  public UpstreamStream open(AgentRunStreamRequest body, AuthenticatedUser actor, String traceId) {
    String runId = "run_" + UUID.randomUUID().toString().replace("-", "");
    String contextToken = tokenService.issue(actor, traceId, runId);
    try {
      HttpRequest request =
          HttpRequest.newBuilder()
              .uri(URI.create(properties.url() + "/internal/v1/agent-runs/stream"))
              .timeout(Duration.ofMinutes(30))
              .header("Content-Type", "application/json")
              .header("Accept", "text/event-stream")
              .header("Authorization", "Bearer " + contextToken)
              .header("X-Service-Token", securityProperties.serviceToken())
              .header("X-Trace-Id", traceId)
              .header("X-Run-Id", runId)
              .POST(HttpRequest.BodyPublishers.ofString(serialize(body), StandardCharsets.UTF_8))
              .build();
      HttpResponse<InputStream> response =
          httpClient.send(request, HttpResponse.BodyHandlers.ofInputStream());
      MediaType contentType = sseContentType(response);
      if (response.statusCode() < 200 || response.statusCode() >= 300 || contentType == null) {
        closeQuietly(response.body());
        throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
      }
      return new UpstreamStream(runId, contentType, response.body());
    } catch (BusinessException exception) {
      throw exception;
    } catch (IOException | IllegalArgumentException exception) {
      throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
    } catch (InterruptedException exception) {
      Thread.currentThread().interrupt();
      throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
    }
  }

  private MediaType sseContentType(HttpResponse<?> response) {
    String value = response.headers().firstValue("Content-Type").orElse(null);
    if (value == null) {
      return null;
    }
    try {
      MediaType contentType = MediaType.parseMediaType(value);
      return contentType.isCompatibleWith(MediaType.TEXT_EVENT_STREAM) ? contentType : null;
    } catch (IllegalArgumentException exception) {
      return null;
    }
  }

  private void closeQuietly(InputStream body) {
    try {
      body.close();
    } catch (IOException ignored) {
      // The original upstream error still maps to the stable public error code.
    }
  }

  private String serialize(Object value) {
    try {
      return objectMapper.writeValueAsString(value);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Cannot serialize Agent stream request", exception);
    }
  }

  public record UpstreamStream(String runId, MediaType contentType, InputStream body) {}
}
