package com.example.meeting.common.security;

import com.example.meeting.auth.application.AuthenticationService;
import com.example.meeting.auth.domain.UserAccount;
import com.example.meeting.common.error.BusinessException;
import io.jsonwebtoken.JwtException;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class JwtAuthenticationFilter extends OncePerRequestFilter {

  private static final Logger LOGGER = LoggerFactory.getLogger(JwtAuthenticationFilter.class);
  private static final String BEARER_PREFIX = "Bearer ";

  private final JwtService jwtService;
  private final AuthenticationService authenticationService;

  public JwtAuthenticationFilter(
      JwtService jwtService, AuthenticationService authenticationService) {
    this.jwtService = jwtService;
    this.authenticationService = authenticationService;
  }

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    String authorization = request.getHeader("Authorization");
    if (authorization != null
        && authorization.startsWith(BEARER_PREFIX)
        && SecurityContextHolder.getContext().getAuthentication() == null) {
      authenticate(authorization.substring(BEARER_PREFIX.length()));
    }
    filterChain.doFilter(request, response);
  }

  private void authenticate(String token) {
    try {
      JwtIdentity identity = jwtService.parse(token);
      UserAccount account = authenticationService.loadActiveAccount(identity.username());
      if (!account.getId().equals(identity.userId())) {
        return;
      }
      List<String> roles = List.of(account.getRole());
      AuthenticatedUser principal =
          new AuthenticatedUser(account.getId(), account.getUsername(), roles);
      List<SimpleGrantedAuthority> authorities =
          roles.stream().map(role -> new SimpleGrantedAuthority("ROLE_" + role)).toList();
      SecurityContextHolder.getContext()
          .setAuthentication(new UsernamePasswordAuthenticationToken(principal, null, authorities));
    } catch (JwtException | IllegalArgumentException | BusinessException exception) {
      LOGGER.debug("Rejected invalid bearer token: {}", exception.getClass().getSimpleName());
      SecurityContextHolder.clearContext();
    }
  }
}
