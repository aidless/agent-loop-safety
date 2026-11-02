# agent-loop-safety：单 agent 循环的安全原语骨架与「改一处、比一处」评测工具

> 本文档是 agent-loop-safety 的**论文摘要草稿**，内容全部来自仓库真实实现与实测
> 结果，无虚构数字。

## 摘要（Abstract）

Anthropic《Building agents: When to fold subagents into your main agent loop》
主张单 agent 循环在多数情况下优于多子 agent 分解。但原文证据极薄。本文构建一个
**参考骨架**（非生产级 runtime，与 Anthropic 无隶属/背书关系）把该循环的安全原语
独立实现，并配一套**评测对比工具**，帮你严谨地度量你自己的系统，而不是替你说结论。

**安全原语（fail-closed）**：
- **write gate**：有写工具却没传 `approve=` → 构造期直接抛错；运行时兜底也拦
  「无闸门的写」。**缺失闸门绝不默认放行。**
- **token 预算**：`max_tokens` 累计 input+output，`charge()` 让子 agent 把花费计入
  同一预算。
- **turn 预算 / 批次上限**：`max_turns`（默认 25）/ `max_batch`（默认 16）。
- **verifier**：答案被挑刺一次给修正机会，两次仍不过则扣留不返回。
- **渐进披露**：系统提示只放 skill 一行描述；`deferred` 工具走 `tool_search`。

**评测工具（可复算）**：`evalkit` 提供 `BaseEvalRunner` 接入点 + `compare`（并排比
通过率/延迟/调用/token/成本 + 路由变化）+ `chain_coverage`（每条生产链路都要有
eval 任务覆盖，防漏测）+ `coupling_map`（从 trace 看谁扛流量、谁总一起跑）+
`estimate`（按已测 variant 估算下一次 run 的 token）。

**关键实证（离线可复算）**：把 red-team 攻击能力包成 Tool（`run_attack` 是写/外部
副作用工具，**必须过写闸门**），跑 baseline（攻击放行）vs locked（攻击拦截）两个
variant。实测（Mock harness，`flow_redteam/` 落盘）：
- `pass_rate` 1.0000 → 0.0000（**3 个任务全部翻转**：t01/t02/t03 True → False）；
- `latency_ms` 0.6333 → 0.5000；`model_calls` 9.0000 → 9.0000（不变）；
- `input_tokens` 690 → 690、`output_tokens` 171 → 171（不变）；
- `chain_coverage: OK (3 prod chains, 1 eval chains)`。

**明确边界**：本仓库**不证明**「单循环优于多子 agent」是普遍定律（原文明确保留三类
该用子 agent 的场景）；**不是**生产级 runtime（Anthropic 原仓库也自述
"Reference implementation. Not maintained."）；merge/upkeep 路径从未在真实模型上
跑过；`cost` 恒为 0.0 是离线 Mock 示例（无真实计费）；`holds=False`（baseline vs
locked 通过率不一致）是**预期**——它证明闸门改变结果，不是要说明哪个 variant 更好。

## 关键词

Agent 循环；子 agent；安全原语；write gate；fail-closed；eval 对比；
red-team；chain coverage；耦合分析

## 与真实代码的对应

| 摘要声明 | 对应真实实现 |
|---|---|
| 单 agent 循环 | `agent_loop.py`（预算/写闸门/verifier/渐进披露） |
| 客户端示例 | `adapters.py`（AnthropicModelClient / OpenAIModelClient） |
| 评测接入点 | `evalkit/runner.py`（BaseEvalRunner） |
| 并排对比 | `evalkit/compare.py`（holds/flips/routes） |
| 链路覆盖 | `evalkit/chain_coverage.py` |
| 耦合分析 | `evalkit/coupling_map.py` |
| token 估算 | `evalkit/estimate.py` |
| 端到端示例 | `examples/real_model_demo.py` + `agent_redteam_integration.py` |
| 6 tests | `unittest discover -s tests -t .` 实测 6 tests OK |
