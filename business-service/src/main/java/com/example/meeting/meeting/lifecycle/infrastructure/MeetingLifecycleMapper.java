package com.example.meeting.meeting.lifecycle.infrastructure;

import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.ActionItemRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.ActionReminderRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.AgendaRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.DecisionRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.DraftRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.MaterialRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.MinutesRow;
import com.example.meeting.meeting.lifecycle.infrastructure.MeetingLifecycleRows.ScheduledMeetingRow;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Options;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface MeetingLifecycleMapper {

  @Select(
      "SELECT preparation_version FROM meeting_lifecycle_profile WHERE meeting_id = #{meetingId}")
  Optional<Integer> findPreparationVersion(@Param("meetingId") long meetingId);

  @Select(
      "SELECT preparation_version FROM meeting_lifecycle_profile WHERE meeting_id = #{meetingId} FOR UPDATE")
  Optional<Integer> findPreparationVersionForUpdate(@Param("meetingId") long meetingId);

  @Insert(
      """
      INSERT INTO meeting_lifecycle_profile (
          meeting_id, preparation_version, created_at, updated_at
      ) VALUES (#{meetingId}, #{version}, #{now}, #{now})
      """)
  int insertPreparationProfile(
      @Param("meetingId") long meetingId,
      @Param("version") int version,
      @Param("now") LocalDateTime now);

  @Update(
      """
      UPDATE meeting_lifecycle_profile
      SET preparation_version = preparation_version + 1, updated_at = #{now}
      WHERE meeting_id = #{meetingId} AND preparation_version = #{expectedVersion}
      """)
  int incrementPreparationVersion(
      @Param("meetingId") long meetingId,
      @Param("expectedVersion") int expectedVersion,
      @Param("now") LocalDateTime now);

  @Delete("DELETE FROM meeting_agenda_item WHERE meeting_id = #{meetingId}")
  int deleteAgenda(@Param("meetingId") long meetingId);

  @Delete("DELETE FROM meeting_material WHERE meeting_id = #{meetingId}")
  int deleteMaterials(@Param("meetingId") long meetingId);

  @Insert(
      """
      <script>
      INSERT INTO meeting_agenda_item (
          meeting_id, sequence_no, topic, owner_employee_id, planned_minutes
      ) VALUES
      <foreach collection="items" item="item" separator=",">
        (#{meetingId}, #{item.sequenceNo}, #{item.topic}, #{item.ownerEmployeeId},
         #{item.plannedMinutes})
      </foreach>
      </script>
      """)
  int insertAgenda(@Param("meetingId") long meetingId, @Param("items") List<AgendaRow> items);

  @Insert(
      """
      <script>
      INSERT INTO meeting_material (
          meeting_id, sequence_no, title, owner_employee_id, required, status,
          version_label, note
      ) VALUES
      <foreach collection="items" item="item" separator=",">
        (#{meetingId}, #{item.sequenceNo}, #{item.title}, #{item.ownerEmployeeId},
         #{item.required}, #{item.status}, #{item.versionLabel}, #{item.note})
      </foreach>
      </script>
      """)
  int insertMaterials(@Param("meetingId") long meetingId, @Param("items") List<MaterialRow> items);

  @Select(
      """
      SELECT agenda.id, agenda.meeting_id, agenda.sequence_no, agenda.topic,
             agenda.owner_employee_id, owner.display_name AS owner_name,
             agenda.planned_minutes
      FROM meeting_agenda_item agenda
      JOIN sys_user owner ON owner.id = agenda.owner_employee_id
      WHERE agenda.meeting_id = #{meetingId}
      ORDER BY agenda.sequence_no
      """)
  List<AgendaRow> findAgenda(@Param("meetingId") long meetingId);

  @Select(
      """
      SELECT material.id, material.meeting_id, material.sequence_no, material.title,
             material.owner_employee_id, owner.display_name AS owner_name,
             material.required, material.status, material.version_label, material.note
      FROM meeting_material material
      JOIN sys_user owner ON owner.id = material.owner_employee_id
      WHERE material.meeting_id = #{meetingId}
      ORDER BY material.sequence_no
      """)
  List<MaterialRow> findMaterials(@Param("meetingId") long meetingId);

  @Select("SELECT COUNT(*) FROM meeting_room WHERE id = #{roomId} AND status = 'ACTIVE'")
  int countActiveRoom(@Param("roomId") long roomId);

  @Select(
      "SELECT COUNT(*) FROM meeting_participant WHERE meeting_id = #{meetingId} AND participant_type = 'REQUIRED'")
  int countRequiredParticipants(@Param("meetingId") long meetingId);

  @Select(
      """
      SELECT id, meeting_id, request_id, agent_run_id, transcript, payload_json, status,
             version, error_code, submitted_by, reviewed_by, created_at, updated_at, reviewed_at
      FROM post_meeting_draft
      WHERE meeting_id = #{meetingId}
      """)
  Optional<DraftRow> findDraft(@Param("meetingId") long meetingId);

  @Select(
      """
      SELECT id, meeting_id, request_id, agent_run_id, transcript, payload_json, status,
             version, error_code, submitted_by, reviewed_by, created_at, updated_at, reviewed_at
      FROM post_meeting_draft
      WHERE meeting_id = #{meetingId}
      FOR UPDATE
      """)
  Optional<DraftRow> findDraftForUpdate(@Param("meetingId") long meetingId);

  @Insert(
      """
      INSERT INTO post_meeting_draft (
          meeting_id, request_id, agent_run_id, transcript, payload_json, status, version,
          error_code, submitted_by, reviewed_by, created_at, updated_at, reviewed_at
      ) VALUES (
          #{meetingId}, #{requestId}, #{agentRunId}, #{transcript}, NULL, 'PROCESSING', 0,
          NULL, #{submittedBy}, NULL, #{now}, #{now}, NULL
      )
      """)
  @Options(useGeneratedKeys = true, keyProperty = "id")
  int insertDraft(DraftInsert row);

  @Update(
      """
      UPDATE post_meeting_draft
      SET request_id = #{requestId}, agent_run_id = #{agentRunId}, transcript = #{transcript},
          payload_json = NULL, status = 'PROCESSING', version = version + 1,
          error_code = NULL, submitted_by = #{submittedBy}, reviewed_by = NULL,
          updated_at = #{now}, reviewed_at = NULL
      WHERE id = #{id} AND version = #{expectedVersion}
        AND status IN ('FAILED', 'REJECTED')
      """)
  int restartDraft(
      @Param("id") long id,
      @Param("requestId") String requestId,
      @Param("agentRunId") String agentRunId,
      @Param("transcript") String transcript,
      @Param("submittedBy") long submittedBy,
      @Param("expectedVersion") int expectedVersion,
      @Param("now") LocalDateTime now);

  @Update(
      """
      UPDATE post_meeting_draft
      SET payload_json = #{payloadJson}, status = 'PENDING_REVIEW', error_code = NULL,
          updated_at = #{now}
      WHERE id = #{id} AND version = #{expectedVersion} AND status = 'PROCESSING'
      """)
  int completeDraft(
      @Param("id") long id,
      @Param("expectedVersion") int expectedVersion,
      @Param("payloadJson") String payloadJson,
      @Param("now") LocalDateTime now);

  @Update(
      """
      UPDATE post_meeting_draft
      SET status = 'FAILED', error_code = #{errorCode}, updated_at = #{now}
      WHERE id = #{id} AND version = #{expectedVersion} AND status = 'PROCESSING'
      """)
  int failDraft(
      @Param("id") long id,
      @Param("expectedVersion") int expectedVersion,
      @Param("errorCode") String errorCode,
      @Param("now") LocalDateTime now);

  @Update(
      """
      UPDATE post_meeting_draft
      SET payload_json = #{payloadJson}, version = version + 1, updated_at = #{now}
      WHERE id = #{id} AND version = #{expectedVersion} AND status = 'PENDING_REVIEW'
      """)
  int editDraft(
      @Param("id") long id,
      @Param("expectedVersion") int expectedVersion,
      @Param("payloadJson") String payloadJson,
      @Param("now") LocalDateTime now);

  @Update(
      """
      UPDATE post_meeting_draft
      SET status = #{status}, reviewed_by = #{reviewedBy}, reviewed_at = #{reviewedAt},
          updated_at = #{reviewedAt}
      WHERE id = #{id} AND version = #{expectedVersion} AND status = 'PENDING_REVIEW'
      """)
  int finishReview(
      @Param("id") long id,
      @Param("expectedVersion") int expectedVersion,
      @Param("status") String status,
      @Param("reviewedBy") long reviewedBy,
      @Param("reviewedAt") LocalDateTime reviewedAt);

  @Select(
      """
      SELECT id, meeting_id, background, discussion_summary, conclusion,
             confirmed_by, confirmed_at
      FROM meeting_minutes
      WHERE meeting_id = #{meetingId}
      """)
  Optional<MinutesRow> findMinutes(@Param("meetingId") long meetingId);

  @Insert(
      """
      INSERT INTO meeting_minutes (
          meeting_id, background, discussion_summary, conclusion, confirmed_by, confirmed_at
      ) VALUES (
          #{meetingId}, #{background}, #{discussionSummary}, #{conclusion},
          #{confirmedBy}, #{confirmedAt}
      )
      """)
  int insertMinutes(
      @Param("meetingId") long meetingId,
      @Param("background") String background,
      @Param("discussionSummary") String discussionSummary,
      @Param("conclusion") String conclusion,
      @Param("confirmedBy") long confirmedBy,
      @Param("confirmedAt") LocalDateTime confirmedAt);

  @Select(
      """
      SELECT id, meeting_id, sequence_no, content, rationale
      FROM meeting_decision
      WHERE meeting_id = #{meetingId}
      ORDER BY sequence_no
      """)
  List<DecisionRow> findDecisions(@Param("meetingId") long meetingId);

  @Insert(
      """
      <script>
      INSERT INTO meeting_decision (meeting_id, sequence_no, content, rationale)
      VALUES
      <foreach collection="items" item="item" separator=",">
        (#{meetingId}, #{item.sequenceNo}, #{item.content}, #{item.rationale})
      </foreach>
      </script>
      """)
  int insertDecisions(@Param("meetingId") long meetingId, @Param("items") List<DecisionRow> items);

  @Select(
      """
      SELECT action.id, action.meeting_id, action.sequence_no, action.title,
             action.description, action.assignee_employee_id,
             assignee.display_name AS assignee_name, action.due_at, action.status,
             action.version, action.completed_at, action.created_at, action.updated_at
      FROM meeting_action_item action
      JOIN sys_user assignee ON assignee.id = action.assignee_employee_id
      WHERE action.meeting_id = #{meetingId}
      ORDER BY action.sequence_no
      """)
  List<ActionItemRow> findActionItems(@Param("meetingId") long meetingId);

  @Select(
      """
      SELECT action.id, action.meeting_id, action.sequence_no, action.title,
             action.description, action.assignee_employee_id,
             assignee.display_name AS assignee_name, action.due_at, action.status,
             action.version, action.completed_at, action.created_at, action.updated_at
      FROM meeting_action_item action
      JOIN sys_user assignee ON assignee.id = action.assignee_employee_id
      WHERE action.id = #{id} AND action.meeting_id = #{meetingId}
      FOR UPDATE
      """)
  Optional<ActionItemRow> findActionItemForUpdate(
      @Param("meetingId") long meetingId, @Param("id") long id);

  @Select(
      """
      SELECT action.id, action.meeting_id, action.sequence_no, action.title,
             action.description, action.assignee_employee_id,
             assignee.display_name AS assignee_name, action.due_at, action.status,
             action.version, action.completed_at, action.created_at, action.updated_at
      FROM meeting_action_item action
      JOIN sys_user assignee ON assignee.id = action.assignee_employee_id
      WHERE action.id = #{id} AND action.meeting_id = #{meetingId}
      """)
  Optional<ActionItemRow> findActionItem(@Param("meetingId") long meetingId, @Param("id") long id);

  @Insert(
      """
      <script>
      INSERT INTO meeting_action_item (
          meeting_id, sequence_no, title, description, assignee_employee_id, due_at,
          status, version, completed_at, created_at, updated_at
      ) VALUES
      <foreach collection="items" item="item" separator=",">
        (#{meetingId}, #{item.sequenceNo}, #{item.title}, #{item.description},
         #{item.assigneeEmployeeId}, #{item.dueAt}, 'OPEN', 0, NULL, #{now}, #{now})
      </foreach>
      </script>
      """)
  int insertActionItems(
      @Param("meetingId") long meetingId,
      @Param("items") List<ActionItemRow> items,
      @Param("now") LocalDateTime now);

  @Update(
      """
      UPDATE meeting_action_item
      SET status = #{status}, version = version + 1, completed_at = #{completedAt},
          updated_at = #{now}
      WHERE id = #{id} AND meeting_id = #{meetingId} AND version = #{expectedVersion}
      """)
  int updateActionStatus(
      @Param("meetingId") long meetingId,
      @Param("id") long id,
      @Param("status") String status,
      @Param("expectedVersion") int expectedVersion,
      @Param("completedAt") LocalDateTime completedAt,
      @Param("now") LocalDateTime now);

  @Select(
      """
      SELECT id, title, organizer_id, start_at, end_at
      FROM meeting
      WHERE status = 'CONFIRMED' AND end_at <= #{now}
      ORDER BY end_at, id
      LIMIT #{limit}
      """)
  List<ScheduledMeetingRow> findMeetingsToComplete(
      @Param("now") LocalDateTime now, @Param("limit") int limit);

  @Update(
      """
      UPDATE meeting
      SET status = 'COMPLETED', version = version + 1, updated_at = #{now}
      WHERE id = #{id} AND status = 'CONFIRMED' AND end_at <= #{now}
      """)
  int completeMeeting(@Param("id") long id, @Param("now") LocalDateTime now);

  @Select(
      """
      SELECT id, title, organizer_id, start_at, end_at
      FROM meeting
      WHERE status = 'CONFIRMED' AND start_at > #{now} AND start_at <= #{upperBound}
      ORDER BY start_at, id
      LIMIT #{limit}
      """)
  List<ScheduledMeetingRow> findMeetingsForReminders(
      @Param("now") LocalDateTime now,
      @Param("upperBound") LocalDateTime upperBound,
      @Param("limit") int limit);

  @Insert(
      """
      INSERT INTO meeting_reminder_delivery (
          meeting_id, meeting_start_at, recipient_id, reminder_type, created_at
      )
      SELECT #{meetingId}, #{meetingStartAt}, #{recipientId}, #{reminderType}, #{createdAt}
      WHERE NOT EXISTS (
          SELECT 1 FROM meeting_reminder_delivery
          WHERE meeting_id = #{meetingId} AND meeting_start_at = #{meetingStartAt}
            AND recipient_id = #{recipientId} AND reminder_type = #{reminderType}
      )
      """)
  int insertMeetingDelivery(
      @Param("meetingId") long meetingId,
      @Param("meetingStartAt") LocalDateTime meetingStartAt,
      @Param("recipientId") long recipientId,
      @Param("reminderType") String reminderType,
      @Param("createdAt") LocalDateTime createdAt);

  @Select(
      """
      SELECT action.id, action.meeting_id, meeting.title AS meeting_title, action.title,
             action.assignee_employee_id, action.due_at, action.status
      FROM meeting_action_item action
      JOIN meeting ON meeting.id = action.meeting_id
      WHERE action.status != 'DONE' AND action.due_at <= #{upperBound}
        AND NOT EXISTS (
          SELECT 1 FROM action_item_reminder_delivery delivery
          WHERE delivery.action_item_id = action.id
            AND delivery.due_at = action.due_at
            AND delivery.recipient_id = action.assignee_employee_id
            AND delivery.reminder_type = CASE
              WHEN action.due_at <= #{now} THEN 'ACTION_ITEM_OVERDUE'
              ELSE 'ACTION_ITEM_DUE_SOON'
            END
        )
      ORDER BY action.due_at, action.id
      LIMIT #{limit}
      """)
  List<ActionReminderRow> findActionsForReminders(
      @Param("now") LocalDateTime now,
      @Param("upperBound") LocalDateTime upperBound,
      @Param("limit") int limit);

  @Insert(
      """
      INSERT INTO action_item_reminder_delivery (
          action_item_id, due_at, recipient_id, reminder_type, created_at
      )
      SELECT #{actionItemId}, #{dueAt}, #{recipientId}, #{reminderType}, #{createdAt}
      WHERE NOT EXISTS (
          SELECT 1 FROM action_item_reminder_delivery
          WHERE action_item_id = #{actionItemId} AND due_at = #{dueAt}
            AND recipient_id = #{recipientId} AND reminder_type = #{reminderType}
      )
      """)
  int insertActionDelivery(
      @Param("actionItemId") long actionItemId,
      @Param("dueAt") LocalDateTime dueAt,
      @Param("recipientId") long recipientId,
      @Param("reminderType") String reminderType,
      @Param("createdAt") LocalDateTime createdAt);

  final class DraftInsert {
    private Long id;
    private final long meetingId;
    private final String requestId;
    private final String agentRunId;
    private final String transcript;
    private final long submittedBy;
    private final LocalDateTime now;

    public DraftInsert(
        long meetingId,
        String requestId,
        String agentRunId,
        String transcript,
        long submittedBy,
        LocalDateTime now) {
      this.meetingId = meetingId;
      this.requestId = requestId;
      this.agentRunId = agentRunId;
      this.transcript = transcript;
      this.submittedBy = submittedBy;
      this.now = now;
    }

    public Long getId() {
      return id;
    }

    public void setId(Long id) {
      this.id = id;
    }

    public long getMeetingId() {
      return meetingId;
    }

    public String getRequestId() {
      return requestId;
    }

    public String getAgentRunId() {
      return agentRunId;
    }

    public String getTranscript() {
      return transcript;
    }

    public long getSubmittedBy() {
      return submittedBy;
    }

    public LocalDateTime getNow() {
      return now;
    }
  }
}
