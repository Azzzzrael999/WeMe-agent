package com.example.meeting.mq;

import com.example.meeting.booking.application.MeetingCommandFactory;
import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.json.StoredJson;
import com.example.meeting.meeting.api.CreateMeetingRequest;
import com.example.meeting.outbox.EventEnvelope;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;

@Service
public class BookingCommandProcessor {

  private final ObjectMapper objectMapper;
  private final MeetingCommandFactory commandFactory;
  private final BookingCommandFinalizationService finalizationService;

  public BookingCommandProcessor(
      ObjectMapper objectMapper,
      MeetingCommandFactory commandFactory,
      BookingCommandFinalizationService finalizationService) {
    this.objectMapper = objectMapper;
    this.commandFactory = commandFactory;
    this.finalizationService = finalizationService;
  }

  public void process(String eventJson) {
    EventEnvelope event = read(eventJson, EventEnvelope.class);
    if (!"BOOKING_COMMAND".equals(event.eventType())) {
      throw new IllegalArgumentException("Unexpected booking event type");
    }
    BookingCommandPayload payload =
        objectMapper.convertValue(event.payload(), BookingCommandPayload.class);
    NormalizedMeetingCommand command = null;
    try {
      command =
          commandFactory.create(
              new CreateMeetingRequest(
                  payload.title(),
                  payload.meetingType(),
                  payload.roomId(),
                  payload.startAt(),
                  payload.endAt(),
                  payload.requiredParticipantIds(),
                  payload.optionalParticipantIds()),
              payload.userId());
      finalizationService.finalizeSuccess(event, payload, command);
    } catch (BusinessException | DataIntegrityViolationException exception) {
      List<Short> slots =
          command == null
              ? List.of()
              : command.schedule().slots().stream().map(slot -> slot.slotIndex()).toList();
      finalizationService.finalizeConflict(event, payload, slots);
    }
  }

  private <T> T read(String json, Class<T> type) {
    try {
      return StoredJson.read(objectMapper, json, type);
    } catch (JsonProcessingException exception) {
      throw new IllegalArgumentException("RocketMQ event JSON is invalid", exception);
    }
  }
}
