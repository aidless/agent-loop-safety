# agent-loop-safety

A **safety-primitive skeleton** for a single-agent-loop harness, plus an **eval-comparison toolkit**, distilled from Anthropic's
[*Building agents: When to fold subagents into your main agent loop*](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop)
and its companion repo [single-agent-loop-kit](https://github.com/anthropics/single-agent-loop-kit).

It is **not a framework** and binds to no model SDK. Implement the `ModelClient` protocol to plug in your own model; attach your eval suite to `BaseEvalRunner` to get "change one thing, compare one thing" regression checks.

> ⚠️ This is a **reference skeleton / teaching implementation**, not a production runtime. Anthropic's original repo describes itself as
> "Reference implementation. Not maintained." This repo is the same: the *shape* of the safety primitives is usable, but the
> empirical evidence for "folding subagents speeds up / improves quality" is thin (1 customer in the article, 1 toy example in the kit,
> one person / one model / two runs, and the merge/upkeep paths were never run on a real model). This repo helps you **rigorously
> measure *your own* system** rather than drawing conclusions for you.

## What's inside

```
agent-loop-safety/
├── agent_loop.py          # single-agent loop: budget / write gate / verifier / progressive disclosure
├── adapters.py            # real-client examples: AnthropicModelClient / OpenAIModelClient
├── evalkit/
│   ├── runner.py          # BaseEvalRunner: the entry point for plugging in your own eval
│   ├── compare.py         # side-by-side eval: pass rate / latency / calls / tokens / cost + route changes
│   ├── chain_coverage.py  # every production chain must have an eval task (prevents dropped coverage)
│   ├── coupling_map.py    # from traces, who carries load / who always runs together (decides what to merge)
│   └── estimate.py        # estimate the next run's tokens from a measured variant
├── examples/
│   ├── real_model_demo.py          # end-to-end demo wired to a real Anthropic / OpenAI backend
│   └── agent_redteam_integration.py # advanced: wrap a red-team harness as gated Tools + full evalkit walkthrough
├── tests/                 # offline unit tests (no API key required)
├── pyproject.toml
├── LICENSE                # Apache-2.0 (same as the original)
└── NOTICE                 # provenance statement
```

## Install

```bash
git clone https://github.com/aidless/agent-loop-safety.git
cd agent-loop-safety
python -m venv .venv && source .venv/bin/activate
pip install -e .                      # zero deps: runs the skeleton and eval toolkit
pip install -e ".[anthropic]"         # if you want AnthropicModelClient
pip install -e ".[openai]"            # if you want OpenAIModelClient
```

## Quick start (offline self-check)

```bash
python agent_loop.py
# stop=answered / turns=4 / tokens=290/69 / final text='已完成。'
```

It uses the built-in `FakeModelClient` to verify: a read tool runs → a write tool is blocked by the gate → the answer is
challenged once by the verifier → after a fix it finalizes. This proves the loop, parallel batching, write gate, and verifier all work.

Run all unit tests (no key, no network):

```bash
python -m unittest discover -s tests -t .
```

## Real-model demo

```bash
export ANTHROPIC_API_KEY=sk-...        # use the Anthropic backend
# or: export OPENAI_API_KEY=sk-...     # use the OpenAI backend
pip install -e ".[anthropic]"          # or ".[openai]"
export ALLOW_WRITES=1                  # optional: allow write tools (otherwise declined by default)
python examples/real_model_demo.py
```

With no key set, the script prints setup instructions and exits with code 2 instead of erroring. Safety primitives (write gate /
budget / verifier) stay under `Agent` control regardless of backend — the adapter only translates message formats. Add
`--flow-dir <dir>` to write the run into the `evalkit` layout for direct `compare`.

## Advanced example: red-team harness + eval-first discipline

`examples/agent_redteam_integration.py` shows how to wrap an external red-team platform as Tools — if the real `agent_redteam`
package is installed it is lazy-imported as the live attack backend; otherwise a built-in mock is used. The `run_attack` tool has
external side effects and **must pass the write gate**. The example then runs two variants (baseline = attacks allowed, locked =
attacks blocked) through `evalkit`, making the gate's effect directly measurable:

```bash
python examples/agent_redteam_integration.py --flow-dir flow_redteam
```

Runs fully offline (FakeModelClient + mock harness); prints `compare` flips and `chain_coverage` verdicts, and writes the
standard flow layout so you can re-run `compare` on it.

## Plug in your own model client

Implement `ModelClient.create(...)`, returning a `ModelResponse` (content blocks are objects with
`.type/.text/.id/.name/.input` attributes, aligned with Anthropic SDK blocks).

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
    approve=lambda tool, args: human_approves(tool, args),   # human-in-the-loop gate for writes
    verify=lambda text, msgs: None,                          # optional: final-answer check
)
result = agent.run("Look up the temperature and publish it.")
```

`AnthropicModelClient` / `OpenAIModelClient` in `adapters.py` are **complete examples** with bidirectional message-format
translation; swap in your own backend as needed.

## Plug in your own eval ("change one thing, compare one thing")

```python
from evalkit.runner import BaseEvalRunner, EvalResult

class MyRunner(BaseEvalRunner):
    def run_one(self, task) -> EvalResult:
        # call your agent / system here, return a standard result
        return EvalResult(
            task_id=task["task_id"], passed=..., route=[...], chain=task["chain"], ...
        )

r = MyRunner("flow/", tasks)
r.run_variant("baseline", reps=2)   # writes flow/baseline/results.jsonl
# ... fold one subagent ...
r.run_variant("v1", reps=2)
r.compare("baseline", "v1")         # side-by-side; returns holds / flips / routes
r.check_coverage("traces.jsonl", "baseline")   # are all production chains covered by tasks?
```

Run the CLIs directly:

```bash
python -m evalkit.compare flow baseline v1
python -m evalkit.chain_coverage traces.jsonl flow baseline
python -m evalkit.coupling_map traces.jsonl
python -m evalkit.estimate flow baseline --reps 2 --actual v1
```

Standard result layout `<flow>/<variant>/results.jsonl`, one task run per line, fields:
`task_id, rep, passed, latency_ms, model_calls, input_tokens, output_tokens, cost,
route, writes, chain` (aligned with Anthropic kit's `/claude/hillclimb/<name>/<variant>/`).

## Safety primitives at a glance

| Primitive | Behavior |
|---|---|
| **write gate** | A write tool with no `approve=` → constructor raises immediately; a runtime fallback also blocks "writes without a gate." **A missing gate never defaults to allow.** |
| **token budget** | `max_tokens` accumulates input+output, and `charge()` lets subagents count their spend into the same budget |
| **turn budget / batch cap** | `max_turns` (default 25) / `max_batch` (default 16); exceeding returns an error result |
| **verifier** | An answer is challenged once with a chance to fix; if it fails twice it is withheld and not returned |
| **progressive disclosure** | The system prompt holds only a one-line skill description (`load_skill` loads on demand); `deferred` tools go through `tool_search` |

## Important statements

- This repo is **independently implemented** and has no affiliation with or endorsement from Anthropic (see `NOTICE`).
- "A single loop beats multiple subagents" is a **conditional** conclusion; the article explicitly keeps three scenarios where subagents are right
  (context bloat causing measurable performance drops / need for parallel, fire-and-forget contexts / single agent picks the wrong tool or has conflicting instructions).
- The evidence is limited: treat it as a **hypothesis you should measure**, not a universal law.
  The `evalkit` toolkit exists precisely to enable that measurement.

## License

Apache-2.0. See [LICENSE](LICENSE).
