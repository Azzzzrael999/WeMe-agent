package com.example.meeting.agentgateway.mcp;

import com.example.meeting.agentgateway.application.AgentToolApplicationService;
import com.example.meeting.agentgateway.internal.AgentToolDtos.*;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AgentToolContext;
import com.example.meeting.meeting.api.CreateMeetingRequest;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.modelcontextprotocol.common.McpTransportContext;
import io.modelcontextprotocol.json.jackson2.JacksonMcpJsonMapper;
import io.modelcontextprotocol.json.schema.jackson2.DefaultJsonSchemaValidator;
import io.modelcontextprotocol.server.McpServer;
import io.modelcontextprotocol.server.McpStatelessServerFeatures;
import io.modelcontextprotocol.server.McpStatelessSyncServer;
import io.modelcontextprotocol.server.transport.HttpServletStatelessServerTransport;
import io.modelcontextprotocol.spec.McpSchema;
import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.web.servlet.ServletRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.ClassPathResource;

/** Protocol adapter only. All business execution and auditing stay in the application service. */
@Configuration
@ConditionalOnProperty(name = "app.mcp.enabled", havingValue = "true", matchIfMissing = true)
public class AgentMcpConfiguration {
  public static final String ENDPOINT = "/internal/v1/mcp";
  private static final String IDEMPOTENCY = "idempotencyKey";
  private final AgentToolApplicationService application;
  private final ObjectMapper mapper;
  private final Map<String, Map<String, Object>> catalog;
  private final DefaultJsonSchemaValidator schemaValidator;

  public AgentMcpConfiguration(AgentToolApplicationService application, ObjectMapper mapper)
      throws IOException {
    this.application = application;
    this.mapper = mapper.copy().enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);
    this.schemaValidator = new DefaultJsonSchemaValidator(this.mapper);
    try (var input = new ClassPathResource("mcp/tool-catalog.json").getInputStream()) {
      this.catalog = mapper.readValue(input, new TypeReference<>() {});
    }
  }

  @Bean
  HttpServletStatelessServerTransport agentMcpTransport() {
    return HttpServletStatelessServerTransport.builder()
        .jsonMapper(new JacksonMcpJsonMapper(mapper))
        .messageEndpoint(ENDPOINT)
        .contextExtractor(
            request -> {
              Object context = request.getAttribute(AgentToolContext.REQUEST_ATTRIBUTE);
              if (!(context instanceof AgentToolContext)) {
                throw new BusinessException(ErrorCode.AGENT_CONTEXT_INVALID);
              }
              Map<String, Object> metadata = new LinkedHashMap<>();
              metadata.put(AgentToolContext.REQUEST_ATTRIBUTE, context);
              String key = request.getHeader("Idempotency-Key");
              if (key != null) metadata.put(IDEMPOTENCY, key);
              return McpTransportContext.create(Map.copyOf(metadata));
            })
        .build();
  }

  @Bean(destroyMethod = "closeGracefully")
  McpStatelessSyncServer agentMcpServer(HttpServletStatelessServerTransport agentMcpTransport) {
    List<McpStatelessServerFeatures.SyncToolSpecification> tools = new ArrayList<>();
    catalog.forEach(
        (name, schema) ->
            tools.add(
                McpStatelessServerFeatures.SyncToolSpecification.builder()
                    .tool(
                        McpSchema.Tool.builder(name)
                            .description((String) schema.get("description"))
                            .inputSchema(map(schema.get("inputSchema")))
                            .outputSchema(map(schema.get("outputSchema")))
                            .annotations(
                                McpSchema.ToolAnnotations.builder()
                                    .readOnlyHint("READ".equals(schema.get("riskLevel")))
                                    .openWorldHint(false)
                                    .build())
                            .build())
                    .callHandler((context, request) -> call(name, request.arguments(), context))
                    .build()));
    return McpServer.sync(agentMcpTransport)
        .serverInfo("meeting-business-tools", "1.0.0")
        .jsonMapper(new JacksonMcpJsonMapper(mapper))
        .capabilities(McpSchema.ServerCapabilities.builder().tools(false).build())
        // DTO validation below also handles defaults and produces our structured error contract.
        .validateToolInputs(false)
        .tools(tools)
        .build();
  }

  @Bean
  ServletRegistrationBean<HttpServletStatelessServerTransport> agentMcpServlet(
      HttpServletStatelessServerTransport agentMcpTransport,
      McpStatelessSyncServer agentMcpServer) {
    var registration = new ServletRegistrationBean<>(agentMcpTransport, ENDPOINT);
    registration.setName("agentMcpServlet");
    registration.setAsyncSupported(true);
    registration.setLoadOnStartup(1);
    return registration;
  }

  private McpSchema.CallToolResult call(
      String name, Map<String, Object> arguments, McpTransportContext metadata) {
    Map<String, Object> envelope;
    boolean error = false;
    try {
      AgentToolContext context =
          (AgentToolContext) metadata.get(AgentToolContext.REQUEST_ATTRIBUTE);
      if (context == null) throw new BusinessException(ErrorCode.AGENT_CONTEXT_INVALID);
      String key = (String) metadata.get(IDEMPOTENCY);
      Object result =
          switch (name) {
            case "resolve_employees" ->
                application.resolveEmployees(
                    decode(name, arguments, ResolveEmployeesRequest.class), context);
            case "resolve_participant_scope" ->
                application.resolveParticipantScope(
                    decode(name, arguments, ResolveParticipantScopeRequest.class), context);
            case "get_employee_free_busy" ->
                application.freeBusy(decode(name, arguments, FreeBusyRequest.class), context);
            case "search_available_rooms" ->
                application.searchRooms(decode(name, arguments, SearchRoomsRequest.class), context);
            case "get_recent_meeting" ->
                application.recentMeeting(
                    decode(name, arguments, RecentMeetingRequest.class), context);
            case "create_booking_draft" ->
                application.createDraft(
                    decode(name, arguments, CreateMeetingRequest.class), context);
            case "create_reschedule_draft" ->
                application.createRescheduleDraft(
                    decode(name, arguments, RescheduleDraftRequest.class), context);
            case "create_cancellation_preview" ->
                application.createCancellationPreview(
                    decode(name, arguments, CancellationPreviewRequest.class), context);
            case "confirm_booking", "confirm_reschedule", "confirm_cancellation" -> {
              ConfirmArguments command = decode(name, arguments, ConfirmArguments.class);
              yield switch (name) {
                case "confirm_booking" ->
                    application.confirmDraft(command.confirmationToken(), key, context);
                case "confirm_reschedule" ->
                    application.confirmRescheduleDraft(command.confirmationToken(), key, context);
                default ->
                    application.confirmCancellationPreview(
                        command.confirmationToken(), key, context);
              };
            }
            default -> throw new BusinessException(ErrorCode.TOOL_NOT_ALLOWED);
          };
      envelope = Map.of("ok", true, "data", result);
    } catch (BusinessException exception) {
      error = true;
      envelope =
          Map.of(
              "ok",
              false,
              "error",
              Map.of(
                  "code",
                  exception.errorCode().name(),
                  "message",
                  exception.errorCode().defaultMessage(),
                  "details",
                  exception.details()));
    } catch (IllegalArgumentException exception) {
      error = true;
      envelope = failure(ErrorCode.VALIDATION_ERROR);
    } catch (RuntimeException exception) {
      error = true;
      envelope = failure(ErrorCode.INTERNAL_ERROR);
    }
    try {
      String text = mapper.writeValueAsString(envelope);
      Map<String, Object> structured = mapper.readValue(text, new TypeReference<>() {});
      return McpSchema.CallToolResult.builder()
          .structuredContent(structured)
          .content(List.of(new McpSchema.TextContent(text)))
          .isError(error)
          .build();
    } catch (IOException exception) {
      throw new IllegalStateException("Cannot serialize MCP business result", exception);
    }
  }

  private Map<String, Object> failure(ErrorCode code) {
    return Map.of(
        "ok",
        false,
        "error",
        Map.of("code", code.name(), "message", code.defaultMessage(), "details", List.of()));
  }

  private <T> T decode(String name, Map<String, Object> arguments, Class<T> type) {
    Map<String, Object> schema = map(catalog.get(name).get("inputSchema"));
    Map<String, Object> properties = map(schema.get("properties"));
    Map<String, Object> normalized = new LinkedHashMap<>(arguments == null ? Map.of() : arguments);
    if (!properties.keySet().containsAll(normalized.keySet())) {
      throw new BusinessException(ErrorCode.VALIDATION_ERROR);
    }
    properties.forEach(
        (field, definition) -> {
          Map<String, Object> property = map(definition);
          if (!normalized.containsKey(field) && property.containsKey("default")) {
            normalized.put(field, property.get("default"));
          }
        });
    // default_factory arrays are normalized explicitly, not only documented in JSON Schema.
    for (String field : List.of("departmentNames", "requiredFeatures", "optionalParticipantIds")) {
      if (properties.containsKey(field) && !normalized.containsKey(field)) {
        normalized.put(field, List.of());
      }
    }
    Object required = schema.get("required");
    if (required instanceof List<?> fields && !normalized.keySet().containsAll(fields)) {
      throw new BusinessException(ErrorCode.VALIDATION_ERROR);
    }
    if (!schemaValidator.validate(schema, normalized).valid()) {
      throw new BusinessException(ErrorCode.VALIDATION_ERROR);
    }
    return mapper.convertValue(normalized, type);
  }

  private Map<String, Object> map(Object value) {
    return mapper.convertValue(value, new TypeReference<>() {});
  }

  public record ConfirmArguments(String confirmationToken) {}
}
