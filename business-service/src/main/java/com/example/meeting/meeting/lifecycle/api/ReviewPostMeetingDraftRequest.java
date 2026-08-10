package com.example.meeting.meeting.lifecycle.api;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

public record ReviewPostMeetingDraftRequest(
    @NotBlank(message = "REQUIRED") @Pattern(regexp = "ACCEPT|EDIT|REJECT", message = "INVALID_ACTION") String action,
    @Min(value = 0, message = "INVALID_VERSION") int expectedVersion,
    PostMeetingDraftContent editedDraft) {}
