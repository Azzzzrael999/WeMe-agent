package com.example.meeting.common.trace;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import java.util.regex.Pattern;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TraceIdFilter extends OncePerRequestFilter {

  private static final String TRACE_HEADER = "X-Trace-Id";
  private static final Pattern SAFE_TRACE_ID = Pattern.compile("[A-Za-z0-9_-]{1,64}");

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
      throws ServletException, IOException {
    String traceId = resolveTraceId(request.getHeader(TRACE_HEADER));
    request.setAttribute(TraceIds.REQUEST_ATTRIBUTE, traceId);
    response.setHeader(TRACE_HEADER, traceId);
    MDC.put("traceId", traceId);
    try {
      filterChain.doFilter(request, response);
    } finally {
      MDC.remove("traceId");
    }
  }

  private String resolveTraceId(String candidate) {
    if (candidate != null && SAFE_TRACE_ID.matcher(candidate).matches()) {
      return candidate;
    }
    return "trc_" + UUID.randomUUID().toString().replace("-", "");
  }
}
