package com.example.meeting.replan.api;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;

public record ResolveReplanCaseRequest(
    @NotNull @Positive Long roomId,
    @NotNull @Min(0) Integer expectedMeetingVersion,
    @NotNull @Min(0) Integer expectedCaseVersion) {}
