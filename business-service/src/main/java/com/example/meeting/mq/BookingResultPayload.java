package com.example.meeting.mq;

public record BookingResultPayload(
    String requestNo, String status, Long meetingId, ConflictView conflict) {

  public record ConflictView(String type, Long roomId, java.util.List<Short> slots) {}
}
