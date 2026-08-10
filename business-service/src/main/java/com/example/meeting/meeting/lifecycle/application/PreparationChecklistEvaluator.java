package com.example.meeting.meeting.lifecycle.application;

import com.example.meeting.meeting.api.MeetingView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.ChecklistItemView;
import com.example.meeting.meeting.lifecycle.api.MeetingLifecycleView.ChecklistView;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleMapper;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.AgendaRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.MaterialRow;
import java.time.Clock;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import org.springframework.stereotype.Component;

@Component
public class PreparationChecklistEvaluator {

  private final MeetingLifecycleMapper mapper;
  private final Clock clock;

  public PreparationChecklistEvaluator(MeetingLifecycleMapper mapper, Clock clock) {
    this.mapper = mapper;
    this.clock = clock;
  }

  public ChecklistView evaluate(
      MeetingView meeting, List<AgendaRow> agenda, List<MaterialRow> materials) {
    Set<Long> people = new LinkedHashSet<>();
    people.add(meeting.organizerId());
    meeting.participants().forEach(participant -> people.add(participant.employeeId()));
    long meetingMinutes = Duration.between(meeting.startAt(), meeting.endAt()).toMinutes();
    int agendaMinutes = agenda.stream().mapToInt(AgendaRow::plannedMinutes).sum();
    long missingMaterials =
        materials.stream()
            .filter(MaterialRow::required)
            .filter(row -> !"READY".equals(row.status()))
            .count();

    List<ChecklistItemView> items =
        List.of(
            item(
                "AGENDA_PRESENT",
                !agenda.isEmpty(),
                agenda.isEmpty() ? "尚未配置会议议程" : "已配置 %d 个议题".formatted(agenda.size())),
            item(
                "AGENDA_DURATION",
                agendaMinutes > 0 && agendaMinutes <= meetingMinutes,
                agendaMinutes <= 0
                    ? "议程预计时长尚未配置"
                    : agendaMinutes > meetingMinutes
                        ? "议程总时长 %d 分钟超过会议时长 %d 分钟".formatted(agendaMinutes, meetingMinutes)
                        : "议程总时长 %d 分钟，未超过会议时长".formatted(agendaMinutes)),
            item(
                "AGENDA_OWNERS",
                agenda.stream().allMatch(row -> people.contains(row.ownerEmployeeId())),
                agenda.stream().allMatch(row -> people.contains(row.ownerEmployeeId()))
                    ? "所有议题负责人均属于当前会议人员"
                    : "存在已不属于当前会议人员的议题负责人"),
            item(
                "MATERIALS_READY",
                missingMaterials == 0,
                missingMaterials == 0
                    ? "所有必需材料均已就绪"
                    : "仍有 %d 份必需材料未就绪".formatted(missingMaterials)),
            item(
                "ROOM_ACTIVE",
                mapper.countActiveRoom(meeting.roomId()) > 0,
                mapper.countActiveRoom(meeting.roomId()) > 0 ? "当前会议室可用" : "当前会议室已停用，请处理异常重排"),
            item(
                "PARTICIPANTS_PRESENT",
                mapper.countRequiredParticipants(meeting.id()) > 0,
                mapper.countRequiredParticipants(meeting.id()) > 0 ? "已配置必需参会者" : "尚未配置必需参会者"));
    boolean ready = items.stream().allMatch(ChecklistItemView::passed);
    return new ChecklistView(
        ready ? "READY" : "NEEDS_ATTENTION",
        LocalDateTime.now(clock).atZone(clock.getZone()).toOffsetDateTime(),
        items);
  }

  private ChecklistItemView item(String code, boolean passed, String message) {
    return new ChecklistItemView(code, passed, message);
  }
}
