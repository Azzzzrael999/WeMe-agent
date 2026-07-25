package com.example.meeting.mq;

public record MeetingConfirmedPayload(long meetingId, String requestNo, long organizerId) {}
