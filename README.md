# agent-loop-safety

[English](README_EN.md) | 中文

在你能打分的任务上，度量你自己的单 agent 循环（写闸门、token 与轮次预算、可选 verifier）。
把子 agent 折进这个循环，只是留给你自己的 eval 的假设；本仓库没有结果说明这样做有帮助。
离线测试不需要 API key。接真实模型的脚本是分开的可选演示，不是基准。

**STATUS: WORK-IN-PROGRESS — not A yet.** 见 [STATUS.md](STATUS.md)。这是参考 / 教学骨架，不是生产 runtime，也不是对 Anthropic 文章的验证。失败模式见 [FAILURE_MODES.md](FAILURE_MODES.md)（英文，避免两份细节漂移）。

![tests](https://github.com/aidless/agent-loop-safety/actions/workflows/tests.yml/badge.svg)

安全原语的形态提炼自 Anthropic 的
[《Building agents: When to fold subagents into your main agent loop》](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop)
与配套仓库 [single-agent-loop-kit](https://github.com/anthropics/single-agent-loop-kit)（Apache-2.0）。
该 kit 自述 "Reference implementation. Not maintained."
其 README（分支 `release`，节 "What has run on a real model"，2026-11-09 读到的版本）写明：记录在案的示例是一个人、一个模型、两次运行；merge 与 upkeep 没有在真实模型上跑过。
本仓库没有重跑那个示例。文章页面在写这份说明时没有成功抓取，所以这里不转述文章里的数字。

不是框架，不绑定任何模型 SDK。实现 `ModelClient` 即可接入；把你的 eval 接到 `BaseEvalRunner` 做「改一处、比一处」。比对工具不会替你证明折叠子 agent 更好。

## 仓库里有什么

```
agent-loop-safety/
├── agent_loop.py          # 单 agent 循环：预算 / 写闸门 / verifier / 渐进披露
├── adapters.py            # 可选示例：AnthropicModelClient / OpenAIModelClient
├── evalkit/               # compare、chain_coverage、coupling_map、estimate、runner
├── examples/
│   ├── real_model_demo.py          # 可选：真实模型。无 key 则退出码 2
│   └── agent_redteam_integration.py # 离线：假模型 + mock。度量的是写闸门
├── tests/                 # 离线测试。不调用模型
├── FAILURE_MODES.md
├── STATUS.md
├── pyproject.toml         # 运行时零依赖。pytest 在 optional extra dev
├── LICENSE                # Apache-2.0
└── NOTICE
```

下表是代码在做什么，不是「测试已经覆盖每一行」。token 预算的缺口在 FAILURE_MODES.md。

| 原语 | 代码行为 |
|---|---|
| **write gate** | 有写工具却没传 `approve=` → 构造期抛错。运行时也会拦住没有闸门的写。缺失闸门不会默认放行。 |
| **token 预算** | 只累计客户端上报的 usage，加上调用方主动 `charge()` 的部分。见 FAILURE_MODES.md。 |
| **turn 预算 / 批次上限** | `max_turns`（默认 25）/ `max_batch`（默认 16）。超限返回错误结果。 |
| **verifier** | 可选。返回空就接受。连续两次非空才扣留。不检查正文是否改过。 |
| **渐进披露** | 系统提示只放 skill 的一行描述；`deferred` 工具走 `tool_search`。 |

## 离线路径（不需要 API key）

从新克隆到跑完，只装这个包和 pytest，不装模型 SDK，也不下载模型。

```bash
git clone https://github.com/aidless/agent-loop-safety.git
cd agent-loop-safety
python -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest -q
python agent_loop.py
python examples/real_model_demo.py
python examples/agent_redteam_integration.py --flow-dir flow_redteam
```

`pytest` 是 CI 使用的命令。`.github/workflows/tests.yml`（徽章指向的文件）和 `checks.yml` 都执行 `python -m pytest -q`，并把 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 设为空。两个 workflow 名字不是两套测试。CI 不跑真实模型演示。

`python agent_loop.py` 用内置 `FakeModelClient`：读工具会执行，写工具被闸门拒绝，演示用 verifier 抱怨一次，然后对同一句「已完成。」返回空并 `answered`。这就是 FAILURE_MODES.md 里的 verifier false green，不是「校验通过了」。测试锁的是 `stop`、轮数、最终文本，以及这句抱怨只送进对话一次。不锁 token 合计，所以这里不写 token 数字。

`python examples/real_model_demo.py` 在没有 key 时打印接入说明并以退出码 2 结束。它不会假装跑过模型。

`agent_redteam_integration.py` 在没装 `agent_redteam` 时用内置 mock。`passed` 表示攻击函数执行了。baseline 对 locked 是闸门开和关。它写的 `traces.jsonl` 是这条假 route 的三份拷贝，不是生产导出。脚本会打印这两条限制。输出目录已被 gitignore，不要把它提交成结果。

两个示例都要从已安装的环境运行（上面的 `pip install -e ".[dev]"`），或者自行把仓库根目录放进 `PYTHONPATH`。示例文件不再把绝对路径插进 `sys.path`。

## 可选：真实模型演示

这不是基准。`passed` 只表示 `stop == "answered"` 且正文非空，`cost` 在示例里写成 0.0。一次提示不能说明折叠子 agent 是否有帮助。

```bash
python -m pip install -e ".[anthropic]"    # 或 ".[openai]"
export ANTHROPIC_API_KEY=sk-...            # 或 OPENAI_API_KEY
export ALLOW_WRITES=1                      # 可选。默认拒绝写工具
python examples/real_model_demo.py --flow-dir flow/real_run
```

写闸门仍由 `Agent` 执行。适配器只翻译消息。token 预算只看见适配器抄回来的 usage。

## 接入你自己的客户端

实现 `ModelClient.create(...)`，返回 `ModelResponse`。内容块需要 `.type` / `.text` / `.id` / `.name` / `.input`。

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
    approve=lambda tool, args: human_approves(tool, args),
    verify=lambda text, msgs: None,   # 返回空即接受。见 FAILURE_MODES.md
)
result = agent.run("查一下温度并发布。")
```

`AnthropicModelClient` / `OpenAIModelClient` 是可选示例。没装对应 SDK 时，构造它们会抛 `RuntimeError`。离线测试只检查这个导入失败路径，不构造已安装的 SDK 客户端，也不发网络请求。

## 接入你自己的 eval

```python
from evalkit.runner import BaseEvalRunner, EvalResult

class MyRunner(BaseEvalRunner):
    def run_one(self, task) -> EvalResult:
        return EvalResult(
            task_id=task["task_id"], passed=..., route=[...], chain=task["chain"], ...
        )

r = MyRunner("flow/", tasks)
r.run_variant("baseline", reps=2)   # flow/baseline/results.jsonl
r.run_variant("v1", reps=2)
r.compare("baseline", "v1")         # holds / flips / routes。holds 可以在翻转互相抵消时仍为真
r.check_coverage("traces.jsonl", "baseline")
```

```bash
python -m evalkit.compare flow baseline v1
python -m evalkit.chain_coverage traces.jsonl flow baseline
python -m evalkit.coupling_map traces.jsonl
python -m evalkit.estimate flow baseline --reps 2 --actual v1
```

结果布局是 `<flow>/<variant>/results.jsonl`，每行字段：
`task_id, rep, passed, latency_ms, model_calls, input_tokens, output_tokens, cost, route, writes, chain`。
这与 kit 的结果目录形状对齐，好让你把自己的运行喂进去。本仓库不附带那样的运行。

## 声明

- 独立实现，与 Anthropic 无隶属或背书。见 `NOTICE`。
- 文章把「单循环优于多子 agent」写成有条件的结论，并保留子 agent 仍然合适的情形（上下文膨胀导致可测的性能下降、需要用完即弃的并行上下文、单 agent 选不准工具或提示互相冲突）。本仓库不重新测量这些情形。
- `evalkit` 用来比较你自己的两次运行。它不包含一个独立的任务集，因此不能在这里得出折叠是否有帮助的结论。

## 许可证

Apache-2.0。见 [LICENSE](LICENSE)。
