# TASK_ID round-NNN verification template

- Decision: `verified` or `verification_failed`
- Verification owner: Task Steward
- Review mode: read-only implementation review
- Completion report parse: pass/fail
- Round input hashes: pass/fail
- Governance event chain before decision: pass/fail
- Scope check: pass/fail
- Focused diagnostics: small direct probes only;no near-full duplicate
- Full suite: one execution for the reviewed code fingerprint,observed result
- Lint: observed result
- Build: observed result
- Supplemental probes: observed result

Canonical evidence: `VERIFICATION_EVIDENCE_PATH`.

This blank template is not verification evidence. The Task Steward creates a real file
only after independently running the recorded checks.

- Execution rule and exact plan: paths and SHA
- Full collection after ordinary failures: observed completion
- All confirmed findings: reproduction,control,impact and correction
- Unexecuted checks: only affected safety/provenance/dependency reason;never count as pass
- Frozen package/original plan preserved;review addendum binding if applicable

This template records planned structure only. Ordinary failures do not stop unrelated checks. No verified decision until every required check is resolved.

## Execution reuse and elapsed time

- Reviewed runners reused: exact path/version/SHA;missing capability and minimal adaptation if any.
- Actual change counts: implementation/test/doc + report + attributable Steward outputs = total;reference inventory.
- Permission preflight: normalized allowed/denied sets disjoint;current report positive,old/unauthorized future reports negative.
- Raw results: canonical locations/hashes;necessary durable copies and first failures/audit corrections retained.
- Interactive process: ready/health/real signal/native exit/resource proof;separate shell exit,manual control and idle waiting.

| Stage | Start/end or interval | Seconds or unknown | Measured/estimated basis and evidence | Parallel overlap |
| --- | --- | --- | --- | --- |
| Main regression | | | | |
| Independent probes | | | | |
| Authoritative checks | | | | |
| Inventory/candidate/publication/cleanup | | | | |
| Steward adaptation/documentation | | | | |
| Interactive idle | | | | |
| Audit rework | | | | |

Report end-to-end wall time separately. Do not sum overlapping rows,attribute all gaps to tests/thinking,or claim unmeasured acceleration. Explain avoidable overhead and corrections without reducing required coverage. This template does not implement a runner,timer or automatic cache.
