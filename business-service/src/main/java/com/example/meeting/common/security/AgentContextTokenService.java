package com.example.meeting.common.security;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.util.Date;
import java.util.List;
import javax.crypto.SecretKey;
import org.springframework.stereotype.Service;

@Service
public class AgentContextTokenService {

  private final InternalSecurityProperties properties;
  private final Clock clock;
  private final SecretKey signingKey;

  public AgentContextTokenService(InternalSecurityProperties properties, Clock clock) {
    this.properties = properties;
    this.clock = clock;
    byte[] secret = properties.agentContextSecret().getBytes(StandardCharsets.UTF_8);
    if (secret.length < 32) {
      throw new IllegalStateException(
          "AGENT_CONTEXT_JWT_SECRET must contain at least 32 UTF-8 bytes");
    }
    this.signingKey = Keys.hmacShaKeyFor(secret);
  }

  public String issue(AuthenticatedUser user, String traceId, String runId) {
    Instant issuedAt = clock.instant();
    return Jwts.builder()
        .subject(Long.toString(user.userId()))
        .claim("roles", user.roles())
        .claim("traceId", traceId)
        .claim("runId", runId)
        .audience()
        .add(properties.agentContextAudience())
        .and()
        .issuedAt(Date.from(issuedAt))
        .expiration(Date.from(issuedAt.plusSeconds(properties.agentContextExpirationSeconds())))
        .signWith(signingKey, Jwts.SIG.HS256)
        .compact();
  }

  public AgentContextIdentity parse(String token) {
    Claims claims =
        Jwts.parser()
            .verifyWith(signingKey)
            .requireAudience(properties.agentContextAudience())
            .build()
            .parseSignedClaims(token)
            .getPayload();
    long userId = Long.parseLong(requireText(claims.getSubject(), "sub"));
    Object rolesClaim = claims.get("roles");
    if (!(rolesClaim instanceof List<?> rawRoles) || rawRoles.isEmpty()) {
      throw new IllegalArgumentException("roles claim is missing");
    }
    List<String> roles = rawRoles.stream().map(String::valueOf).toList();
    return new AgentContextIdentity(
        userId,
        roles,
        requireText(claims.get("traceId", String.class), "traceId"),
        requireText(claims.get("runId", String.class), "runId"));
  }

  private String requireText(String value, String name) {
    if (value == null || value.isBlank()) {
      throw new IllegalArgumentException(name + " claim is missing");
    }
    return value;
  }
}
