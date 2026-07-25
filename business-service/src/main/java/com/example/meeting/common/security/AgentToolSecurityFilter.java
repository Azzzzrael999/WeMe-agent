package com.example.meeting.common.security;

import com.example.meeting.auth.domain.UserAccount;
import com.example.meeting.auth.infrastructure.UserMapper;
import com.example.meeting.common.error.ErrorCode;
import io.jsonwebtoken.JwtException;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class AgentToolSecurityFilter extends OncePerRequestFilter {

  private static final String INTERNAL_TOOL_PREFIX = "/internal/v1/tools/";
  private static final String BEARER_PREFIX = "Bearer ";

  private final InternalSecurityProperties properties;
  private final AgentContextTokenService tokenService;
  private final UserMapper userMapper;
  private final SecurityErrorWriter errorWriter;

  public AgentToolSecurityFilter(
      InternalSecurityProperties properties,
      AgentContextTokenService tokenService,
      UserMapper userMapper,
      SecurityErrorWriter errorWriter) {
    this.properties = properties;
    this.tokenService = tokenService;
    this.userMapper = userMapper;
    this.errorWriter = errorWriter;
  }

  @Override
  protected boolean shouldNotFilter(HttpServletRequest request) {
    return !request.getRequestURI().startsWith(INTERNAL_TOOL_PREFIX);
  }

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    if (!serviceTokenMatches(request.getHeader("X-Service-Token"))) {
      errorWriter.write(request, response, ErrorCode.SERVICE_TOKEN_INVALID);
      return;
    }
    try {
      AgentToolContext context = authenticateContext(request);
      request.setAttribute(AgentToolContext.REQUEST_ATTRIBUTE, context);
      filterChain.doFilter(request, response);
    } catch (JwtException | IllegalArgumentException exception) {
      errorWriter.write(request, response, ErrorCode.AGENT_CONTEXT_INVALID);
    }
  }

  private AgentToolContext authenticateContext(HttpServletRequest request) {
    String authorization = requireHeader(request, "Authorization", 4096);
    if (!authorization.startsWith(BEARER_PREFIX)) {
      throw new IllegalArgumentException("Agent context bearer token is missing");
    }
    AgentContextIdentity identity =
        tokenService.parse(authorization.substring(BEARER_PREFIX.length()));
    String traceId = requireHeader(request, "X-Trace-Id", 64);
    String runId = requireHeader(request, "X-Run-Id", 64);
    String toolCallId = requireHeader(request, "X-Tool-Call-Id", 80);
    if (!traceId.equals(identity.traceId()) || !runId.equals(identity.runId())) {
      throw new IllegalArgumentException("Agent context headers do not match token claims");
    }
    UserAccount account = userMapper.selectById(identity.userId());
    if (account == null || !account.isActive() || !identity.roles().contains(account.getRole())) {
      throw new IllegalArgumentException("Agent context user is inactive or role is stale");
    }
    return new AgentToolContext(
        account.getId(),
        account.getUsername(),
        java.util.List.of(account.getRole()),
        traceId,
        runId,
        toolCallId);
  }

  private boolean serviceTokenMatches(String actual) {
    if (actual == null) {
      return false;
    }
    return MessageDigest.isEqual(
        properties.serviceToken().getBytes(StandardCharsets.UTF_8),
        actual.getBytes(StandardCharsets.UTF_8));
  }

  private String requireHeader(HttpServletRequest request, String name, int maxLength) {
    String value = request.getHeader(name);
    if (value == null || value.isBlank() || value.length() > maxLength) {
      throw new IllegalArgumentException(name + " is missing or invalid");
    }
    return value;
  }
}
