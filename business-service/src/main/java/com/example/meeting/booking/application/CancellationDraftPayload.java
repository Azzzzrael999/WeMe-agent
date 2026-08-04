package com.example.meeting.booking.application;

public record CancellationDraftPayload(long meetingId, int expectedVersion) {}
