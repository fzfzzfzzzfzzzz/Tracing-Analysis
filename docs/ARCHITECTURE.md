# 架构与数据流

## 分层

1. `schema.py` / `graph.py`：节点、边、生命周期和增量图。
2. `archive.py` / `capture.py`：原始工具结果归档和 wrapper 捕获。
3. `lifecycle.py` / `failure_cards.py`：状态推断、operation-scope 失败聚合、分类与过期。
4. `context.py`：统一 `ContextManager.select()` 接口、card-aware 主方法及全部对照/消融条件。
5. `runtime.py`：固定 tool-calling scaffold；实验条件只替换 context manager。
6. `adapters/tau.py`：τ-bench/τ³ JSON 到 TraceGraph 的适配。
7. `experiments.py`：离线现象、Oracle、在线前缀回放、baseline/ablation 和结果聚合。
8. `integrations/tau3_agent.py`：在当前 τ³ 环境内运行的半双工 live agent。

## 运行时数据流

```mermaid
flowchart LR
  U["User / Goal / Constraint"] --> G["Runtime TraceGraph"]
  G --> C["Context Manager"]
  C --> V["Active Context View"]
  V --> L["LLM"]
  L --> D["Decision / ToolCall"]
  D --> T["Tool Wrapper"]
  T --> O["Observation / Error"]
  O --> A["Content-addressed Archive"]
  O --> G
  A --> H["Failure Card / Summary / Recoverable Handle"]
  H --> G
```

每个工具结果先归档，再进入图。context manager 不改变原始日志，只生成当前输入视图。`full_ours` 使用 `failure_card_v3`：未解决失败按 operation scope 聚合为受独立子预算约束的 compact card，原始失败和 audit-required 写操作默认留在 archive，不因“需要可恢复”而自动回注每一轮 prompt。只有当前目标/子目标、有效约束、不可逆动作前的显式确认和不可恢复的唯一关键证据属于 hard set；hard set 自身超过总预算时才显式设置 `budget_infeasible=true`。第二阶段的原始失败硬保留策略保留为 `raw_hard_failure_retention` 对照 manager。

τ³ live 投影将 Failure Card 作为独立 fragment 发送，不使用其 provenance 节点的历史 message ordinal，因此 card 不会触发 tool-call/result 协议闭包。运行元数据分别记录 graph-selected representation tokens 与 protocol-closed message tokens；provider actual input 继续由上游 usage 单独记录。

## 类型不变量

- `produces`: ToolCall/MCPCall → Observation
- `failed_with`: ToolCall/MCPCall → Error
- `uses`: Decision/ToolCall → Observation/Error/Summary/Handle
- `supports`: Observation/Summary/Constraint → Decision
- `blocks`: Error/Constraint → ToolCall/Decision
- `resolves`: Observation/Decision → Error
- `supersedes`: 新 Observation → 旧 Observation
- `compresses`: Summary → Observation/Error/Constraint
- `retries`: 新 ToolCall → 旧 ToolCall
- `leads_to`: Decision → ToolCall/MCPCall

非法类型连接在写入时立即失败，加载后的图还会执行完整结构校验。
