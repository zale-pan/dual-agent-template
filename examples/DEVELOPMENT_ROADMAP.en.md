# Development Phases and Acceptance Roadmap

> This document explains phase design. `DEVELOPMENT_STATUS.json` is authoritative for
> live status, tasks, evidence, blockers, and next actions.

For every phase document prerequisites, primary outcome, complete task set, dependencies,
external requirements, and an evidence-backed exit gate. Order phases by dependency,
not by team or directory. Do not treat configured schemas, interfaces, or empty stores as
implemented, exercised, or verified capabilities.

| Phase | Outcome | Prerequisite | Exit gate |
|---|---|---|---|
| P0 | Read-only project, scope, provenance, and event-chain checks with positive/negative drills | Planning complete | Controls independently verified and policy activated |
| P1 | Bounded offline capability and representative fixture replay | P0 verified | Functional, error, and audit-chain evidence resolves |
| P2 | Stable external integration and security hardening | P1 verified and external interface stable | Online/offline reconciliation passes and protected surfaces remain unchanged |
