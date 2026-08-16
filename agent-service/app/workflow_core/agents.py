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
        selection, completions = _model_output_with_count(
            provider=self.provider,
            runner=self.runner,
            agent_name="policy",
            system_prompt=(
                "You are the Policy Agent. Answer only from the supplied RETRIEVED_EVIDENCE "
                "content. Select only chunk IDs whose content directly supports the answer. "
                "If none of the supplied chunks answers the question, return selectedChunkIds "
                "as [] and explicitly say that no verifiable policy evidence was found. Never "
                "infer a rule from a title, invent a citation, or make a booking decision."
            ),
            user_prompt=(
                f"QUESTION={state.message}\nRETRIEVED_EVIDENCE="
                f"{json.dumps(candidate_evidence, ensure_ascii=False, separators=(',', ':'))}"
            ),
            output_type=PolicySelection,
        )
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
