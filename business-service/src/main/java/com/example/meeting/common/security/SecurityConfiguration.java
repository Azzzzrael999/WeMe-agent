package com.example.meeting.common.security;

import com.example.meeting.common.error.ErrorCode;
import jakarta.servlet.DispatcherType;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

@Configuration
@EnableMethodSecurity
public class SecurityConfiguration {

  @Bean
  PasswordEncoder passwordEncoder() {
    return new BCryptPasswordEncoder();
  }

  @Bean
  SecurityFilterChain securityFilterChain(
      HttpSecurity http,
      JwtAuthenticationFilter jwtAuthenticationFilter,
      AgentToolSecurityFilter agentToolSecurityFilter,
      SecurityErrorWriter errorWriter)
      throws Exception {
    return http.csrf(csrf -> csrf.disable())
        .sessionManagement(
            sessions -> sessions.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
        .authorizeHttpRequests(
            requests ->
                requests
                    // StreamingResponseBody completes on an ASYNC dispatcher. The initial REQUEST
                    // remains authenticated by the rules below; only its internal continuation and
                    // error dispatch are permitted so the Java SSE proxy cannot be cut off after
                    // it has already committed a valid event stream.
                    .dispatcherTypeMatchers(DispatcherType.ASYNC, DispatcherType.ERROR)
                    .permitAll()
                    .requestMatchers("/api/v1/auth/login", "/actuator/health/**")
                    .permitAll()
                    .requestMatchers("/internal/v1/tools/**")
                    .permitAll()
                    .requestMatchers("/api/v1/admin/**")
                    .hasRole("ADMIN")
                    .requestMatchers("/api/v1/rooms", "/api/v1/rooms/**", "/api/v1/auth/me")
                    .hasAnyRole("EMPLOYEE", "ADMIN")
                    .requestMatchers("/api/v1/directory", "/api/v1/directory/**")
                    .hasAnyRole("EMPLOYEE", "ADMIN")
                    .requestMatchers("/api/v1/meetings", "/api/v1/meetings/**")
                    .hasAnyRole("EMPLOYEE", "ADMIN")
                    .requestMatchers("/api/v1/booking-requests/**", "/api/v1/agent/runs/**")
                    .hasAnyRole("EMPLOYEE", "ADMIN")
                    .requestMatchers("/api/v1/notifications", "/api/v1/notifications/**")
                    .hasAnyRole("EMPLOYEE", "ADMIN")
                    .requestMatchers("/api/v1/replan-cases", "/api/v1/replan-cases/**")
                    .hasAnyRole("EMPLOYEE", "ADMIN")
                    .anyRequest()
                    .authenticated())
        .exceptionHandling(
            exceptions ->
                exceptions
                    .authenticationEntryPoint(
                        (request, response, exception) ->
                            errorWriter.write(request, response, ErrorCode.AUTH_REQUIRED))
                    .accessDeniedHandler(
                        (request, response, exception) ->
                            errorWriter.write(request, response, ErrorCode.FORBIDDEN)))
        .addFilterBefore(jwtAuthenticationFilter, UsernamePasswordAuthenticationFilter.class)
        .addFilterBefore(agentToolSecurityFilter, JwtAuthenticationFilter.class)
        .build();
  }
}
