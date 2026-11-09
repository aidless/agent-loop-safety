# agent-loop-safety

[中文](README.md)

Measure your own single-agent loop (write gate, token and turn budgets, optional verifier) on tasks you can score.
Folding subagents into that loop is a hypothesis for your eval; this repository does not show that it helps.
Offline tests need no API key. The live-model script is a separate optional demo, and it is not a benchmark.

**STATUS: WORK-IN-PROGRESS — not A yet.** See [STATUS.md](STATUS.md). This is a reference / teaching skeleton, not a production runtime, and not a validation of Anthropic's article. Failure modes: [FAILURE_MODES.md](FAILURE_MODES.md).

![tests](https://github.com/aidless/agent-loop-safety/actions/workflows/tests.yml/badge.svg)

The shape of the primitives is distilled from Anthropic's
[*Building agents: When to fold subagents into your main agent loop*](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop)
and the companion repo [single-agent-loop-kit](https://github.com/anthropics/single-agent-loop-kit) (Apache-2.0).
That kit calls itself "Reference implementation. Not maintained."
Its README (branch `release`, section "What has run on a real model", as fetched on 2026-11-09) says the recorded example is two runs by one person on one model, and that the merge and upkeep steps were not run on a real model.
This repository did not re-run that example. The article page was not fetched successfully while writing this note, so article statistics are not repeated here.

It is not a framework and it binds to no model SDK. Implement `ModelClient` to plug in a model. Attach your eval to `BaseEvalRunner` for "change one thing, compare one thing." The comparison tools do not prove that folding a subagent is an improvement.

## What's inside

```
agent-loop-safety/
├── agent_loop.py          # single-agent loop: budget / write gate / verifier / progressive disclosure
├── adapters.py            # optional examples: AnthropicModelClient / OpenAIModelClient
├── evalkit/               # compare, chain_coverage, coupling_map, estimate, runner
├── examples/
│   ├── real_model_demo.py          # optional live model. Exit 2 when no key is set
│   └── agent_redteam_integration.py # offline fake model + mock. Measures the write gate
├── tests/                 # offline tests. They do not call a model
├── FAILURE_MODES.md
├── STATUS.md
├── pyproject.toml         # runtime dependencies: none. pytest is the dev extra
├── LICENSE                # Apache-2.0
└── NOTICE
```

The table is what the code does, not a claim that tests cover every row. The token-budget holes are in FAILURE_MODES.md.

| Primitive | What the code does |
|---|---|
| **write gate** | A write tool and no `approve=` raises in the constructor. A runtime check also blocks a write that has no gate. A missing gate does not default to allow. |
| **token budget** | Counts usage the client reports, plus whatever the caller passes to `charge()`. See FAILURE_MODES.md. |
| **turn budget / batch cap** | `max_turns` (default 25) / `max_batch` (default 16). Exceeding returns an error result. |
| **verifier** | Optional. An empty return accepts the text. Two truthy complaints in a row withhold it. The loop does not check that the text changed. |
| **progressive disclosure** | The system prompt gets a one-line skill description. `deferred` tools go through `tool_search`. |

## Offline path (no API key)

A fresh clone installs this package and pytest. It does not install a model SDK and it does not download a model.

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

`pytest` is the CI command. `.github/workflows/tests.yml` (the badge) and `checks.yml` both run `python -m pytest -q` with `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` set empty. Two workflow names are not two suites. CI does not run the live-model demo.

`python agent_loop.py` uses `FakeModelClient`: the read tool runs, the write tool is declined, the demo verifier complains once, then returns `None` on the same text `已完成。` and the stop is `answered`. That is the verifier false green in FAILURE_MODES.md, not a passed check. Tests lock the stop, the turn count, the final text, and that the complaint is handed to the model once. They do not lock a token total, so this file states none.

`python examples/real_model_demo.py` with no key prints how to opt in and exits 2. It does not pretend a model ran.

`agent_redteam_integration.py` uses the built-in mock unless `agent_redteam` imports. `passed` means the attack function ran. baseline versus locked is the gate open versus shut. The `traces.jsonl` it writes is three copies of that fake route, not a production export. The script prints both limits. The output directory is gitignored. Do not commit it as a result.

Run the examples from the install above, or put the repo root on `PYTHONPATH` yourself. The example files do not insert an absolute path onto `sys.path`.

## Optional live-model demo

This is not a benchmark. `passed` means `stop == "answered"` and the text is non-empty. The example writes `cost: 0.0`. One prompt does not show whether folding subagents helps.

```bash
python -m pip install -e ".[anthropic]"    # or ".[openai]"
export ANTHROPIC_API_KEY=sk-...            # or OPENAI_API_KEY
export ALLOW_WRITES=1                      # optional. Writes are declined by default
python examples/real_model_demo.py --flow-dir flow/real_run
```

The write gate still runs in `Agent`. The adapter only translates messages. The token budget only sees the usage the adapter copies back.

## Plug in your own client

Implement `ModelClient.create(...)` and return a `ModelResponse`. Content blocks need `.type` / `.text` / `.id` / `.name` / `.input`.

```python
from agent_loop import Agent, Tool
from adapters import AnthropicModelClient

tools = [
    Tool("lookup", "Read-only lookup.", {...}, lookup_fn, writes=False),
    Tool("publish", "Writes externally.", {...}, publish_fn, writes=True),
]

agent = Agent(
    system="You are an assistant.",
    tools=tools,
    client=AnthropicModelClient(model="claude-opus-4-5"),
    approve=lambda tool, args: human_approves(tool, args),
    verify=lambda text, msgs: None,   # an empty return accepts the text. See FAILURE_MODES.md
)
result = agent.run("Look up the temperature and publish it.")
```

`AnthropicModelClient` / `OpenAIModelClient` are optional examples. Constructing one without its SDK raises `RuntimeError`. Offline tests cover that import-failure path only. They do not construct an SDK client when the package is installed, and they do not send a request.

## Plug in your own eval

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
r.compare("baseline", "v1")         # holds / flips / routes. holds can stay true when flips cancel
r.check_coverage("traces.jsonl", "baseline")
```

```bash
python -m evalkit.compare flow baseline v1
python -m evalkit.chain_coverage traces.jsonl flow baseline
python -m evalkit.coupling_map traces.jsonl
python -m evalkit.estimate flow baseline --reps 2 --actual v1
```

Result layout: `<flow>/<variant>/results.jsonl`, one run per line, fields
`task_id, rep, passed, latency_ms, model_calls, input_tokens, output_tokens, cost, route, writes, chain`.
That matches the kit's result-directory shape so you can feed in your own runs. This repository does not ship such a run.

## Statements

- Independently implemented. No affiliation with or endorsement from Anthropic. See `NOTICE`.
- The article states the single-loop conclusion as conditional, and it keeps cases where a subagent is still the right tool (measurable degradation from context bloat, parallel work in a context that can be discarded, a single agent picking the wrong tool or carrying conflicting instructions). This repository does not re-measure those cases.
- `evalkit` compares two runs you supply. It does not ship an independent task set, so it cannot settle whether folding helps.

## License

Apache-2.0. See [LICENSE](LICENSE).
