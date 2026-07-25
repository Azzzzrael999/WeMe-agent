package com.example.meeting.booking.application;

import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.common.json.StoredJson;
import com.example.meeting.meeting.api.CreateMeetingRequest;
import com.example.meeting.mq.BookingCommandPayload;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;

@Component
public class DraftPayloadCodec {

  private final ObjectMapper objectMapper;
  private final MeetingCommandFactory commandFactory;

  public DraftPayloadCodec(ObjectMapper objectMapper, MeetingCommandFactory commandFactory) {
    this.objectMapper = objectMapper;
    this.commandFactory = commandFactory;
  }

  public CreateMeetingRequest fromCommand(NormalizedMeetingCommand command) {
    return new CreateMeetingRequest(
        command.title(),
        command.meetingType(),
        command.roomId(),
        command.schedule().startAt(),
        command.schedule().endAt(),
        command.requiredParticipantIds(),
        command.optionalParticipantIds(),
        false);
  }

  public String write(CreateMeetingRequest request) {
    try {
      return objectMapper.writeValueAsString(request);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Cannot serialize booking draft", exception);
    }
  }

  public CreateMeetingRequest read(String json) {
    try {
      return StoredJson.read(objectMapper, json, CreateMeetingRequest.class);
    } catch (JsonProcessingException exception) {
      throw new IllegalStateException("Stored booking draft payload is invalid", exception);
    }
  }

  public NormalizedMeetingCommand toCommand(String json, long organizerId) {
    return commandFactory.create(read(json), organizerId);
  }

  public BookingCommandPayload toBookingCommand(
      String requestNo, long userId, CreateMeetingRequest request) {
    return new BookingCommandPayload(
        requestNo,
        userId,
        request.title(),
        request.meetingType(),
        request.roomId(),
        request.startAt(),
        request.endAt(),
        request.requiredParticipantIds(),
        request.optionalParticipantIds(),
        request.createVideoConference());
  }
}
