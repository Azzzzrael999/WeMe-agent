package com.example.meeting.common.security;

import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.trace.TraceIds;
import com.example.meeting.common.web.ApiError;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;

@Component
public class SecurityErrorWriter {

  private final ObjectMapper objectMapper;

  public SecurityErrorWriter(ObjectMapper objectMapper) {
    this.objectMapper = objectMapper;
  }

  public void write(HttpServletRequest request, HttpServletResponse response, ErrorCode errorCode)
      throws IOException {
    response.setStatus(errorCode.status().value());
    response.setCharacterEncoding(StandardCharsets.UTF_8.name());
    response.setContentType(MediaType.APPLICATION_JSON_VALUE);
    ApiError error =
        new ApiError(
            errorCode.name(), errorCode.defaultMessage(), List.of(), TraceIds.from(request));
    objectMapper.writeValue(response.getOutputStream(), error);
  }
}
