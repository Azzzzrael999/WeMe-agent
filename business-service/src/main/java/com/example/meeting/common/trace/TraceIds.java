package com.example.meeting.common.trace;

import jakarta.servlet.http.HttpServletRequest;

public final class TraceIds {

  public static final String REQUEST_ATTRIBUTE = TraceIds.class.getName() + ".traceId";

  private TraceIds() {}

  public static String from(HttpServletRequest request) {
    Object traceId = request.getAttribute(REQUEST_ATTRIBUTE);
    return traceId instanceof String value ? value : "trc_unavailable";
  }
}
