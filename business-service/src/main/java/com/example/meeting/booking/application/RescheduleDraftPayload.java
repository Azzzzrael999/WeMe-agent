package com.example.meeting.booking.application;

import com.example.meeting.meeting.api.UpdateMeetingRequest;

public record RescheduleDraftPayload(long meetingId, UpdateMeetingRequest request) {}
