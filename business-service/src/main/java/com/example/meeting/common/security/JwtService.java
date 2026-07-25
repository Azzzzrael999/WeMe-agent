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
public class JwtService {

  private final JwtProperties properties;
  private final Clock clock;
  private final SecretKey signingKey;

  public JwtService(JwtProperties properties, Clock clock) {
    this.properties = properties;
    this.clock = clock;
    byte[] secretBytes = properties.secret().getBytes(StandardCharsets.UTF_8);
    if (secretBytes.length < 32) {
      throw new IllegalStateException("JWT_SECRET must contain at least 32 UTF-8 bytes");
    }
    this.signingKey = Keys.hmacShaKeyFor(secretBytes);
  }

  public String issue(long userId, String username, List<String> roles) {
    Instant issuedAt = clock.instant();
    Instant expiresAt = issuedAt.plusSeconds(properties.expirationSeconds());
    return Jwts.builder()
        .issuer(properties.issuer())
        .subject(username)
        .claim("uid", userId)
        .claim("roles", List.copyOf(roles))
        .issuedAt(Date.from(issuedAt))
        .expiration(Date.from(expiresAt))
        .signWith(signingKey)
        .compact();
  }

  public JwtIdentity parse(String token) {
    Claims claims =
        Jwts.parser()
            .verifyWith(signingKey)
            .requireIssuer(properties.issuer())
            .build()
            .parseSignedClaims(token)
            .getPayload();
    Object userIdClaim = claims.get("uid");
    if (!(userIdClaim instanceof Number userId)) {
      throw new IllegalArgumentException("JWT uid claim is missing");
    }
    Object rolesClaim = claims.get("roles");
    if (!(rolesClaim instanceof List<?> rawRoles)) {
      throw new IllegalArgumentException("JWT roles claim is missing");
    }
    List<String> roles = rawRoles.stream().map(String::valueOf).toList();
    return new JwtIdentity(userId.longValue(), claims.getSubject(), roles);
  }

  public long expirationSeconds() {
    return properties.expirationSeconds();
  }
}
