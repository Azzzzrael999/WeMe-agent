# Meeting workflow architecture

`app.workflow` is the stable public facade. Runtime code outside this directory
should import workflow types and factories from that facade rather than from an
implementation module.

## Module ownership

| Module | Owns |
| --- | --- |
| `prompts.py` | Prompt/schema versions and model system prompts |
| `common.py` | `WorkflowError`, event buffering, model invocation and token accounting |
| `clarification.py` | Verified clarification contracts and safe user-facing rendering |
| `agents.py` | Supervisor and policy Agents |
| `requirement_agent.py` | Requirement Agent model/repair pipeline and post-meeting analysis |
| `requirement_source.py` | Pure predicates over the current user message |
| `requirement_parsing.py` | Deterministic defaults, date/time parsing and identity handling |
| `requirement_merge.py` | Continuation deltas, inherited requirements and draft merging |
| `requirement_validation.py` | Requirement-slot completeness and clarification formatting |
| `scheduling_agent.py` | Bounded read-tool loop, solver call and draft construction |
| `scheduling_support.py` | Target meeting hydration, Java response conversion and solver inputs |
| `runtime.py` | LangGraph nodes/routes, HITL, persistence and workflow telemetry |

## Dependency direction

Dependencies flow from orchestration toward deterministic rules:

```text
app.workflow (public facade)
  -> runtime
       -> agents
       -> requirement_agent -> requirement_merge      -> requirement_source
                            -> requirement_parsing    -> requirement_source
                            -> requirement_validation -> requirement_source
                                                       -> requirement_parsing
       -> scheduling_agent -> scheduling_support -> requirement_source
                                                -> requirement_validation
  -> prompts / common / clarification
```

Implementation modules must not import the public facade, and deterministic
rule modules must not import Agents or the LangGraph runtime. This keeps the
dependency graph acyclic and lets rule changes be tested without constructing a
workflow.

## Where new code belongs

- Add a new source phrase or intent predicate to `requirement_source.py`.
- Add date/time/default interpretation to `requirement_parsing.py`.
- Add continuation or inheritance behavior to `requirement_merge.py`.
- Add required-field or slot-status behavior to `requirement_validation.py`.
- Add Java response mapping or solver input construction to
  `scheduling_support.py`.
- Add graph transitions, HITL behavior, persistence or emitted events to
  `runtime.py`.
- Add a public symbol only through `app.workflow` and update `__all__`.

The four runtime Agents remain `SupervisorAgent`, `RequirementAgent`,
`PolicyAgent`, and `SchedulingAgent`. Deterministic helpers are not additional
Agents and do not receive a general write-tool surface.
