# TASK_ID round-NNN dispatch template

> Create this file only after the user consultation is recorded as `agreed`, the task
> document and manifest are approved, and the current-task pointer is published.

- Task ID: `TASK_ID`
- Round: `round-NNN`
- Status at dispatch: `approved`
- Runtime: `IMPLEMENTATION_RUNTIME`
- Current task pointer: `.dual-agent/tasks/CURRENT_TASK.md`
- Authoritative task: `TASK_DOC_PATH`
- Manifest: `TASK_MANIFEST_PATH`
- Round package manifest: `ROUND_PACKAGE_MANIFEST_PATH`
- Completion report: `COMPLETION_REPORT_PATH`

Read the current task pointer and follow its order. Write only manifest-authorized paths.
Before editing, verify every package input by raw-byte SHA-256 using a tool. Read the
minimal `agent_read_set` first and open other snapshots only as needed. Stop on a missing
file, unexpected package member, mismatch, or live-route/package disagreement.
Run every required command and write the structured report. Stop and report a scope
exception if another path is necessary. Do not update status or verification evidence.

This template is not a dispatch record. Copy and fill it only after approval.

Approval is not actual dispatch. Require the genuine task_dispatched event binding this
exact manifest hash, and compare explicit task_id/round_id across live/frozen task,
route and package plus the exact current report path. Frozen preparation records do not
prove provider receipt.
