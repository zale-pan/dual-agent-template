# Append-only governance event protocol

Current authorization and the round summary follow docs/STEWARD_ORCHESTRATION_RULES.zh-CN.md. The event-specific fields and transitions below apply only while a real consumer requires that ledger. Its five exit conditions are in docs/STEWARD_STORAGE_VALIDATION.zh-CN.md.

`.dual-agent/artifacts/events/governance-events.jsonl` records accepted governance transitions. It is
an audit history; `.dual-agent/DEVELOPMENT_STATUS.json` remains authoritative for current state.

## 使用 append-event

**启用条件。** 默认项目保持 unused。只有用户明确选择事件账本、Steward 已记录消费者及退出条件后，才手工将 `.dual-agent/DEVELOPMENT_STATUS.json` 中 `governance_event_log.status` 设为 `enabled`，保留原配置的精确路径。命令只读取这份配置。普通工作单流程无需运行它。

**输入是已有决定的记录。** 在业务项目根运行命令，所有引用均用仓库相对路径。为同一决定保留同一 decision_id 和同一份不可变 JSON。它既是证据也是重试键；不要在重试时重新生成记录。真实批准或独立验收先由人完成，Steward 将当时的决定记入记录。工具只验证本地数据一致性，不能认证发言者身份或证明人真的作过该决定。

例如，用户已批准 APP-42 的 round-001、当前台账也已登记该轮和精确授权后，已有批准记录可以采用以下结构：

```json
{
  "project": "MyProject",
  "decision_id": "APP-42-round-001-user-approval",
  "event_type": "task_approved",
  "task_id": "APP-42",
  "round_id": "round-001",
  "decision": "approved",
  "role": "user",
  "decided_by": "实际批准人",
  "basis": "在此写明实际批准的合同版本和决定",
  "approved_paths": ["src/calc.py"],
  "evidence": [".dual-agent/task_interactions/APP-42/round-001/approved-contract.md"]
}
```

```powershell
python -B .dual-agent/tools.py append-event project_initialized --record .dual-agent/artifacts/records/project-init.json
python -B .dual-agent/tools.py append-event task_approved --task APP-42 --round round-001 --record .dual-agent/artifacts/records/approval.json
```

| 事件 | 记录要求 |
|---|---|
| 项目初始化、项目元数据更新 | task_id、round_id 为 JSON null，命令省略对应参数。role 为 task_steward、decision 为 initialized。首条必须是 project_initialized。 |
| 商讨同意、任务批准 | role 为 user，decision 分别为 agreed、approved。批准路径逐项与该轮台账授权比对，商讨状态应为 agreed。 |
| 实际下发 | role 为 task_steward，decision 为 dispatched；记录 release_channel、route_sha256、manifest_sha256，evidence 包含该轮冻结清单。工具读回当前路由、冻结包及链中的本轮批准。 |
| 实施开始、提交、阻塞、替代 | Steward 记录实际观察到的事实，decision 分别为 implementation_in_progress、implementation_submitted、blocked、superseded，并与当前任务状态对照。 |
| 验收通过或失败 | role 为 task_steward；decision 为 verified 或 verification_failed。checks 是非空数组，每项含 result（passed、failed 或 blocked）和实际 observation。全部通过才可登记 verified；失败裁决须有失败或阻断项。 |

证据引用的实际字节由工具散列。绑定同一记录的重试返回 already_recorded 和原序号，即使任务后来已推进；同一 decision_id 换内容则拒绝。请在追加期间保持当前授权及其证据稳定；所有写入者都使用此命令。命令串行读取原链、检查状态/序号/证据、计算 UTC 时间与哈希并原子发布；普通错误保留原链。锁等待上限为 10 秒，不自动抢占。进程被强制终止后如留有 .lock 或 .append-*，先确认写者已退出，读回现有账本，按项目清理规则处理准确残留路径，再用原决定重试。正常追加和重试均不会批准任务、生成验收结论、更新当前状态或授予权限。

## Ownership and creation

- Only the Task Steward appends events.
- The Implementation Agent has no event-log write permission.
- A copied template contains no event log. The Task Steward creates the log only when the
  target project records its first real event.
- One UTF-8 JSON object is appended per line. Existing lines are never edited or removed.

## Required event fields

- `schema_version`, globally unique `event_id`, positive `sequence`, `event_type`, and
  RFC 3339 UTC `occurred_at`;
- durable `actor_role` and optional runtime assignment;
- `task_id` when the event concerns a task;
- `state_domain` plus `previous_state` and `new_state` when state changes;
- `artifact_hashes` for artifacts that justify the transition;
- `previous_event_hash` and `event_hash`;
- optional `reason`, `limitations`, and `supersedes_event_id`.

## Hash-chain rules

To compute `event_hash`, remove only the `event_hash` field and serialize the remaining
object with RFC 8785 JSON Canonicalization Scheme (JCS). Hash those exact UTF-8 bytes with
SHA-256 and store lowercase hex. NaN, Infinity, duplicate keys, and non-JSON values are
invalid. `sequence` is an integer; times and hashes are strings.
The first event uses `previous_event_hash: null`; every later event copies the immediately
preceding event's `event_hash`. Newline bytes are not part of the event hash.

Do not include `governance-events.jsonl` itself in `artifact_hashes`; that would create a
circular digest. Hash an immutable ledger transition baseline, round manifest, report,
or verification artifact. Do not hash a mutable live ledger or generated view directly:
later legitimate changes would break historical event validation.

## Minimum transition events

- `project_initialized`;
- `consultation_agreed`;
- `task_approved`;
- `task_dispatched`;
- `implementation_started` when observable;
- `implementation_submitted`;
- `verification_failed` or `task_verified`;
- `task_superseded` or `task_blocked` when applicable.
- `governance_metadata_updated` only for an authorized project metadata self-transition
  (`initialized` → `initialized`) linked to an immutable baseline; it does not approve
  implementation or prove independent verification.

Corrections append a new event and may identify `supersedes_event_id`; they never rewrite
history. The project checker must verify JSON parsing, unique monotonic sequence numbers,
event IDs, RFC 3339 timestamps, the complete hash chain, allowed transitions per
`state_domain`, referenced artifact hashes, and agreement between the final relevant event
and the current ledger state.

An intact hash chain detects repository-record tampering; it is not an external timestamp,
signature, immutable store, or operating-system security boundary.

## First-consultation evidence and current-round dispatch

Follow CONSULTATION_WITNESS_RULES.md. Extract task-specific states only from hash-valid
immutable DEVELOPMENT_STATUS.json baselines bound by earlier events. For a first
consultation event, the latest earlier position is authoritative; all artifacts at that
position must agree. Older different states are chronological evolution. Future evidence
and the live ledger cannot prove the past. Once tracked, consultation follows its event
chain; subsequent metadata never resets it. Invalid artifacts always remain errors.

Validate every historical event/hash/artifact, but reconcile live dispatch_status only
with the task's current round. A previous round's dispatched event cannot approve the new
round. task_dispatched must bind the exact current manifest hash. Shared-workspace release
also binds a Steward-owned receipt of route hash, manifest hash, release time and channel;
publication does not imply provider receipt, start or successful implementation.

For append-event, validate the proposed chain in memory under the writer lock before publishing the extended ledger. Existing event bytes are preserved.

Before expensive projection checks,validate explicit identities,transition fields and
reference locations. Bind immutable baselines or package snapshots,never live proposals,
ledgers or generated views. Preserve a rejected external candidate and its raw error;
repair only the unaccepted projection and resume affected checks in the same candidate
when safe. Do not recopy the whole history or rerun unchanged business suites for event
metadata fixes. Approved and dispatched states may be checked in one candidate and
published in one governance operation with distinct append-only events and a real
release receipt. Follow docs/STEWARD_ORCHESTRATION_RULES.zh-CN.md section 4: retain
mandatory event-chain coverage and actual publication readback without automatically
running a second full history check. Reuse a verified prefix only while its referenced
objects and relevant path semantics also remain valid. Validate the new events, state
transitions and bindings; after-preflight changes invalidate affected assertions.
If existing trusted tools cannot supply the missing proof without a full check, record
the inspected capability gap, exact command/coverage and cost before that fallback.
Prefix equality alone is insufficient, and this rule does not add incremental checking.

Baseline inventory files and migration/recovery indexes that justify a transition must
have immutable paths and hashes with documented capture scope. Temp paths cannot be the
sole durable basis of later acceptance. Preserve original event references when objects
move;append versioned migration evidence and verify copied bytes rather than rewriting
history. Follow docs/STEWARD_STORAGE_VALIDATION.zh-CN.md for retention and proof-loss
handling;neither cleanup nor a generic request to continue authorizes a historical waiver.

## Evidence lifecycle

Follow docs/STEWARD_STORAGE_VALIDATION.zh-CN.md. Active inputs remain immutable and complete. Once independently closed and unpinned, a round retains one concise Markdown summary; obsolete packages, reports and runtime outputs may be retired through a separately verified project checkpoint. This overrides indefinite-original-retention wording, not authorization, historical decisions or active integrity checks. Current bundled checkers do not yet support retirement: adapt and independently verify them before deleting bound historical originals.
