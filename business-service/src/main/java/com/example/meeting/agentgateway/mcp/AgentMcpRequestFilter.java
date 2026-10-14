package com.example.meeting.agentgateway.mcp;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ReadListener;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Set;
import java.util.concurrent.Semaphore;
import java.util.stream.Collectors;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/** Bound resources before the SDK buffers a request. Stateless MCP stores no sessions. */
@Component
public class AgentMcpRequestFilter extends OncePerRequestFilter {
  private final int maxRequestBytes;
  private final Semaphore concurrency;
  private final Set<String> allowedOrigins;

  public AgentMcpRequestFilter(
      @Value("${app.mcp.max-request-bytes:65536}") int maxRequestBytes,
      @Value("${app.mcp.max-concurrent-requests:32}") int maxConcurrentRequests,
      @Value("${app.mcp.allowed-origins:}") String allowedOrigins) {
    if (maxRequestBytes < 1024
        || maxRequestBytes > 1048576
        || maxConcurrentRequests < 1
        || maxConcurrentRequests > 256)
      throw new IllegalArgumentException("Invalid MCP resource limits");
    this.maxRequestBytes = maxRequestBytes;
    this.concurrency = new Semaphore(maxConcurrentRequests);
    this.allowedOrigins =
        Arrays.stream(allowedOrigins.split(","))
            .map(String::trim)
            .filter(value -> !value.isEmpty())
            .collect(Collectors.toUnmodifiableSet());
  }

  @Override
  protected boolean shouldNotFilter(HttpServletRequest request) {
    return !AgentMcpConfiguration.ENDPOINT.equals(request.getRequestURI());
  }

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain chain)
      throws ServletException, IOException {
    String origin = request.getHeader("Origin");
    if (origin != null && !allowedOrigins.contains(origin)) {
      response.sendError(403);
      return;
    }
    if (request.getContentLengthLong() > maxRequestBytes) {
      response.sendError(413);
      return;
    }
    if (!concurrency.tryAcquire()) {
      response.sendError(503);
      return;
    }
    try {
      byte[] body = request.getInputStream().readNBytes(maxRequestBytes + 1);
      if (body.length > maxRequestBytes) {
        response.sendError(413);
        return;
      }
      chain.doFilter(new BufferedRequest(request, body), response);
    } finally {
      concurrency.release();
    }
  }

  private static class BufferedRequest extends HttpServletRequestWrapper {
    private final byte[] body;

    BufferedRequest(HttpServletRequest request, byte[] body) {
      super(request);
      this.body = body;
    }

    @Override
    public BufferedReader getReader() {
      return new BufferedReader(
          new InputStreamReader(new ByteArrayInputStream(body), StandardCharsets.UTF_8));
    }

    @Override
    public ServletInputStream getInputStream() {
      ByteArrayInputStream input = new ByteArrayInputStream(body);
      return new ServletInputStream() {
        @Override
        public int read() {
          return input.read();
        }

        @Override
        public boolean isFinished() {
          return input.available() == 0;
        }

        @Override
        public boolean isReady() {
          return true;
        }

        @Override
        public void setReadListener(ReadListener listener) {
          throw new UnsupportedOperationException("MCP uses buffered synchronous request reads");
        }
      };
    }
  }
}
