package com.example.meeting.agentgateway.api;

import com.fasterxml.jackson.annotation.JsonIgnore;
import jakarta.validation.Valid;
import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.time.OffsetDateTime;

/**
 * User-confirmed continuation for an Agent run. The Java gateway validates only the public shape;
 * Python reloads the persisted run and decides whether the caller may resume it.
 */
public record AgentRunResumeRequest(
    @NotNull Action action,
    @NotBlank @Size(max = 80) String confirmationToken,
    @Valid EditedDraft editedDraft,
    @Size(max = 1000) String feedback) {

  @AssertTrue(
      message =
          "editedDraft must only be supplied for EDIT and include meetingId, roomId or startAt")
  @JsonIgnore
  public boolean isActionPayloadValid() {
    return switch (action == null ? null : action) {
      case EDIT -> editedDraft != null && editedDraft.hasAtLeastOneChange();
      case ACCEPT, REJECT -> editedDraft == null;
      case null -> false;
    };
  }

  public enum Action {
    ACCEPT,
    EDIT,
    REJECT
  }

  public record EditedDraft(
      @Positive Long meetingId, @Positive Long roomId, OffsetDateTime startAt) {

    @JsonIgnore
    boolean hasAtLeastOneChange() {
      return meetingId != null || roomId != null || startAt != null;
    }
  }
}
