package com.example.meeting.agentgateway.client;

import com.example.meeting.common.json.StoredJson;
import com.example.meeting.common.security.InternalSecurityProperties;
import com.example.meeting.mq.BookingResultPayload;
import com.example.meeting.outbox.EventEnvelope;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import org.springframework.stereotype.Service;

@Service
public class AgentBusinessResultCallback {

  private final AgentServiceProperties properties;
  private final InternalSecurityProperties securityProperties;
  private final ObjectMapper objectMapper;
  private final HttpClient httpClient;

  public AgentBusinessResultCallback(
      AgentServiceProperties properties,
      InternalSecurityProperties securityProperties,
      ObjectMapper objectMapper) {
    this.properties = properties;
    this.securityProperties = securityProperties;
    this.objectMapper = objectMapper;
    this.httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
  }

  public void deliver(String eventJson) {
    if (!properties.callbackEnabled()) {
      return;
    }
    EventEnvelope event = read(eventJson, EventEnvelope.class);
    BookingResultPayload payload =
        objectMapper.convertValue(event.payload(), BookingResultPayload.class);
    BusinessResultCallbackRequest body =
        new BusinessResultCallbackRequest(
            event.eventId(),
            payload.requestNo(),
            payload.status(),
            payload.meetingId(),
            payload.conflict());
    HttpRequest request =
        HttpRequest.newBuilder()
            .uri(
                URI.create(
                    properties.url()
                        + "/internal/v1/agent-runs/"
                        + event.runId()
                        + "/business-result"))
            .timeout(Duration.ofSeconds(5))
            .header("Content-Type", "application/json")
            .header("X-Service-Token", securityProperties.serviceToken())
            .POST(HttpRequest.BodyPublishers.ofString(write(body), StandardCharsets.UTF_8))
            .build();
    try {
      HttpResponse<Void> response =
          httpClient.send(request, HttpResponse.BodyHandlers.discarding());
      if (response.statusCode() < 200 || response.statusCode() >= 300) {
        throw new IllegalStateException("Agent business result callback was rejected");
      }
    } catch (IOException exception) {
      throw new IllegalStateException("Agent business result callback is unavailable", exception);
    } catch (InterruptedException exception) {
      Thread.currentThread().interrupt();
      throw new IllegalStateException("Agent business result callback was interrupted", exception);
    }
  }

  private <T> T read(String json, Class<T> type) {
    try {
      return StoredJson.read(objectMapper, json, type);
    } catch (JsonProcessingException exception) {
      throw new IllegalArgumentException("BOOKING_RESULT event is invalid", exception);
    }
  }

  private String write(Object value) {
    try {
      return objectMapper.writeValueAsString(value);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Cannot serialize business result callback", exception);
    }
  }

  public record BusinessResultCallbackRequest(
      String eventId,
      String requestNo,
      String status,
      Long meetingId,
      BookingResultPayload.ConflictView conflict) {}
}
