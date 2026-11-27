# Dual-Agent Development Template

English | [简体中文](README.zh-CN.md)

A user approves the scope, a Task Steward dispatches work and independently verifies it, and an Implementation Agent makes the approved changes.

## Getting started

For small changes, use the [round worksheet](templates/docs/lifecycle/round-worksheet.en.md) and give each agent its role prompt: [Task Steward](TASK_STEWARD_BOOTSTRAP_PROMPT.en.md) and [Implementation Agent](IMPLEMENTATION_AGENT_BOOTSTRAP_PROMPT.en.md). Fill in the goal, allowed files, acceptance commands and approval. Expand the startup section for the first round and the evidence section when needed.

Every new project starts with a lightweight P0 review. Record the project root, goal, responsible people or agents, protected files and independent acceptance entry point. Exercise every automated control needed by the first round with a valid input and a rejecting input in the actual environment. If a required control needs adaptation, approve a separate adaptation task. Dispatch business work once the startup decision is ready.

Manual users can copy the three linked English documents directly. The initializer's manual mode currently copies the Chinese documents. Run one of these commands from the template root:

```powershell
python -B init_project.py "D:/Projects/MyScript" --name "MyScript" --manual
python -B init_project.py "D:/Projects/MyProject" --name "MyProject"
```

The Python tools require Python 3.10+ and use the standard library. Structured initialization creates AGENTS.md and .dual-agent/ in the target, fills in the project name, generates three review views, and includes the English implementation prompt required by packaging. The project starts in draft with P0 pending. Complete its project identity and controls before dispatch.

Initialization checks conflicts before writing. Existing identical files are accepted; different content at the same path stops initialization for manual resolution. Keep the target free of concurrent writes. For existing projects, complete active rounds using their frozen layout. The initializer does not migrate historical packages.

From a structured project's root, check its generated views with:

```powershell
python -B .dual-agent/tools.py review_views --check
```

Governance tests are optional. Add --with-governance-tests during structured initialization if the target needs them. This option is mutually exclusive with --manual. With pytest installed, run those tests from .dual-agent using `python -B -m pytest -p no:cacheprovider tests/governance`. Run business tests from the business project root.

Repository checks verify inputs and changes. Enforcement before a write depends on the agent host's restricted tools or OS permissions. Verify both an allowed write and a rejected out-of-scope write under the actual implementation identity, including unchanged file contents after rejection, when that protection is required.

## Preparing a round

From the initialized project root:

```powershell
python -B .dual-agent/tools.py new-round APP-42 round-001
```

This creates .dual-agent/task_interactions/APP-42/round-001/轮次工作单.md with task, round, path and draft fields. Generated worksheet text currently uses Chinese. English manual users can use the English worksheet linked above.

Add --structured on the first creation to also generate instruction.draft.md and acceptance-plan.draft.json. Commands and authorized paths start empty. An existing round directory causes a nonzero exit while preserving its contents. Continue editing the existing draft.

The Steward fills in the goal, exact scope and acceptance criteria for review. Creating a draft leaves authorization and P0 unchanged. Approval, frozen inputs and actual delivery are separate decisions recorded before implementation.

For promotion, field synchronization, freezing and execution, follow the [structured round guide (Chinese)](templates/docs/STRUCTURED_ROUND.zh-CN.md).

## Dispatch, verification and closure

1. Confirm the P0 decision and agree on one substantive round contract.
2. Record user approval, freeze the required inputs, deliver them to the named implementer, and read back the actual delivered files. Dispatch checks identity and delivery.
3. The implementer makes the authorized changes and reports actual results.
4. The Steward reviews the diff and independently runs the affected checks. Reuse earlier results only when their inputs, dependencies, commands and environment remain valid. Record failures and checks that were not run.
5. Close implementation permission and retain one concise round summary. Materials with active consumers remain until references are released. List exact cleanup targets and obtain authorization before deletion.

In a structured shared workspace, dispatch-round binds approval to delivery, submit-round registers the implementation submission, and close-round closes permissions using the independent acceptance decision. See the structured guide for their complete record formats.

## Optional event ledger

Projects explicitly enabling the ledger can use `python -B .dual-agent/tools.py append-event` to record decisions already made. Its evidence requirements, retries and concurrency behavior are described in the [event protocol](templates/EVENT_LOG_PROTOCOL.md). Projects with the default unused ledger continue with the worksheet and short summary.

External calls, spending, publication, additional writes and cleanup require explicit authorization. A tool records decisions made by the responsible people or agents.

## Testing the template

Install pytest 8+ in your Python environment. From the template root, run the following in PowerShell:

```powershell
$env:PYTHONPATH = (Resolve-Path templates).Path
python -X utf8 -B -m pytest -p no:cacheprovider -q tests templates/tests -rs
```

On Linux:

```sh
PYTHONPATH=templates python3 -X utf8 -B -m pytest -p no:cacheprovider -q tests templates/tests -rs
```

Some checks use platform-specific filesystem capabilities. Read the reported skip reasons when interpreting results.

## Planning a project

Before implementation, describe the users, purpose, deliverables, exclusions and protected areas. Turn goals into observable acceptance criteria, define component responsibilities and interfaces, then identify phases, dependencies and exit conditions. Link goals and acceptance criteria to tasks and review the first task for readiness.

Small projects can keep this information in the worksheet. Larger projects can use the structured planning files and generated review views. The optional Python modules include governance.round_package, governance.review_views and governance.steward_workflow. The workflow runner requires the structured task, authorization and event inputs it validates. schema_version identifies a data format.

Further material currently includes Chinese [startup guidance](templates/docs/PROJECT_STARTUP.zh-CN.md), [orchestration rules](templates/docs/STEWARD_ORCHESTRATION_RULES.zh-CN.md), [acceptance rules](templates/docs/ACCEPTANCE_EXECUTION_RULES.zh-CN.md), [storage rules](templates/docs/lifecycle/存储与清理.md), and [design notes](DESIGN_NOTES.zh-CN.md). The [planning examples](examples/README.md) also include some English task and roadmap documents.

## Upgrading a structured project

Run from the template root using the original project name. If the project was initialized with --with-governance-tests, include that option in both commands.

```powershell
python -B init_project.py "D:/Projects/MyProject" --name "MyProject" --upgrade-preview
```

Review the JSON lists of missing files, tools to replace and project documents requiring manual comparison. Confirm that local tool edits may be replaced, then use the returned plan_sha256:

```powershell
python -B init_project.py "D:/Projects/MyProject" --name "MyProject" --upgrade --plan-sha256 "<plan_sha256 from this preview>"
```

If the plan changes, preview and review again. Keep the target free of concurrent writes throughout the upgrade. Manual-mode documents are compared and merged individually by the project maintainer.

## License

Developed from the dual-agent workflow used in ForeContract. Copyright 2026 Zale Pan. Licensed under [Apache License 2.0](LICENSE).


## Platform support and macOS CI

Windows and Ubuntu have been tested locally. macOS is included in the GitHub Actions matrix with Python 3.10, 3.11 and 3.13; its first remote run is pending. Every matrix job runs the complete root and governance suites and reports skip reasons. Failures fail the job. The workflow can also be started manually after upload.

Manual worksheets and prompts can be used on macOS. The Python workflow runner now selects a macOS kqueue vnode witness for directories and regular files. Read-session caching observes those notifications, and final inventory reads are independent of the admission cache. A registration or event-read failure prevents acceptance eligibility. Notifications for directories are retained conservatively even if they may concern an excluded child.

Portable API-double tests cover notification handling, cache invalidation with identical signatures, clean and changed inventories, monitoring failures and descriptor cleanup. These tests validate integration logic; real macOS behavior remains pending CI.

The macOS CI must verify the existing clean-and-restored-write control, the new native vnode test (restored bytes and mtime, transient directory changes), symlink rejection, and subprocess/port cleanup. Large trees may exhaust file descriptors because each watched file and directory holds one descriptor; registration failure rejects eligibility. Establish supported filesystem and project-size limits from real macOS results before claiming broader support. Network filesystem behavior remains unverified.

Three expected platform skips on macOS are Windows open-handle deletion semantics, Windows command syntax, and the Linux notification queue test. Symlink tests should normally run. Review any additional skips individually. The clean-workspace and restored-write controls have no platform skip.

The CI matrix expands platform coverage; macOS support remains unverified until the relevant controls pass on a real macOS runner.
