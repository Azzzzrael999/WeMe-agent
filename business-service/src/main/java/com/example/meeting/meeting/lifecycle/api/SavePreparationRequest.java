package com.example.meeting.meeting.lifecycle.api;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.List;

public record SavePreparationRequest(
    @Min(value = 0, message = "INVALID_VERSION") int expectedVersion,
    @NotNull(message = "REQUIRED") @Size(max = 30, message = "TOO_MANY_ITEMS") List<@Valid AgendaItemInput> agendaItems,
    @NotNull(message = "REQUIRED") @Size(max = 50, message = "TOO_MANY_ITEMS") List<@Valid MaterialInput> materials) {

  public SavePreparationRequest {
    agendaItems = agendaItems == null ? null : List.copyOf(agendaItems);
    materials = materials == null ? null : List.copyOf(materials);
  }

  public record AgendaItemInput(
      @NotBlank(message = "REQUIRED") @Size(max = 200, message = "TOO_LONG") String topic,
      @NotNull(message = "REQUIRED") Long ownerEmployeeId,
      @Min(value = 5, message = "OUT_OF_RANGE") @Max(value = 240, message = "OUT_OF_RANGE") int plannedMinutes) {}

  public record MaterialInput(
      @NotBlank(message = "REQUIRED") @Size(max = 200, message = "TOO_LONG") String title,
      @NotNull(message = "REQUIRED") Long ownerEmployeeId,
      boolean required,
      @NotBlank(message = "REQUIRED") @Pattern(regexp = "MISSING|READY", message = "INVALID_STATUS") String status,
      @Size(max = 64, message = "TOO_LONG") String versionLabel,
      @Size(max = 500, message = "TOO_LONG") String note) {}
}
