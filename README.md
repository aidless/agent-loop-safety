# agent-loop-safety

单 agent 循环 harness 的**安全原语骨架** + 一套**评测对比工具**，提炼自 Anthropic 的
[《Building agents: When to fold subagents into your main agent loop》](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop)
与其配套仓库 [single-agent-loop-kit](https://github.com/anthropics/single-agent-loop-kit)。

不是框架，不绑定任何模型 SDK。把你的模型客户端实现 `ModelClient` 协议即可接入；把你的
eval 体系接到 `BaseEvalRunner` 即可获得「改一处、比一处」的回归对比。

> ⚠️ 这是**参考骨架 / 教学实现**，不是生产级 runtime。Anthropic 原仓库也自述
> "Reference implementation. Not maintained." 本仓库同理：安全原语的*形态*可用，
> 但「折叠子智能体能提速/提质」这一主张的实证极薄（原文 1 个客户、工具包 1 个玩具示例、
> 单人单模型两次，且 merge/upkeep 路径从未在真实模型上跑过）。本仓库帮你**严谨地度量你自己的系统**，
> 而不是替你下结论。

## 包含什么

```
agent-loop-safety/
├── agent_loop.py          # 单 agent 循环：预算/写闸门/verifier/渐进披露
├── adapters.py            # 真实客户端示例：AnthropicModelClient / OpenAIModelClient
├── evalkit/
│   ├── runner.py          # BaseEvalRunner：把你自己的 eval 挂进来的接入点
│   ├── compare.py         # 两套 eval 并排比：通过率/延迟/调用/token/成本 + 路由变化
│   ├── chain_coverage.py  # 每条生产链路都要有 eval 任务覆盖（防漏测）
│   ├── coupling_map.py    # 从 trace 看谁扛流量、谁总一起跑（决定能不能合并）
│   └── estimate.py        # 按已测 variant 估算下一次 run 的 token
├── tests/                 # 离线单测（无需任何 API key）
├── pyproject.toml
├── LICENSE                # Apache-2.0（与原作一致）
└── NOTICE                 # 溯源声明
```

## 安装

```bash
git clone https://github.com/aidless/agent-loop-safety.git
cd agent-loop-safety
python -m venv .venv && source .venv/bin/activate
pip install -e .                      # 零依赖即可跑骨架与 eval 工具
pip install -e ".[anthropic]"         # 若要用 AnthropicModelClient
pip install -e ".[openai]"            # 若要用 OpenAIModelClient
```

## 快速开始（离线自检）

```bash
python agent_loop.py
# stop=answered / turns=4 / tokens=290/69 / final text='已完成。'
```

它用内置 `FakeModelClient` 验证：read 工具跑通 → write 工具被闸门拒绝 → 作答被 verifier
拦一次 → 修正后定稿。说明循环、并行批次、写闸门、verifier 都已生效。

跑全部单测（无 key、无网络）：

```bash
python -m unittest discover -s tests -t .
```

## 接入你自己的模型客户端

实现 `ModelClient.create(...)`，返回 `ModelResponse`（内容块用带
`.type/.text/.id/.name/.input` 属性的对象，与 Anthropic SDK 块对齐）。

```python
from agent_loop import Agent, Tool
from adapters import AnthropicModelClient

tools = [
    Tool("lookup", "Read-only lookup.", {...}, lookup_fn, writes=False),
    Tool("publish", "Writes externally.", {...}, publish_fn, writes=True),
]

agent = Agent(
    system="你是助手。",
    tools=tools,
    client=AnthropicModelClient(model="claude-opus-4-5"),
    approve=lambda tool, args: human_approves(tool, args),   # 写操作的"人在环"闸门
    verify=lambda text, msgs: None,                          # 可选：最终答复校验
)
result = agent.run("查一下温度并发布。")
```

`adapters.py` 里的 `AnthropicModelClient` / `OpenAIModelClient` 是**完整示例**，含消息格式
双向翻译；按需替换成你自己的后端即可。

## 接入你自己的 eval（「改一处、比一处」）

```python
from evalkit.runner import BaseEvalRunner, EvalResult

class MyRunner(BaseEvalRunner):
    def run_one(self, task) -> EvalResult:
        # 在这里调你的 agent / 系统，返回标准结果
        return EvalResult(
            task_id=task["task_id"], passed=..., route=[...], chain=task["chain"], ...
        )

r = MyRunner("flow/", tasks)
r.run_variant("baseline", reps=2)   # 产出 flow/baseline/results.jsonl
# ... 折叠一个子 agent 后 ...
r.run_variant("v1", reps=2)
r.compare("baseline", "v1")         # 并排对比，返回 holds / flips / routes
r.check_coverage("traces.jsonl", "baseline")   # 生产链路是否都有任务覆盖
```

CLI 直跑：

```bash
python -m evalkit.compare flow baseline v1
python -m evalkit.chain_coverage traces.jsonl flow baseline
python -m evalkit.coupling_map traces.jsonl
python -m evalkit.estimate flow baseline --reps 2 --actual v1
```

标准结果布局 `<flow>/<variant>/results.jsonl`，每行一个任务运行，字段：
`task_id, rep, passed, latency_ms, model_calls, input_tokens, output_tokens, cost,
route, writes, chain`（与 Anthropic kit 的 `/claude/hillclimb/<name>/<variant>/` 对齐）。

## 安全原语一览

| 原语 | 行为 |
|---|---|
| **write gate** | 有写工具却没传 `approve=` → 构造期直接抛错；运行时兜底也拦「无闸门的写」。**缺失闸门绝不默认放行。** |
| **token 预算** | `max_tokens` 累计 input+output，且 `charge()` 让子 agent 把花费计入同一预算 |
| **turn 预算 / 批次上限** | `max_turns`（默认 25）/ `max_batch`（默认 16），超限返回错误结果 |
| **verifier** | 答案被挑刺一次给修正机会，两次仍不过则扣留不返回 |
| **渐进披露** | 系统提示只放 skill 一行描述（`load_skill` 按需加载）；`deferred` 工具走 `tool_search` |

## 重要声明

- 本仓库**独立实现**，与 Anthropic 无隶属或背书关系（见 `NOTICE`）。
- 「单循环优于多子 agent」是**条件性结论**，原文明确保留三类该用子 agent 的场景
  （上下文膨胀致可测性能下降 / 需并行且用完即弃 / 单 agent 选不准工具或提示冲突）。
- 证据强度有限：不要把它当成普遍定律，而要把它当成**你该去度量的假设**。
  `evalkit` 这套工具就是为这个度量而生的。

## 许可证

Apache-2.0。见 [LICENSE](LICENSE)。
