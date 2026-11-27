# <Task> / <Round>

English | [简体中文](轮次工作单.md)

## Four required fields for a small change

| Required field | This round |
|---|---|
| Goal | <Observable change and completion criteria> |
| Allowed files | <Each project-relative path and operation, such as modify src/calc.py; all other paths are read-only> |
| Acceptance | <Working directory, complete command and expected result; for documents, concrete manual review steps and criteria> |
| Approval and dispatch | <Valid P0 ready evidence, approver and approved worksheet version, named implementer, actual delivery location and Steward read-back result> |

The initial state is draft. Implementation starts after user approval and delivery read-back. External calls, spending, publication, additional writes and cleanup each require explicit authorization. The implementer returns actual changes. The Steward independently verifies and closes permissions, recording the decision and remaining issues in the round's single concise summary.

<details>
<summary>First use or changed startup conditions — lightweight P0</summary>

Record the project root, goal, Steward, implementer, approver, protected objects and independent acceptance entry point. Name the executor and criteria for each check. Exercise required automatic controls with positive and negative cases under the real identity and environment. If pre-write enforcement is promised, also confirm unchanged files after rejection. Register temporary locations, consumers and release conditions.

Start with pending. The Steward records ready when requirements are met or blocked when an essential capability is unavailable. Approve governance adaptation separately with its own scope. Later business rounds may reference a still-valid startup decision. Manual projects record it here; structured projects reference project_startup in .dual-agent/DEVELOPMENT_STATUS.json.

</details>

<details>
<summary>Additional detail when needed — fingerprints, boundaries, controls and temporary storage</summary>

| When applicable | Required information |
|---|---|
| Freezing external inputs or reusing historical evidence | Original source, readable version, SHA-256 or Git blob ID, consumers; also compare assertions, commands and environment for reuse |
| Paths alone do not define the scope | Functions, permitted operations, read-only objects and exact external output paths |
| Rejection branches or important side effects | Control ID, positive and negative inputs and modified fields, expected result, prohibited side effects, actual result |
| Temporary environments, logs, screenshots or candidate directories | Exact location, consumers, release condition and cleanup approval; reference an existing registration when available |
| Correction rounds or blocked checks | Original first failure, inherited passing evidence, untested items with reasons, changes requiring independent verification |

Run checks already required by P0 or the frozen contract. Leave inapplicable optional fields unused.

</details>
