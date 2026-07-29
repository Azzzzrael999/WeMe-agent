package com.example.meeting.agentgateway.client;

import com.example.meeting.auth.infrastructure.UserMapper;
import com.example.meeting.auth.infrastructure.UserProfileRow;
import com.example.meeting.booking.domain.BookingRequestRecord;
import com.example.meeting.booking.infrastructure.BookingRequestMapper;
import com.example.meeting.common.json.StoredJson;
import com.example.meeting.common.security.AgentContextTokenService;
import com.example.meeting.common.security.AuthenticatedUser;
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
  private final AgentContextTokenService tokenService;
  private final BookingRequestMapper bookingRequestMapper;
  private final UserMapper userMapper;
  private final ObjectMapper objectMapper;
  private final HttpClient httpClient;

  public AgentBusinessResultCallback(
      AgentServiceProperties properties,
      InternalSecurityProperties securityProperties,
      AgentContextTokenService tokenService,
      BookingRequestMapper bookingRequestMapper,
      UserMapper userMapper,
      ObjectMapper objectMapper) {
    this.properties = properties;
    this.securityProperties = securityProperties;
    this.tokenService = tokenService;
    this.bookingRequestMapper = bookingRequestMapper;
    this.userMapper = userMapper;
    this.objectMapper = objectMapper;
    this.httpClient =
        HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(3))
            .version(HttpClient.Version.HTTP_1_1)
            .build();
  }

  public void deliver(String eventJson) {
    if (!properties.callbackEnabled()) {
      return;
    }
    EventEnvelope event = read(eventJson, EventEnvelope.class);
    BookingResultPayload payload =
        objectMapper.convertValue(event.payload(), BookingResultPayload.class);
    CallbackContext context = resolveContext(event, payload);
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
                        + context.runId()
                        + "/business-result"))
            .timeout(Duration.ofSeconds(5))
            .header("Content-Type", "application/json")
            .header("Authorization", "Bearer " + context.agentContextToken())
            .header("X-Service-Token", securityProperties.serviceToken())
            .header("X-Trace-Id", context.traceId())
            .header("X-Run-Id", context.runId())
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

  private CallbackContext resolveContext(EventEnvelope event, BookingResultPayload payload) {
    String requestNo = requireText(payload.requestNo(), "requestNo");
    BookingRequestRecord request =
        bookingRequestMapper
            .findByRequestNo(requestNo)
            .orElseThrow(
                () -> new IllegalArgumentException("Booking request for callback is missing"));
    String runId = requireEqual(event.runId(), request.getRunId(), "runId");
    String traceId = requireEqual(event.traceId(), request.getTraceId(), "traceId");
    UserProfileRow owner =
        userMapper
            .findProfileById(request.getUserId())
            .orElseThrow(() -> new IllegalArgumentException("Booking request owner is missing"));
    String role = requireText(owner.getRole(), "owner role");
    AuthenticatedUser user =
        new AuthenticatedUser(
            request.getUserId(),
            requireText(owner.getUsername(), "owner username"),
            java.util.List.of(role));
    return new CallbackContext(runId, traceId, tokenService.issue(user, traceId, runId));
  }

  private String requireEqual(String eventValue, String persistedValue, String name) {
    String eventText = requireText(eventValue, "event " + name);
    String persistedText = requireText(persistedValue, "booking request " + name);
    if (!eventText.equals(persistedText)) {
      throw new IllegalArgumentException(
          "BOOKING_RESULT callback " + name + " does not match request");
    }
    return eventText;
  }

  private String requireText(String value, String name) {
    if (value == null || value.isBlank()) {
      throw new IllegalArgumentException(name + " is missing");
    }
    return value;
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

  private record CallbackContext(String runId, String traceId, String agentContextToken) {}
}
