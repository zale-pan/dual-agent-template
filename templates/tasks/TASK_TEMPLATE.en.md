# TASK_ID: Task title

## Control

Record status, owner role, Goal/AC references, dependencies, consultation, manifest,
human-review companion, intended interaction round, completion report path, and
Task-Steward-owned round-manifest path. A draft never grants permission.
List every Steward-provided input in the JSON manifest's exact
`governed_input_paths`; the package builder rejects omissions and extras. This list is
the closed input inventory, while `allowed_write_paths` is the separate closed output
authorization. Do not include protected secrets or an implicit directory.

## Objective and reason for ordering

State one observable outcome and why its verified prerequisites make it the next task.

## Confirmed facts and unknowns

Separate repository/input facts from assumptions, missing data, and external dependencies.

## Authorized write scope

List exact files. Stop and request a revised manifest before writing any additional path.

## Requirements and error model

Number every required behavior. Define stable error identity, safe context, actionable
remediation, fail-closed cases, determinism, immutability, and secret-handling rules.

## In scope / out of scope

Name both explicitly, including later phases and protected surfaces.

## Test matrix and commands

Map success, boundary, malformed input, regression, lint, build, and supplemental probes
to exact commands. Do not treat file or schema presence as implementation evidence.

## Completion report

Authorize exactly one current-round report path. The Implementation Agent may claim only
`submitted`; it records the verified round-manifest digest but never edits the manifest or
event log. The Task Steward independently decides `verified` or `verification_failed` and
appends the corresponding governance event.

## Independent acceptance execution plan

Freeze the project execution rule and an exact plan:content/environment fingerprints,AC-to-assertion mapping,commands/probes/order,dedup basis,cost estimates,build/install triggers and safety/dependency stop conditions. Ordinary product failures do not stop unrelated checks;collect findings,run one full suite for code review and report all results. Do not require a near-full focused suite followed by the same full suite. Use tasks/_templates/acceptance-plan.template.json as a configured scaffold,never as evidence. Preserve original immutable input packages;any Steward-only review addendum must bind the original manifest,plan,user authorization and metadata event.
