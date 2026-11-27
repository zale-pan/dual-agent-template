# 结构化业务轮：从草稿到独立验收

以下命令均在已初始化的业务项目根运行。示例使用任务 APP、轮次 round-001、阶段 P1 和实施者 example-agent。首次启用时检查三项：

- Steward 已在目标环境完成轻量 P0，`.dual-agent/DEVELOPMENT_STATUS.json` 的 `project_startup.status` 为 `ready`。同一文件的 `phases` 包含实际阶段，例如 `{"id":"P1","pending_items":[]}`。
- 默认 `.dual-agent/COLLABORATION_POLICY.json` 为 `configured`。只有项目实际采用并完成必要控制核对后，Steward 才显式把 `policy_status` 设为 `active`。初始化、预览、转正均不会替你激活。
- 运行结构化执行器需要真实下发事件；仅在项目已明确启用事件账本时，将其状态设为 `enabled` 并按[事件协议](../EVENT_LOG_PROTOCOL.md)登记真实的 `project_initialized`。轻量工作单流程无需这一项。

## 1. 填一次可执行输入，预览待批范围

```powershell
python -B .dual-agent/tools.py new-round APP round-001 --structured
python -B .dual-agent/tools.py promote-round APP round-001 --phase P1 --agent example-agent
```

两条命令之间，填写本轮目录里的工作单和验收计划草稿。工作单写目标、业务约束及预期行为，可直接引用计划草稿的路径与命令。保留草稿已有身份字段，只编辑：

| 草稿字段 | 示例或含义 |
|---|---|
| authorized_write_paths | 业务写路径，如 `["src/calc.py"]`；相对项目根逐文件填写 |
| required_commands | 如 `["python -B -m pytest -p no:cacheprovider -q tests/test_calc.py"]`，使用本项目实际入口 |
| extra_inputs | 确实依赖并需冻结的附加源文件，如 `["tests/test_calc.py"]`；基础治理文件由工具补齐 |

无记录参数的 promote-round 只打印预览。其中 approved_paths 会**列出完整范围，包括本轮唯一 completion-report.yaml**；若用户不接受报告路径，这份结构化合同不能转正。review_sha256 绑定项目、任务、轮次、实施者、阶段、最终范围、命令及草稿/附加输入身份。

## 2. 日常路线：一次具体批准，一次实际交付，一次独立结论

仅在项目首次 P0 已核对为 ready、并已决定采用结构化执行器后，运行一次 `activate-project --ledger enabled --by <Steward身份> --basis <P0决定依据>`。完整调用前缀为 `python -B .dual-agent/tools.py`。这一步同步 policy_status 和账本开关，并登记首次初始化。批准依据填写实际记录。纯手工项目仍可直接使用工作单。

用户确认第 1 节预览的合同后，Steward 填入该次批准的身份、原话或出处、当时预览的摘要。只同意方向时继续准备草稿。

```powershell
python -B .dual-agent/tools.py dispatch-round APP round-001 --phase P1 --agent example-agent --approved-sha256 <已审预览的摘要> --by <批准人> --basis <实际批准原话或出处>
```

此命令用于共享工作区：生成合并的同意及批准记录，冻结合同，在正式位置逐文件读回，登记下发并绑定实施前范围基线。返回 manifest 与 receipt。实施 Agent 读该路径和绑定后开工；回执只证明共享工作区交付。读回失败时保留批准，修复原因后用原参数重试；合同变化须重新预览并取得批准。

实施 Agent 将改动、实测、未测项写进冻结范围内的 completion-report.yaml。Steward 收到后直接登记这份交付，再独立执行验收。日常无需单独登记「开始」或手填第二份提交说明。

```powershell
python -B .dual-agent/tools.py submit-round APP round-001
python -B .dual-agent/tools.py steward_workflow run
```

submit-round 会核对当前授权、实际下发、报告路径及字节，同步提交状态。范围外变化按新增、修改、删除列出，来源待核对；文件差异不能证明由哪个进程或 Agent 造成。核对失败保留提交事实。按第 4 节记一次独立结论并运行 close-round，工具生成一份 .dual-agent/docs/round-summaries/APP/round-001.md。独立结论的 changes、limitations、retention 可记录改动、限制与仍有消费者的材料。启用账本时其直接引用仍按既有保留规则处理，收尾命令不会删除材料。

## 3. 按需要使用分步命令

需分开记录讨论与批准、使用其他交付渠道或恢复旧轮次时，仍可按原流程用 promote-round --agreement/--approval、append-event、round_package --build 及真实交付回执。日常路线已执行的步骤不用重复。`steward_workflow summarize` 用于额外复核原结果；需要更新派生视图时运行 `review_views --write`。

默认模式为 acceptance。输出保存在项目同级的 `<项目名>-steward-output/<身份哈希>/`，按项目绝对路径、当前清单路径和模式区分。run 返回实际 output。需要自选目录时给 run 和 summarize 传同一个仓库外的 `--output`；路由切换后也可用它复查原轮次。显式使用 dispatch 模式时仅选择声明支持该模式的阶段。

`command_verdict` 表示阶段的执行结果。`verdict` 和 `publication_eligible` 表示本次证据能否用于验收。命令成功但工作区漂移时，run 退出 1，problems 与 workspace_drift 列出相对路径及新增、移除、修改类别。输入或 I/O 错误见标准错误。summarize 只读核对本次记录，结果输出到标准输出；原运行失败或证据不一致时返回非零。摘要本身不产生批准。

默认每次实际执行。只有确认可复用的只读检查才在冻结的 required_commands 对象中填写 `"reusable_readonly": true`。相同输出目录中，上一轮执行的证据合格、命令指纹和尝试记录字节一致时才复用。要求现场取证、有副作用或依赖未纳入指纹的外部状态的命令应保持默认。一次运行失效后，即使移除了引起漂移的文件，下一次也要重新执行。缺少绑定信息的旧记录保留供人工查看，不能自动作为有效证据。

## 4. 根据已有验收结论关闭本轮

先登记真实的 implementation_submitted，再由 Steward 独立验收并写下结论。复用 append-event 的决定记录格式：project、task_id、round_id、decision_id、role（task_steward）、decided_by、basis，以及每项检查的 result 和 observation。通过用 event_type=task_verified、decision=verified；失败用 event_type 和 decision 均为 verification_failed。记录路径按轮次唯一；命令输出或实施报告不能替代这份决定。

```powershell
python -B .dual-agent/tools.py close-round APP round-001 --record .dual-agent/artifacts/records/APP-round-001-verification.json
```

命令校验当前路由、任务行、轮次清单路径、结论及证据。先清空当前写权限，再同步终态事件（仅限已启用账本的项目），最后更新当前路由。成功任务进入 completed_details；失败任务留在 pending_items，权限同样关闭。冻结包保持原样。人工模式仍可按工作单收尾。

如果台账和事件已经正确收尾、路由仍停在 approved，传原决定记录即可补齐路由。遇到中断，使用同一命令和原记录恢复；若进程异常退出留下共用写锁，按原账本锁规则先核实持有者。重复执行不会多写事件。关闭成功后，修正轮仍须另建草稿、取得该轮批准并实际下发。同一任务的商讨事件按任务连续；修正轮转正仍需本轮同意和批准记录，账本已有 agreed 时只追加本轮 task_approved，不重复追加 consultation_agreed。

## 实施改动范围和输出

新转正的结构化轮次在登记 task_dispatched 时，在该轮目录保存 implementation-baseline.json，内容为清单身份、项目相对路径和 SHA-256。下发事件绑定它的摘要。Steward 应在实施者开始写入前完成这一步；如果已经开工，事后补录不能证明原始状态。旧包不会补造基线。

按当前任务行更新提交状态，并将本轮 Steward 的 started/submitted 决定记录写在 .dual-agent/artifacts/records/ 下，再用 append-event 登记。implementation_submitted 在标准输出返回 scope_check；changes 分别列出新增、修改、删除路径，outside_grant 只列冻结范围以外的变化。即使发现越界也记录本次提交及首败，命令返回 1，不能登记通过；按独立结论关闭失败轮。正式验收会复用准入时的盘点再核对一次，能发现提交后、测试开始前的新增变化。

比较涵盖已有未提交和未跟踪的普通文件。工具单独核对事件链及基线身份；当前任务行的执行状态可合法推进，新增且绑定本轮事件的 Steward 决定记录可写入。其他治理文件和已有决定记录也在范围内。沿用运行器的目录排除项 .git、__pycache__、.pytest_cache、.ruff_cache。此检查比较两个时点的文件内容，不能证明期间从未写后还原，也不覆盖空目录、仓库外文件或操作系统权限。验收命令自身引发的漂移仍由 run 的收尾检查报告。基线只含摘要，不允许进入下轮冻结输入，退役见[存储与清理](lifecycle/存储与清理.md)。

## 命令速查

| 调用 | 用途 |
|---|---|
| steward_workflow plan / plan --mode all | 默认预览验收；all 同时预览下发及验收阶段 |
| steward_workflow run / summarize | 实际执行冻结阶段 / 只读复核原结果 |
| steward_workflow audit | 兼容入口，运行相应阶段；正式说明使用 run |
| review_views --check --json | 生成机器可解析的视图核对结果 |

旧入口 steward_workflow review 及 --allow-missing-report 旁路已退役。独立结论由 Steward 记录。

## Windows 与 Linux 的运行期间稳定性

Windows 仍用原有 NTFS ChangeTime 和文件内容检查。Linux 运行器在准入盘点时为受管目录打开 inotify 监听，在收尾时独立重读内容并对照 inode、ctime 和收到的变更事件。普通写入后即使恢复原字节和 mtime，写入事件仍使本次证据失效。该检查不归因到某个进程，沿用原有排除目录。

WSL 项目可放在 Ubuntu 的 Linux 文件系统（例如 ~/projects）。支持表为 ext2/3/4、XFS、Btrfs、tmpfs；实测为 WSL Ubuntu ext4。Windows 共享盘（例如 /mnt/d 的 9p）、网络挂载和未识别类型不采信为完整运行证据。监听建立失败、目录监听丢失或事件队列溢出也会使验收失败。此能力针对常规文件系统写入，无法证明没有 mmap 写后还原、外部硬链接别名写入或内核以外的改动。

## 轮内例外清理

优先在关闭后清理。若合同已预见轮内清理，可在 extra_inputs 中冻结一份用户批准记录（.dual-agent/artifacts/records/ 下）。它写明 project、task_id、round_id、role=user、decision=approve_cleanup、decided_by，以及 approved_deletions（精确业务相对路径到原 SHA-256 的映射）。先按存储规则完成引用释放与真实清理。Steward 的实际回执同样位于 records 下，写明相同身份、role=task_steward、decision=cleanup_performed、decided_by、approval（批准路径）、approval_sha256 和 deleted（确已删除的路径到原 SHA-256）。用 submit-round 的 --cleanup-receipt 传回执。只在冻结批准、基线字节、回执和当前缺失状态一致时单列为 steward_removals。治理文件、冻结输入、实施写路径不能用此例外。工具不执行删除；它核实有据可查的处置，不能认定因果身份。临时新增且未冻结的处置需另行核对，不能追认成已批准操作。
