"""Requirement Agent model calls and deterministic extraction pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta

from app.agent_loop import (
    RequirementEvaluator,
    RequirementNormalizer,
    SourceFidelityEvaluator,
)
from app.providers.base import (
    ModelCompletion,
    ModelProvider,
    ModelRequest,
    StructuredModelRunner,
)
from app.schemas.agent import (
    AgentState,
    Intent,
    PostMeetingDraft,
    PostMeetingDraftRequest,
    RequirementExtraction,
    RequirementFeedbackState,
    Route,
    TimeWindow,
)
from app.security import AgentContext
from app.tools.java import (
    JavaReadToolClient,
    JavaToolError,
    ToolOutcome,
    stable_tool_identity,
)
from app.workflow_core.common import _apply_completions, _model_output_with_count
from app.workflow_core.prompts import (
    POST_MEETING_ANALYSIS_PROMPT,
    REQUIREMENT_PROMPT,
    REQUIREMENT_REPAIR_PROMPT,
)
from app.workflow_core.requirement_merge import (
    _apply_constraint_delta,
    _apply_participant_delta,
    _apply_unsat_recommended_time,
    _continuation_fidelity_feedback,
    _merge_requirement_drafts,
    _scope_members,
)
from app.workflow_core.requirement_parsing import (
    _apply_current_user_participation,
    _apply_explicit_meeting_defaults,
    _resolve_ambiguous_pending_start,
)
from app.workflow_core.requirement_source import (
    _closes_optional_requirements,
    _source_changes_intent,
    _source_describes_fixed_interval,
    _source_has_ambiguous_single_time,
)
from app.workflow_core.requirement_validation import (
    _format_requirement_clarification,
    _requirement_feedback,
    _requirement_items,
)


def _requirement_prompt(state: AgentState) -> str:
    return (
        f"REQUEST_TIME={state.request_time.isoformat()}\n"
        "TIMEZONE=Asia/Shanghai\n"
        f"USER_MESSAGE={state.message}"
    )


@dataclass(frozen=True)
class RequirementAgent:
    provider: ModelProvider
    runner: StructuredModelRunner
    tools: JavaReadToolClient | None = None
    context: AgentContext | None = None

    evaluator: RequirementEvaluator = field(default_factory=RequirementEvaluator)
    fidelity: SourceFidelityEvaluator = field(default_factory=SourceFidelityEvaluator)
    normalizer: RequirementNormalizer = field(default_factory=RequirementNormalizer)

    def analyze_post_meeting(
        self, request: PostMeetingDraftRequest
    ) -> tuple[PostMeetingDraft, list[ModelCompletion]]:
        """Run the same Requirement Agent in an isolated structured-analysis mode."""

        return self.runner.invoke_with_count(
            provider=self.provider,
            request=ModelRequest(
                agent_name="requirement",
                system_prompt=POST_MEETING_ANALYSIS_PROMPT,
                user_prompt=(
                    "POST_MEETING_INPUT="
                    + json.dumps(
                        request.model_dump(by_alias=True, mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ),
                schema_name=PostMeetingDraft.__name__,
                schema=PostMeetingDraft.model_json_schema(by_alias=True),
            ),
            output_type=PostMeetingDraft,
        )

    def execute(self, state: AgentState) -> tuple[AgentState, str, int, list[ToolOutcome]]:
        prompt = _requirement_prompt(state)
        extraction, completions = _model_output_with_count(
            provider=self.provider,
            runner=self.runner,
            agent_name="requirement",
            system_prompt=REQUIREMENT_PROMPT,
            user_prompt=prompt,
            output_type=RequirementExtraction,
        )
        draft = extraction.requirement_draft
        if state.intent is not None and (
            not state.continuation_turn or not _source_changes_intent(state.message)
        ):
            draft = draft.model_copy(update={"intent": state.intent})
        draft = _apply_explicit_meeting_defaults(
            draft, state.message, request_time=state.request_time
        )
        draft = _apply_current_user_participation(draft, state.message)
        if (
            state.continuation_turn
            and state.requirement_draft is not None
            and draft.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
        ):
            draft = draft.model_copy(
                update={
                    "target_meeting_id": (
                        draft.target_meeting_id or state.requirement_draft.target_meeting_id
                    ),
                    "target_meeting_reference": (
                        draft.target_meeting_reference
                        or state.requirement_draft.target_meeting_reference
                    ),
                }
            )
        feedback = _continuation_fidelity_feedback(
            self.fidelity.evaluate(draft, state.message), state=state
        )
        if (
            feedback is not None
            and feedback.codes == ["INTENT_SOURCE_MISMATCH"]
            and state.intent is not None
        ):
            # The Supervisor's high-confidence verb anchors already decide
            # the mutation boundary.  “Give candidates / do not confirm” is
            # HITL guidance, not a room-recommendation intent, so avoid an
            # unnecessary model repair that could rewrite otherwise faithful
            # evidence strings.
            draft = draft.model_copy(update={"intent": state.intent})
            feedback = _continuation_fidelity_feedback(
                self.fidelity.evaluate(draft, state.message), state=state
            )
        if (
            feedback is None
            and not state.continuation_turn
            and not (draft.pending_start_at is not None and draft.duration_minutes is None)
        ):
            initial_request, _ = self.normalizer.normalize(draft, source=state.message)
            feedback = self.evaluator.evaluate(initial_request, request_time=state.request_time)
        if feedback is not None and feedback.repairable:
            extraction, repair_completions = _model_output_with_count(
                provider=self.provider,
                runner=self.runner,
                agent_name="requirement",
                system_prompt=REQUIREMENT_REPAIR_PROMPT,
                user_prompt=(
                    f"{prompt}\nEVALUATOR_FEEDBACK={feedback.model_dump_json(by_alias=True)}"
                ),
                output_type=RequirementExtraction,
            )
            completions.extend(repair_completions)
            draft = extraction.requirement_draft
            if state.intent is not None and (
                not state.continuation_turn or not _source_changes_intent(state.message)
            ):
                draft = draft.model_copy(update={"intent": state.intent})
            draft = _apply_explicit_meeting_defaults(
                draft, state.message, request_time=state.request_time
            )
            draft = _apply_current_user_participation(draft, state.message)
            if (
                state.continuation_turn
                and state.requirement_draft is not None
                and draft.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING}
            ):
                draft = draft.model_copy(
                    update={
                        "target_meeting_id": (
                            draft.target_meeting_id or state.requirement_draft.target_meeting_id
                        ),
                        "target_meeting_reference": (
                            draft.target_meeting_reference
                            or state.requirement_draft.target_meeting_reference
                        ),
                    }
                )
            feedback = _continuation_fidelity_feedback(
                self.fidelity.evaluate(draft, state.message), state=state
            )
            if (
                feedback is not None
                and "INTENT_SOURCE_MISMATCH" in feedback.codes
                and state.intent is not None
            ):
                # The Supervisor route boundary has already applied the same
                # high-confidence anchors.  After one failed model repair,
                # reuse that safe intent instead of asking the user to restate
                # an unambiguous “预约/改到/取消” verb.
                draft = draft.model_copy(update={"intent": state.intent})
                feedback = _continuation_fidelity_feedback(
                    self.fidelity.evaluate(draft, state.message), state=state
                )
        previous_draft = state.requirement_draft if state.continuation_turn else None
        draft = _resolve_ambiguous_pending_start(previous_draft, draft, state.message)
        draft = _apply_participant_delta(previous_draft, draft, state.message)
        draft = _apply_constraint_delta(previous_draft, draft, state.message)
        draft = _apply_unsat_recommended_time(
            previous_draft,
            draft,
            source=state.message,
            analysis=state.unsat_analysis,
        )
        merged = _merge_requirement_drafts(previous_draft, draft, source=state.message)
        if (
            merged.time_window is None
            and merged.pending_start_at is not None
            and not merged.pending_start_ambiguous
            and merged.duration_minutes is not None
        ):
            merged = merged.model_copy(
                update={
                    "time_window": TimeWindow(
                        start=merged.pending_start_at,
                        end=merged.pending_start_at + timedelta(minutes=merged.duration_minutes),
                    )
                }
            )
        outcomes: list[ToolOutcome] = []
        resolved_employees = list(state.resolved_employees)
        if previous_draft is not None and (
            merged.required_participant_names != previous_draft.required_participant_names
        ):
            final_names = set(merged.required_participant_names)
            resolved_employees = [item for item in resolved_employees if item.name in final_names]
        if (
            merged.participant_scope == "MY_DEPARTMENT"
            and not merged.required_participant_names
            and not merged.participant_list_modified
        ):
            if self.tools is None or self.context is None:
                feedback = feedback or _requirement_feedback(
                    "PARTICIPANT_SCOPE_UNRESOLVED", "无法读取当前用户所属小组。"
                )
            else:
                try:
                    outcome = self.tools.resolve_participant_scope(
                        context=self.context,
                        tool_call_id=stable_tool_identity(
                            state.run_id,
                            "resolve_participant_scope",
                            f"requirement-revision-{state.requirement_revision + 1}",
                        ),
                    )
                    members = _scope_members(outcome)
                    if not members:
                        feedback = _requirement_feedback(
                            "PARTICIPANT_SCOPE_UNRESOLVED", "当前小组没有可用于排期的在职成员。"
                        )
                    else:
                        outcomes.append(outcome)
                        resolved_employees = members
                        scope_includes_current_user = any(
                            item.employee_id == self.context.user_id for item in members
                        )
                        merged = merged.model_copy(
                            update={
                                "required_participant_names": [item.name for item in members],
                                "includes_current_user": (
                                    merged.includes_current_user and not scope_includes_current_user
                                ),
                                "minimum_capacity": max(
                                    merged.minimum_capacity or 1,
                                    len({item.employee_id for item in members}),
                                ),
                            }
                        )
                except JavaToolError:
                    feedback = _requirement_feedback(
                        "PARTICIPANT_SCOPE_UNRESOLVED", "无法从通讯录确定当前小组成员。"
                    )

        request, report = self.normalizer.normalize(merged, source=state.message)
        if (
            merged.duration_minutes is None
            and "durationMinutes" in report.derived_fields
            and _source_describes_fixed_interval(state.message)
        ):
            merged = merged.model_copy(update={"duration_minutes": request.duration_minutes})
        semantic = self.evaluator.evaluate(request, request_time=state.request_time)
        if (
            merged.pending_start_at is not None
            and not merged.pending_start_ambiguous
            and merged.duration_minutes is None
            and semantic is not None
            and semantic.codes == ["TIME_WINDOW_REQUIRED"]
        ):
            semantic = None
        if semantic is not None:
            feedback = semantic
        if merged.time_window is None and _source_has_ambiguous_single_time(state.message):
            feedback = _requirement_feedback(
                "TIME_MERIDIEM_AMBIGUOUS", "单独的几点存在上午和下午两种解释。"
            )
        # EDIT is intentionally revalidated by Requirement before it reaches
        # Scheduling.  Only the documented bounded fields may override it.
        if state.edited_draft is not None and state.edited_draft.start_at is not None:
            start_at = state.edited_draft.start_at
            request = request.model_copy(
                update={
                    "time_window": TimeWindow(
                        start=start_at,
                        end=start_at + timedelta(minutes=request.duration_minutes or 0),
                    )
                }
            )
        if state.edited_draft is not None and state.edited_draft.meeting_id is not None:
            request = request.model_copy(
                update={"target_meeting_id": state.edited_draft.meeting_id}
            )
        feedback_state = (
            RequirementFeedbackState.model_validate(feedback.model_dump())
            if feedback is not None
            else None
        )
        semantic_missing = [] if feedback is None else feedback.codes
        missing_fields = list(semantic_missing)
        if request.intent in {
            Intent.CREATE_MEETING,
            Intent.FIND_COMMON_TIME,
            Intent.RECOMMEND_ROOM,
        }:
            if merged.time_window is None and (
                merged.pending_start_at is None or merged.pending_start_ambiguous
            ):
                missing_fields.append("timeWindow")
            if merged.duration_minutes is None:
                missing_fields.append("durationMinutes")
            if (
                request.intent in {Intent.CREATE_MEETING, Intent.FIND_COMMON_TIME}
                and not merged.required_participant_names
                and merged.participant_scope != "ORGANIZER_ONLY"
            ):
                missing_fields.append("requiredParticipants")
        if request.intent in {Intent.MODIFY_MEETING, Intent.CANCEL_MEETING} and (
            request.target_meeting_id is None and not request.target_meeting_reference
        ):
            missing_fields.append("targetMeeting")
        missing_fields = list(dict.fromkeys(missing_fields))
        optional_closed = state.optional_requirements_closed or _closes_optional_requirements(
            state.message
        )
        items = _requirement_items(
            draft=merged,
            request=request,
            missing_fields=missing_fields,
            source=state.message,
            previous_items=state.requirement_items,
            optional_closed=optional_closed,
        )
        next_route = (
            Route.CLARIFICATION
            if missing_fields
            else Route.POLICY
            if merged.needs_policy
            else Route.SCHEDULING
        )
        clarification = None
        if missing_fields:
            clarification = _format_requirement_clarification(items)
        return (
            _apply_completions(state, completions).model_copy(
                update={
                    "intent": request.intent,
                    "meeting_request": request,
                    "requirement_draft": merged,
                    "requirement_items": items,
                    "requirement_revision": state.requirement_revision + 1,
                    "continuation_turn": False,
                    "optional_requirements_closed": optional_closed,
                    "resolved_employees": resolved_employees,
                    "missing_fields": missing_fields,
                    "requirement_feedback": feedback_state,
                    "normalization_report": report,
                    "next_route": next_route,
                    "answer_summary": clarification,
                }
            ),
            merged.summary if feedback is None else feedback.summary,
            len(completions),
            outcomes,
        )
