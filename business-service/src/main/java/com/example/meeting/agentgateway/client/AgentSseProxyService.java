package com.example.meeting.agentgateway.client;

import com.example.meeting.agentgateway.api.AgentRunResumeRequest;
import com.example.meeting.agentgateway.api.AgentRunStreamRequest;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AgentContextTokenService;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.security.InternalSecurityProperties;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;

@Service
public class AgentSseProxyService {

  private static final Set<String> INTERNAL_RESPONSE_FIELDS =
      Set.of(
          "authorization",
          "access_token",
          "accesstoken",
          "agent_context_token",
          "agentcontexttoken",
          "jwt",
          "service_token",
          "servicetoken",
          "x-service-token");

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
    return open("/internal/v1/agent-runs/stream", runId, body, actor, traceId);
  }

  public UpstreamStream resume(
      String runId, AgentRunResumeRequest body, AuthenticatedUser actor, String traceId) {
    return open("/internal/v1/agent-runs/" + runId + "/resume", runId, body, actor, traceId);
  }

  public JsonNode getRun(String runId, AuthenticatedUser actor, String traceId) {
    return get("/internal/v1/agent-runs/" + runId, runId, actor, traceId, false);
  }

  public JsonNode getTrace(String runId, AuthenticatedUser actor, String traceId) {
    return get("/internal/v1/agent-runs/" + runId + "/trace", runId, actor, traceId, true);
  }

  private UpstreamStream open(
      String path, String runId, Object body, AuthenticatedUser actor, String traceId) {
    String contextToken = tokenService.issue(actor, traceId, runId);
    try {
      HttpRequest request =
          HttpRequest.newBuilder()
              .uri(URI.create(properties.url() + path))
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

  private JsonNode get(
      String path,
      String runId,
      AuthenticatedUser actor,
      String traceId,
      boolean omitConfirmationToken) {
    String contextToken = tokenService.issue(actor, traceId, runId);
    try {
      HttpRequest request =
          HttpRequest.newBuilder()
              .uri(URI.create(properties.url() + path))
              .timeout(Duration.ofSeconds(10))
              .header("Accept", MediaType.APPLICATION_JSON_VALUE)
              .header("Authorization", "Bearer " + contextToken)
              .header("X-Service-Token", securityProperties.serviceToken())
              .header("X-Trace-Id", traceId)
              .header("X-Run-Id", runId)
              .GET()
              .build();
      HttpResponse<String> response =
          httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
      if (response.statusCode() < 200 || response.statusCode() >= 300) {
        throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
      }
      return sanitize(readJson(response.body()), omitConfirmationToken);
    } catch (BusinessException exception) {
      throw exception;
    } catch (IOException | IllegalArgumentException exception) {
      throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
    } catch (InterruptedException exception) {
      Thread.currentThread().interrupt();
      throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
    }
  }

  private JsonNode readJson(String value) {
    try {
      JsonNode response = objectMapper.readTree(value);
      if (response == null || !response.isObject()) {
        throw new IllegalArgumentException("Agent recovery response must be a JSON object");
      }
      return response;
    } catch (JsonProcessingException exception) {
      throw new IllegalArgumentException("Agent recovery response is invalid JSON", exception);
    }
  }

  private JsonNode sanitize(JsonNode response, boolean omitConfirmationToken) {
    JsonNode copy = response.deepCopy();
    scrubInternalFields(copy, omitConfirmationToken);
    return copy;
  }

  private void scrubInternalFields(JsonNode node, boolean omitConfirmationToken) {
    if (node instanceof ObjectNode objectNode) {
      List<String> names = new ArrayList<>();
      objectNode.fieldNames().forEachRemaining(names::add);
      for (String name : names) {
        String normalized = name.toLowerCase(Locale.ROOT);
        if (INTERNAL_RESPONSE_FIELDS.contains(normalized)
            || (omitConfirmationToken && "confirmationtoken".equals(normalized))) {
          objectNode.remove(name);
        } else {
          scrubInternalFields(objectNode.get(name), omitConfirmationToken);
        }
      }
    } else if (node instanceof ArrayNode arrayNode) {
      for (JsonNode element : arrayNode) {
        scrubInternalFields(element, omitConfirmationToken);
      }
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
