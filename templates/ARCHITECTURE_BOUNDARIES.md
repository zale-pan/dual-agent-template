# 架构边界与组件责任

## 1. 系统上下文

描述系统边界、外部参与者、上游输入、下游消费者和信任边界。

## 2. 主数据流

```text
SOURCE
  -> ADAPTER
  -> CANONICAL_MODEL
  -> DOMAIN_COMPONENT
  -> OUTPUT / EVIDENCE
```

## 3. 组件责任

| 组件 ID | 责任 | 读取 | 输出 | Owner | 可用性 | 修改模式 |
|---|---|---|---|---|---|---|
| component_a | ONE_RESPONSIBILITY | INPUT | OUTPUT | TEAM | planned | proposal_only |
| protected_a | PROTECTED_RESPONSIBILITY | INPUT | OUTPUT | EXTERNAL | existing | forbidden |

## 4. 关键禁止耦合

- 组件 A 不得直接读取组件 C 的完整私有存储；
- 诊断组件不得修改正式知识；
- 编排组件不得绕过策略校验；
- 派生处理不得覆盖原始证据；
- 实施代理不得修改受保护面。

根据项目实际替换以上示例。

## 5. 数据所有权和不可变性

| 数据 | Owner | 可变性 | 保留/版本策略 | 敏感性 | 可被哪些组件读取 |
|---|---|---|---|---|---|
| raw_input | DATA_OWNER | immutable | VERSIONED | high | LIST |
| derived_output | COMPONENT_OWNER | append/versioned | POLICY | medium | LIST |

## 6. 外部依赖

| 依赖 | 版本/接口 | 当前状态 | 离线替代 | 阻塞哪些阶段 | Owner |
|---|---|---|---|---|---|
| EXTERNAL_API | VERSION | unavailable | fixture/adapter | P2 | EXTERNAL_OWNER |

## 7. 设计决策

关键决策应包含：决策、原因、备选方案、后果、验证方式和重新评估条件。不要把
尚未确认的供应商私有格式固化为架构事实。
