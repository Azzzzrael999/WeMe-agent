package com.example.meeting.meeting.lifecycle.client;

import com.example.meeting.agentgateway.client.AgentServiceProperties;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AgentContextTokenService;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.security.InternalSecurityProperties;
import com.example.meeting.meeting.api.MeetingParticipantView;
import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.lifecycle.api.PostMeetingDraftContent;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;

@Component
public class PostMeetingAgentClient {

  private final AgentServiceProperties properties;
  private final InternalSecurityProperties securityProperties;
  private final AgentContextTokenService tokenService;
  private final ObjectMapper objectMapper;
  private final HttpClient httpClient;

  public PostMeetingAgentClient(
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

  public AgentDraftResponse generate(
      MeetingView meeting,
      String transcript,
      AuthenticatedUser actor,
      String traceId,
      String runId) {
    String contextToken = tokenService.issue(actor, traceId, runId);
    AgentDraftRequest body =
        new AgentDraftRequest(
            meeting.id(),
            meeting.title(),
            meeting.meetingType(),
            meeting.startAt(),
            meeting.endAt(),
            meeting.participants().stream().map(ParticipantSnapshot::from).toList(),
            transcript);
    try {
      HttpRequest request =
          HttpRequest.newBuilder()
              .uri(URI.create(properties.url() + "/internal/v1/post-meeting/drafts"))
              .timeout(Duration.ofSeconds(45))
              .header("Content-Type", MediaType.APPLICATION_JSON_VALUE)
              .header("Accept", MediaType.APPLICATION_JSON_VALUE)
              .header("Authorization", "Bearer " + contextToken)
              .header("X-Service-Token", securityProperties.serviceToken())
              .header("X-Trace-Id", traceId)
              .header("X-Run-Id", runId)
              .POST(
                  HttpRequest.BodyPublishers.ofString(
                      objectMapper.writeValueAsString(body), StandardCharsets.UTF_8))
              .build();
      HttpResponse<String> response =
          httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
      if (response.statusCode() < 200 || response.statusCode() >= 300) {
        throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
      }
      AgentDraftResponse parsed = objectMapper.readValue(response.body(), AgentDraftResponse.class);
      if (parsed == null
          || parsed.agentRunId() == null
          || !parsed.agentRunId().equals(runId)
          || parsed.model() == null
          || parsed.model().isBlank()
          || parsed.promptVersion() == null
          || parsed.promptVersion().isBlank()
          || parsed.schemaVersion() == null
          || parsed.schemaVersion().isBlank()
          || parsed.draft() == null) {
        throw new AgentOutputException("Agent response metadata is invalid");
      }
      return parsed;
    } catch (BusinessException | AgentOutputException exception) {
      throw exception;
    } catch (JsonProcessingException exception) {
      throw new AgentOutputException("Agent response is invalid JSON", exception);
    } catch (IOException | IllegalArgumentException exception) {
      throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
    } catch (InterruptedException exception) {
      Thread.currentThread().interrupt();
      throw new BusinessException(ErrorCode.AGENT_UNAVAILABLE);
    }
  }

  private record AgentDraftRequest(
      long meetingId,
      String title,
      String meetingType,
      java.time.OffsetDateTime startAt,
      java.time.OffsetDateTime endAt,
      List<ParticipantSnapshot> participants,
      String transcript) {}

  private record ParticipantSnapshot(long employeeId, String displayName) {
    private static ParticipantSnapshot from(MeetingParticipantView participant) {
      return new ParticipantSnapshot(participant.employeeId(), participant.displayName());
    }
  }

  public record AgentDraftResponse(
      String agentRunId,
      String model,
      String promptVersion,
      String schemaVersion,
      PostMeetingDraftContent draft) {}

  public static final class AgentOutputException extends RuntimeException {
    public AgentOutputException(String message) {
      super(message);
    }

    public AgentOutputException(String message, Throwable cause) {
      super(message, cause);
    }
  }
}
