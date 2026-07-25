package com.example.meeting.outbox;

import com.fasterxml.jackson.databind.JsonNode;
import java.time.OffsetDateTime;

public record EventEnvelope(
    String eventId,
    String eventType,
    String aggregateType,
    String aggregateId,
    String traceId,
    String runId,
    OffsetDateTime occurredAt,
    int schemaVersion,
    JsonNode payload) {}
