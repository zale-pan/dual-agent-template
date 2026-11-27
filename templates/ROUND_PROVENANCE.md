# Round input provenance

For contracts that explicitly use a structured package, use the following layout. For a manual round, bind the same input identity and actual dispatch in the current worksheet.

## Package layout

In an installed project use repository-relative paths in the manifest: governance paths begin with .dual-agent/ (except AGENTS.md); business paths such as src/calc.py stay relative to the business root. Snapshot paths preserve the complete source path after inputs/. Run python -B .dual-agent/tools.py round_package from the project root.

The Task Steward creates `.dual-agent/task_interactions/<task-id>/round-NNN/package/` before dispatch:

```text
package/
  round-manifest.json            # inventory; never an input to itself
  inputs/<repository-relative-path>  # path-preserving raw-byte copies
```

The package contains **every Steward-provided input the Implementation Agent is told to
use**, not merely hashes of mutable live documents. Minimum inventory: configured stable
prompt source, `AGENTS.md`, `.dual-agent/COLLABORATION_POLICY.json`, `.dual-agent/PROJECT_GOALS.json`,
`.dual-agent/DEVELOPMENT_STATUS.json`, `.dual-agent/tasks/CURRENT_TASK.md`, agreed consultation, English task
document, exact task manifest, and round instruction. Add every optional design/reference
document and supplied source baseline named as an input. A named but unlisted input is a
dispatch failure. Do not implicitly package a whole repository or copy secrets, credentials,
real enterprise data, or protected inputs. Large/binary inputs need an explicit path, size,
hash, and approved inclusion; otherwise narrow the task. Runtime tool outputs are evidence,
not retroactive frozen inputs.
The approved task manifest declares a closed, exact `governed_input_paths` inventory;
the builder requires it to equal the package inventory. This is distinct from
`allowed_write_paths`, which authorizes outputs only.

`.dual-agent/tasks/CURRENT_TASK.md` remains a **mutable route**, not a historical fact source. It
points to the current package manifest and carries approval/draft state. Its pre-dispatch
bytes are copied into `package/inputs/.dual-agent/tasks/CURRENT_TASK.md`; that snapshot is the round
input. The route must not contain the package manifest digest, avoiding a self-hash cycle.
The manifest itself is bound by the `task_dispatched` event artifact hash. The Implementation
Agent uses the live route only to find the current package, then reads governed documents
**from the package**, not mutable originals. Stop if route, snapshot, event, task/round ID,
approval, consultation, or exact write paths disagree. Historical packages remain valid
when the live ledger moves on.

The exact authorized `round-NNN/completion-report.yaml` is outside `package/`. Verification
and event records are also outside it. The Implementation Agent may never write, delete,
rename, or replace package paths. Output authorization never covers `package/`.

The manifest also names a small `agent_read_set`: task contract, exact authorization, and
round instruction by default. The agent verifies hashes for **all** package entries with
a tool, but does not load all their contents into model context. It opens other snapshots
only when a numbered requirement needs them. Package copying improves provenance; it
does not by itself reduce model tokens, and unnecessary broad reading defeats the goal.

## Hash and lifecycle rules

Use `round-manifest.template.json`. Each input records `kind`, advisory live
`source_path`, authoritative `snapshot_path`, raw-byte length, and lowercase SHA-256.
Paths are POSIX-style, repository-relative, exact, unique after Unicode normalization and
case folding, and must resolve inside the package. Reject traversal, absolute paths,
symlinks/reparse points, missing files, duplicate paths, and unexpected files under
`package/inputs/`. Hash raw bytes; never normalize newlines, Unicode, or whitespace.
The manifest is deterministic UTF-8/LF JSON; its own hash is in the dispatch event.

Before dispatch, compare each copy byte-for-byte with its declared live source and check
the complete inventory, scope, consultation, status, generated views, and project state.
After dispatch, **never require historical copies to match mutable live sources**. Rehash
the package and validate event binding. Any correction or added input needs a new package.
Do not repair an old one. If the provider cannot export its received system prompt, the
prompt digest proves only the configured repository source. Hashes are not external
signatures, trusted timestamps, or OS immutability; use ACLs/separate identities for that.



Do not select an earlier package as an input. Protect each active consumer until release; see docs/lifecycle/冻结与复用.md and docs/STEWARD_STORAGE_VALIDATION.zh-CN.md.
