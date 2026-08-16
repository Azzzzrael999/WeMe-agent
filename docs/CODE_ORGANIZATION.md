# Large-file organization

This document records the boundaries used when a source file becomes large.
Line count is a signal, not the decision: split a file when it owns multiple
reasons to change, mixes transport/state/domain concerns, or forces unrelated
tests to import the same implementation module.

## Current public facades

The following modules are compatibility facades. Existing callers should keep
using them; implementation code is grouped behind them.

| Public module | Implementation areas |
| --- | --- |
| `agent-service/app/workflow.py` | `workflow_core/` Agents, requirement rules, scheduling, HITL and LangGraph runtime |
| `agent-service/app/schemas/agent.py` | `schemas/agent_core/` base, post-meeting, requirement, scheduling and runtime contracts |
| `agent-service/app/agent_loop.py` | `agent_loop_core/` feedback, routing, requirement evaluation and read-tool gate |

Implementation modules must not import their public facade. Public symbols are
added explicitly to the facade and `__all__`; star re-exports are not used.

## Internal API boundaries

`agent-service/app/api/internal.py` owns FastAPI dependency declarations and
the internal HTTP endpoints. Non-HTTP mechanics live in `api/internal_core/`:

- `run_support.py`: checkpoint loading, run completion/failure persistence,
  visible messages, access checks and safe SSE payload formatting.
- `business_results.py`: idempotent asynchronous callback recording and
  preserved-constraint summaries.
- `sse.py`: the thread-affine LangGraph producer and `StreamingResponse`
  transport.

Endpoint code may depend on these helpers. Helpers must not import the router
module, which keeps endpoint registration acyclic and preserves test overrides.

## Frontend boundaries

Views own markup and component wiring; asynchronous state machines live in
composables, while pure decoding and browser-storage rules live under a feature
directory.

### Agent chat

- `views/ChatView.vue`: template and binding only.
- `composables/useChatWorkflow.ts`: run lifecycle, recovery, polling and user
  actions.
- `features/chat/sse-events.ts`: typed application of server-sent events.
- `features/chat/parsers.ts`: defensive decoding of untrusted API event data.
- `features/chat/storage.ts`: session-storage keys, validation and stored data
  contracts.

### Meeting lifecycle

- `views/MeetingLifecycleView.vue`: preparation/post-meeting presentation.
- `composables/useMeetingLifecycle.ts`: loading, validation and mutations.

Composables must expose a deliberate return contract instead of returning all
local implementation details. Parsing modules do not import Vue state or the
router, so they remain independently testable.

## Candidates intentionally not split now

| File | Decision |
| --- | --- |
| `agent-service/app/evaluation/corpus.py` | Mostly evaluation case data. Split only when cases gain separate ownership, preferably into validated data files. |
| `frontend/src/api/types.ts` | Type-only contracts. Split by API domain when a domain is independently generated or versioned. |
| `agent-service/app/scheduling/solver.py` | One cohesive deterministic algorithm. Extract cost, constraint and UNSAT policies only when they evolve independently. |
| `agent-service/app/rag/ingestion.py` | Cohesive ingestion pipeline; future boundary is parser/chunker versus vector/repository adapters. |
| `agent-service/app/persistence.py` | One repository abstraction. Prefer separate repositories when transaction ownership diverges, not mixins for line-count reduction. |
| Large integration-test files | Scenario collections share expensive fixtures. Split by capability only when fixture setup can remain centralized. |

## Review thresholds

Review production files above roughly 700 lines or classes/functions above 200
lines. A split is justified when at least one of these is true:

1. The file crosses transport, application and domain layers.
2. Changes in one feature repeatedly touch unrelated imports or fixtures.
3. A stable facade can preserve callers while implementation ownership becomes
   clearer.
4. Extracted code has a smaller dependency surface and can be verified on its
   own.

Do not split generated code, declarative data, migrations, or cohesive
algorithms solely to satisfy a numeric limit.
