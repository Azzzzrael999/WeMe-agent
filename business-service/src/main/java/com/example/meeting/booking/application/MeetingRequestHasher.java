package com.example.meeting.booking.application;

import com.example.meeting.booking.domain.NormalizedMeetingCommand;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import org.springframework.stereotype.Component;

@Component
public class MeetingRequestHasher {

  public String hash(NormalizedMeetingCommand command) {
    String canonical =
        String.join(
            "\n",
            command.title(),
            command.meetingType(),
            Long.toString(command.roomId()),
            command.schedule().startAt().toString(),
            command.schedule().endAt().toString(),
            command.requiredParticipantIds().toString(),
            command.optionalParticipantIds().toString());
    try {
      MessageDigest digest = MessageDigest.getInstance("SHA-256");
      return HexFormat.of().formatHex(digest.digest(canonical.getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 is unavailable", exception);
    }
  }
}
