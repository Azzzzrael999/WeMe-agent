package com.example.meeting.meeting.lifecycle.api;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record CreatePostMeetingDraftRequest(
    @NotBlank(message = "REQUIRED") @Size(max = 20000, message = "TOO_LONG") String transcript) {}
