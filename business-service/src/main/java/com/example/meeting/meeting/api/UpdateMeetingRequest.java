package com.example.meeting.meeting.api;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.time.OffsetDateTime;
import java.util.List;

public record UpdateMeetingRequest(
    @NotBlank(message = "REQUIRED") @Size(max = 128, message = "TOO_LONG") String title,
    @NotBlank(message = "REQUIRED") @Size(max = 32, message = "TOO_LONG") String meetingType,
    @NotNull(message = "REQUIRED") @Positive(message = "MUST_BE_POSITIVE") Long roomId,
    @NotNull(message = "REQUIRED") OffsetDateTime startAt,
    @NotNull(message = "REQUIRED") OffsetDateTime endAt,
    @NotNull(message = "REQUIRED") @Size(max = 100, message = "TOO_MANY_PARTICIPANTS") List<@Positive(message = "MUST_BE_POSITIVE") Long> requiredParticipantIds,
    @NotNull(message = "REQUIRED") @Size(max = 100, message = "TOO_MANY_PARTICIPANTS") List<@Positive(message = "MUST_BE_POSITIVE") Long> optionalParticipantIds,
    @NotNull(message = "REQUIRED") Boolean createVideoConference,
    @NotNull(message = "REQUIRED") @Min(value = 0, message = "MUST_NOT_BE_NEGATIVE") Integer expectedVersion) {}
