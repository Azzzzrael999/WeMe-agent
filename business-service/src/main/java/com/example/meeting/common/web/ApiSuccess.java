package com.example.meeting.common.web;

import java.time.OffsetDateTime;

public record ApiSuccess<T>(T data, String traceId, OffsetDateTime timestamp) {}
