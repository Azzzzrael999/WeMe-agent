package com.example.meeting.meeting.application;

import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import java.time.format.DateTimeFormatter;
import java.util.UUID;
import org.springframework.stereotype.Component;

@Component
public class MeetingNumberGenerator {

  private static final DateTimeFormatter DATE_FORMAT = DateTimeFormatter.BASIC_ISO_DATE;

  public String next(NormalizedMeetingCommand command) {
    String date = command.schedule().startAt().toLocalDate().format(DATE_FORMAT);
    String suffix = UUID.randomUUID().toString().replace("-", "").substring(0, 16);
    return "MTG" + date + suffix.toUpperCase();
  }
}
