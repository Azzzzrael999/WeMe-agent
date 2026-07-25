package com.example.meeting.agentgateway.internal;

import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.api.UpdateMeetingRequest;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.time.OffsetDateTime;
import java.util.List;

public final class AgentToolDtos {

  private AgentToolDtos() {}

  public record ResolveEmployeesRequest(
      @NotNull @Size(max = 50) List<@NotBlank @Size(max = 64) String> names,
      @NotNull @Size(max = 50) List<@NotBlank @Size(max = 64) String> departmentNames) {}

  public record ResolvedEmployeeView(
      long employeeId,
      String username,
      String displayName,
      Long departmentId,
      String departmentName,
      String status) {}

  public record ResolveEmployeesResponse(
      List<ResolvedEmployeeView> employees, List<String> unresolvedNames) {}

  public record FreeBusyRequest(
      @NotNull @Size(min = 1, max = 50) List<@Positive Long> employeeIds,
      @NotNull OffsetDateTime from,
      @NotNull OffsetDateTime to) {}

  public record BusySlotView(long meetingId, OffsetDateTime startAt, OffsetDateTime endAt) {}

  public record EmployeeFreeBusyView(long employeeId, List<BusySlotView> busySlots) {}

  public record FreeBusyResponse(List<EmployeeFreeBusyView> employees) {}

  public record SearchRoomsRequest(
      @NotNull OffsetDateTime from,
      @NotNull OffsetDateTime to,
      @NotNull @Min(1) @Max(10000) Integer minimumCapacity,
      @NotNull @Size(max = 50) List<@NotBlank @Size(max = 64) String> requiredFeatures,
      @NotNull @Min(1) @Max(50) Integer limit) {}

  public record AvailableRoomView(
      long roomId,
      String roomCode,
      String roomName,
      String building,
      String floor,
      int capacity,
      String roomType,
      boolean isHot,
      List<String> features) {}

  public record SearchRoomsResponse(List<AvailableRoomView> rooms) {}

  public record RecentMeetingRequest(@NotNull @Min(1) @Max(5) Integer limit) {}

  public record RecentMeetingResponse(List<MeetingView> meetings) {}

  public record DraftParticipantView(long employeeId, String displayName) {}

  public record BookingDraftView(
      String title,
      long roomId,
      String roomName,
      OffsetDateTime startAt,
      OffsetDateTime endAt,
      List<DraftParticipantView> requiredParticipants,
      List<DraftParticipantView> optionalParticipants,
      boolean createVideoConference) {}

  public record CreateDraftResponse(
      String confirmationToken, OffsetDateTime expiresAt, BookingDraftView draft) {}

  public record ConfirmBookingResponse(String status, Long meetingId, String requestNo) {}

  public record ConfirmAuditRequest(String confirmationToken, String idempotencyKey) {}

  public record RescheduleDraftRequest(
      @Positive long meetingId,
      @NotBlank @Size(max = 128) String title,
      @NotBlank @Size(max = 32) String meetingType,
      @NotNull @Positive Long roomId,
      @NotNull OffsetDateTime startAt,
      @NotNull OffsetDateTime endAt,
      @NotNull @Size(max = 100) List<@Positive Long> requiredParticipantIds,
      @NotNull @Size(max = 100) List<@Positive Long> optionalParticipantIds,
      @NotNull Boolean createVideoConference,
      @NotNull @Min(0) Integer expectedVersion) {

    public UpdateMeetingRequest toUpdateRequest() {
      return new UpdateMeetingRequest(
          title,
          meetingType,
          roomId,
          startAt,
          endAt,
          requiredParticipantIds,
          optionalParticipantIds,
          createVideoConference,
          expectedVersion);
    }
  }

  public record CancellationPreviewRequest(@Positive long meetingId) {}

  public record CreateRescheduleDraftResponse(
      String confirmationToken,
      OffsetDateTime expiresAt,
      MeetingView before,
      BookingDraftView after) {}

  public record CreateCancellationPreviewResponse(
      String confirmationToken, OffsetDateTime expiresAt, MeetingView meeting) {}
}
