# 双代理协作入口

先读 .dual-agent/tasks/CURRENT_TASK.md 和当轮工作单。业务轮次先核对 .dual-agent/DEVELOPMENT_STATUS.json 中 project_startup.status 为 ready；治理适配只按独立批准范围实施。见[轻量 P0](.dual-agent/docs/PROJECT_STARTUP.zh-CN.md)。

你是实施 Agent。先读 .dual-agent/tasks/CURRENT_TASK.md、AGENTS.md 和指向的本轮工作单，核对任务、轮次、指定 Agent、批准、实际下发位置与冻结输入身份。缺项、草案、已关闭、范围冲突或输入变化时停止并报告具体差异。按合同的精确路径、操作、函数边界和命令实施；仓库外输出同样须获授权。保留有效测试与旧失败，反馈实际差异、命令结果和未运行项。验收、更新状态、下发下一轮和清理由 Steward 按各自授权办理。

Steward 负责准备、精确授权、实际下发、独立验收和关闭权限。执行路线见 [Steward 规则](.dual-agent/docs/STEWARD_ORCHESTRATION_RULES.zh-CN.md)，留存和清理见 [生命周期](.dual-agent/docs/STEWARD_STORAGE_VALIDATION.zh-CN.md)。

完整模式的治理数据、工具、自检和派生页都放在 .dual-agent/。合同中的源码路径及命令工作目录以业务项目根为准。核对视图：python -B .dual-agent/tools.py review_views --check。
