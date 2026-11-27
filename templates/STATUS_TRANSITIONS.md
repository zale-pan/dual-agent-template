# Status transition reference

Current authorization and the round summary follow docs/STEWARD_ORCHESTRATION_RULES.zh-CN.md. The event-specific fields and transitions below apply only while a real consumer requires that ledger. Its five exit conditions are in docs/STEWARD_STORAGE_VALIDATION.zh-CN.md.

| State | Meaning | May be set by |
|---|---|---|
| `draft` | Listed or being discussed; not authorized for implementation | Task Steward |
| `ready_for_review` | Consultation inputs are complete, but final user agreement or package approval is pending | Task Steward |
| `approved` | User agreement is recorded and the complete task package passes consistency checks | Task Steward |
| `implementation_in_progress` | Implementation Agent has started the approved task | Task Steward, from observed workflow state |
| `implementation_submitted` | A completion claim exists and awaits independent verification | Task Steward, after preserving the submitted report |
| `verification_failed` | Independent verification found a reproducible gap | Task Steward |
| `verified` | All acceptance gates have resolvable independent evidence | Task Steward |

The Implementation Agent may write `implementation_status: submitted` inside its YAML
report. That is a claim value, not a ledger state transition. The Task Steward preserves
the claim and, if the report exists and is attributable to the approved round, may set
the ledger state to `implementation_submitted` before verification.

`blocked`, `waiting_for_dependencies`, and similar readiness values belong in an explicit
blocker/readiness field; they are not substitutes for the execution-state vocabulary.

Every accepted transition is also appended as a Task-Steward-owned event following
`EVENT_LOG_PROTOCOL.md`. The event log records history but does not replace the current
state in `DEVELOPMENT_STATUS.json`. A `task_dispatched` event must include the SHA-256 of
the frozen round input manifest.

Forbidden transitions:

- `draft -> approved` without an `agreed` consultation and matching task package;
- `draft -> verified` from file presence alone;
- `implementation_submitted -> verified` based only on the completion report;
- `verification_failed -> verified` without a new round and complete rerun;
- incrementing phase completion for `implementation_submitted` or `verification_failed` work;
- overwriting the evidence that justified an earlier transition.

## Consultation is a separate domain

| State | Meaning |
|---|---|
| not_started | Listed task, discussion not yet begun; no authorization |
| awaiting_discussion | Discussion inputs/design not complete |
| awaiting_decision | Concrete scope awaits user decision |
| ready_for_review | Discussion prepared for final review |
| agreed | Genuine user agreement recorded |
| rejected | User rejected the proposal |

Use CONSULTATION_WITNESS_RULES.md for temporal metadata preparation and the first-event
predecessor rule. Never merge all historical states into one conflict set. At the latest
eligible earlier event position, conflicting artifacts still reject. not_started may
only reach its first agreed event; an already tracked agreed state cannot be reset by
later metadata or accept duplicate agreement. Execution approval still requires all gates.

## Recovery and round identity

verification_failed -> approved requires a strictly new round, frozen prior failure
evidence, unchanged Goal/AC and implementation/test paths, except the new exact report
path. Bind the prior failure and new manifest; independently rerun before verification.

An approved package never dispatched/started and having no report may be replaced in a
new round without claiming implementation. Preserve the old preparation, retain scope
except the new report path, and bind replacement by a metadata event and new dispatch.
Old reports and accepted events are never edited. Already recorded user consent is not
requested again unless the scope or unresolved decision actually changes.

## Evidence lifecycle

Follow docs/STEWARD_STORAGE_VALIDATION.zh-CN.md. Active inputs remain immutable and complete. Once independently closed and unpinned, a round retains one concise Markdown summary; obsolete packages, reports and runtime outputs may be retired through a separately verified project checkpoint. This overrides indefinite-original-retention wording, not authorization, historical decisions or active integrity checks. Current bundled checkers do not yet support retirement: adapt and independently verify them before deleting bound historical originals.
