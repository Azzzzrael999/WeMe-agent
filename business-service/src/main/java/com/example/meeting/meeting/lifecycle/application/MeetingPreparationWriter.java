package com.example.meeting.meeting.lifecycle.application;

import com.example.meeting.common.error.BusinessException;
import com.example.meeting.common.error.ErrorCode;
import com.example.meeting.common.security.AuthenticatedUser;
import com.example.meeting.common.web.ApiErrorDetail;
import com.example.meeting.meeting.domain.MeetingRecord;
import com.example.meeting.meeting.infrastructure.MeetingMapper;
import com.example.meeting.meeting.infrastructure.MeetingParticipantMapper;
import com.example.meeting.meeting.lifecycle.api.SavePreparationRequest;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleMapper;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.AgendaRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.MaterialRow;
import java.time.Clock;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
public class MeetingPreparationWriter {

  private final MeetingMapper meetingMapper;
  private final MeetingParticipantMapper participantMapper;
  private final MeetingLifecycleMapper lifecycleMapper;
  private final Clock clock;

  public MeetingPreparationWriter(
      MeetingMapper meetingMapper,
      MeetingParticipantMapper participantMapper,
      MeetingLifecycleMapper lifecycleMapper,
      Clock clock) {
    this.meetingMapper = meetingMapper;
    this.participantMapper = participantMapper;
    this.lifecycleMapper = lifecycleMapper;
    this.clock = clock;
  }

  @Transactional
  public void save(long meetingId, SavePreparationRequest request, AuthenticatedUser actor) {
    MeetingRecord meeting =
        meetingMapper
            .findByIdForUpdate(meetingId)
            .orElseThrow(() -> new BusinessException(ErrorCode.MEETING_NOT_FOUND));
    requireManagePermission(meeting, actor);
    LocalDateTime now = LocalDateTime.now(clock);
    if (!"CONFIRMED".equals(meeting.getStatus()) || !meeting.getStartAt().isAfter(now)) {
      throw new BusinessException(ErrorCode.MEETING_CONTENT_STATE_CONFLICT);
    }

    Set<Long> allowedOwners = new LinkedHashSet<>();
    allowedOwners.add(meeting.getOrganizerId());
    allowedOwners.addAll(participantMapper.findEmployeeIdsByMeetingId(meetingId));
    int agendaMinutes =
        request.agendaItems().stream().mapToInt(item -> item.plannedMinutes()).sum();
    long meetingMinutes = Duration.between(meeting.getStartAt(), meeting.getEndAt()).toMinutes();
    if (agendaMinutes > meetingMinutes) {
      throw validation("agendaItems", "AGENDA_DURATION_EXCEEDED", "议程总预计时长不能超过会议时长");
    }

    List<AgendaRow> agenda =
        java.util.stream.IntStream.range(0, request.agendaItems().size())
            .mapToObj(
                index -> {
                  SavePreparationRequest.AgendaItemInput item = request.agendaItems().get(index);
                  requireOwner(allowedOwners, item.ownerEmployeeId(), "agendaItems[" + index + "]");
                  return new AgendaRow(
                      null,
                      meetingId,
                      index + 1,
                      item.topic().trim(),
                      item.ownerEmployeeId(),
                      null,
                      item.plannedMinutes());
                })
            .toList();
    List<MaterialRow> materials =
        java.util.stream.IntStream.range(0, request.materials().size())
            .mapToObj(
                index -> {
                  SavePreparationRequest.MaterialInput item = request.materials().get(index);
                  requireOwner(allowedOwners, item.ownerEmployeeId(), "materials[" + index + "]");
                  return new MaterialRow(
                      null,
                      meetingId,
                      index + 1,
                      item.title().trim(),
                      item.ownerEmployeeId(),
                      null,
                      item.required(),
                      item.status(),
                      normalizeOptional(item.versionLabel()),
                      normalizeOptional(item.note()));
                })
            .toList();

    int currentVersion = lifecycleMapper.findPreparationVersionForUpdate(meetingId).orElse(0);
    if (currentVersion != request.expectedVersion()) {
      throw new BusinessException(ErrorCode.MEETING_CONTENT_STATE_CONFLICT);
    }
    try {
      if (lifecycleMapper.findPreparationVersion(meetingId).isEmpty()) {
        if (lifecycleMapper.insertPreparationProfile(meetingId, 1, now) != 1) {
          throw new BusinessException(ErrorCode.MEETING_CONTENT_STATE_CONFLICT);
        }
      } else if (lifecycleMapper.incrementPreparationVersion(meetingId, currentVersion, now) != 1) {
        throw new BusinessException(ErrorCode.MEETING_CONTENT_STATE_CONFLICT);
      }
    } catch (DataIntegrityViolationException exception) {
      throw new BusinessException(ErrorCode.MEETING_CONTENT_STATE_CONFLICT);
    }
    lifecycleMapper.deleteAgenda(meetingId);
    lifecycleMapper.deleteMaterials(meetingId);
    if (!agenda.isEmpty()) {
      lifecycleMapper.insertAgenda(meetingId, agenda);
    }
    if (!materials.isEmpty()) {
      lifecycleMapper.insertMaterials(meetingId, materials);
    }
  }

  private void requireManagePermission(MeetingRecord meeting, AuthenticatedUser actor) {
    if (actor.roles().contains("ADMIN") || meeting.getOrganizerId() == actor.userId()) {
      return;
    }
    if (participantMapper.countParticipant(meeting.getId(), actor.userId()) > 0) {
      throw new BusinessException(ErrorCode.FORBIDDEN);
    }
    throw new BusinessException(ErrorCode.MEETING_NOT_FOUND);
  }

  private void requireOwner(Set<Long> allowed, Long ownerId, String field) {
    if (ownerId == null || !allowed.contains(ownerId)) {
      throw validation(field + ".ownerEmployeeId", "OWNER_NOT_IN_MEETING", "负责人必须是会议参与者或组织者");
    }
  }

  private String normalizeOptional(String value) {
    return value == null || value.isBlank() ? null : value.trim();
  }

  private BusinessException validation(String field, String reason, String message) {
    return new BusinessException(
        ErrorCode.VALIDATION_ERROR, message, List.of(new ApiErrorDetail(field, reason)));
  }
}
