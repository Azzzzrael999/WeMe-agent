"""Supervisor and policy runtime Agents."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.agent_loop import (
    RouteEvaluator,
)
from app.providers.base import (
    ModelProvider,
    StructuredModelRunner,
)
from app.rag.policies import PolicyRetrievalError, PolicyRetriever
from app.schemas.agent import (
    AgentState,
    Intent,
    PolicyResult,
    PolicySelection,
    Route,
    SupervisorDecision,
)
from app.workflow_core.clarification import _compose_clarification
from app.workflow_core.common import WorkflowError, _apply_completions, _model_output_with_count
from app.workflow_core.prompts import SUPERVISOR_PROMPT


@dataclass(frozen=True)
class SupervisorAgent:
    provider: ModelProvider
    runner: StructuredModelRunner
    evaluator: RouteEvaluator = field(default_factory=RouteEvaluator)

    def execute(self, state: AgentState) -> tuple[AgentState, str, int]:
        decision, completions = _model_output_with_count(
            provider=self.provider,
            runner=self.runner,
            agent_name="supervisor",
            system_prompt=SUPERVISOR_PROMPT,
            user_prompt=state.message,
            output_type=SupervisorDecision,
        )
        feedback = self.evaluator.evaluate(decision, state.message)
        if feedback is not None:
            try:
                repaired, repair_completions = _model_output_with_count(
                    provider=self.provider,
                    runner=self.runner,
                    agent_name="supervisor",
                    system_prompt=SUPERVISOR_PROMPT,
                    user_prompt=(
                        f"USER_MESSAGE={state.message}\nROUTE_FEEDBACK="
                        f"{feedback.model_dump_json(by_alias=True)}"
                    ),
                    output_type=SupervisorDecision,
                )
                completions.extend(repair_completions)
                decision = repaired
                feedback = self.evaluator.evaluate(decision, state.message)
            except WorkflowError:
                feedback = feedback
        if feedback is None:
            route = decision.route
            intent = decision.intent_hint
        else:
            route, intent = self.evaluator.fallback(state.message)
        fallback_route, fallback_intent = self.evaluator.fallback(state.message)
        if (
            route is Route.REQUIREMENT
            and fallback_route is Route.REQUIREMENT
            and fallback_intent is not None
        ):
            # A missing model intent hint must not reintroduce variance after
            # high-confidence source anchors have already fixed the boundary.
            intent = fallback_intent
        if route is Route.POLICY:
            intent = Intent.QUERY_POLICY
        clarification = None
        if route is Route.CLARIFICATION:
            clarification, clarification_completions = _compose_clarification(
                provider=self.provider,
                issue_codes=["OBJECTIVE_NOT_UNDERSTOOD"],
                request=None,
            )
            completions.extend(clarification_completions)
        updated = _apply_completions(state, completions)
        return (
            updated.model_copy(
                update={
                    "next_route": route,
                    "intent": intent,
                    "answer_summary": clarification,
                }
            ),
            decision.summary,
            len(completions),
        )


@dataclass(frozen=True)
class PolicyAgent:
    provider: ModelProvider
    runner: StructuredModelRunner
    retriever: PolicyRetriever

    def execute(self, state: AgentState) -> tuple[AgentState, str, int]:
        try:
            candidates = self.retriever.search(state.message)
        except PolicyRetrievalError as exc:
            raise WorkflowError("POLICY_RETRIEVAL_UNAVAILABLE", "会议制度检索暂不可用") from exc
        if not candidates:
            result = PolicyResult(
                summary="未找到可验证的会议制度证据。",
                confidence=0.0,
                verification_status="UNVERIFIED",
                constraints=[],
                citations=[],
            )
            return (
                state.model_copy(update={"policy_result": result, "citations": []}),
                result.summary,
                0,
            )

        candidate_evidence = [
            {
                "chunkId": chunk.chunk_id,
                "title": chunk.title,
                "headingPath": list(chunk.heading_path),
                "page": chunk.page,
                "content": chunk.content,
            }
            for chunk in candidates[:5]
        ]
        selection_prompt = (
            "You are the Policy Agent. Answer only from the supplied RETRIEVED_EVIDENCE "
            "content. Select only chunk IDs whose content directly supports the answer. "
            "If none of the supplied chunks answers the question, return selectedChunkIds "
            "as [] and explicitly say that no verifiable policy evidence was found. Never "
            "infer a rule from a title, invent a citation, or make a booking decision."
        )
        selection_input = (
            f"QUESTION={state.message}\nRETRIEVED_EVIDENCE="
            f"{json.dumps(candidate_evidence, ensure_ascii=False, separators=(',', ':'))}"
        )
        selection, completions = _model_output_with_count(
            provider=self.provider,
            runner=self.runner,
            agent_name="policy",
            system_prompt=selection_prompt,
            user_prompt=selection_input,
            output_type=PolicySelection,
        )
        if not selection.selected_chunk_ids:
            # Empty selection is safe but occasionally caused by a transient
            # model miss even when the retrieved content directly answers the
            # question. Re-check once; an empty second answer still wins.
            try:
                repaired, repair_completions = _model_output_with_count(
                    provider=self.provider,
                    runner=self.runner,
                    agent_name="policy",
                    system_prompt=selection_prompt,
                    user_prompt=(
                        f"{selection_input}\nSELECTION_FEEDBACK="
                        "The first pass selected no evidence. Re-check the content once. "
                        "Select a chunk only if its content directly answers the question; "
                        "otherwise keep selectedChunkIds empty."
                    ),
                    output_type=PolicySelection,
                )
                completions.extend(repair_completions)
                selection = repaired
            except WorkflowError:
                # A failed optional re-check must not turn the first pass's
                # safe UNVERIFIED result into a workflow failure.
                pass
        try:
            opened = self.retriever.open_candidates(
                candidates=candidates,
                selected_chunk_ids=selection.selected_chunk_ids,
            )
        except PolicyRetrievalError as exc:
            raise WorkflowError("POLICY_CITATION_INVALID", "规则引用不在本轮检索结果中") from exc
        citations = [chunk.citation() for chunk in opened]
        if not citations:
            selection = selection.model_copy(
                update={
                    "answer_summary": "未找到可验证的会议制度证据。",
                    "confidence": 0.0,
                    "constraints": [],
                }
            )
        result = PolicyResult(
            summary=selection.answer_summary,
            confidence=selection.confidence,
            verification_status="VERIFIED" if citations else "UNVERIFIED",
            constraints=selection.constraints,
            citations=citations,
        )
        next_route = Route.FINAL if state.intent is Intent.QUERY_POLICY else Route.SCHEDULING
        return (
            _apply_completions(state, completions).model_copy(
                update={"policy_result": result, "citations": citations, "next_route": next_route}
            ),
            result.summary,
            len(completions),
        )
