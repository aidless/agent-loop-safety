# FAILURE_MODES

Current behavior of this skeleton, locked by `tests/test_failure_modes.py`.
These are not measured incident rates, not a security audit, and not a list of fixes.
If a test and this file disagree, neither is authoritative until both match the code.

The write gate is not one of these holes. A write tool with `approve=None` raises at construction.
A declined write does not run, including when the client reports zero tokens.
Adapters do not implement the gate and do not remove it. See `TestWriteGateStaysLocal`.

## Verifier false greens

`Agent.verify` is optional. Leaving it unset means any non-tool final turn stops as `answered` with the model's text.

A complaint is a truthy return value. `None` or `""` accepts the text. The loop does not compare the new text with the rejected draft. The offline demo verifier complains once, then returns `None` on the same string `已完成。`. `tests/test_loop.py` asserts that this complaint is handed to the model once and that the final text is still `已完成。`. `stop=answered` there means the second call returned `None`, not that a check of the answer passed.

A second truthy complaint withholds the text (`verify_failed`). That path is the strict one. The false green is a verifier that stops complaining.

On `refusal`, `max_tokens`, and `model_context_window_exceeded`, a truthy verify result clears `text`. A verifier that returns `None` leaves the partial text in place. The stop reason stays the provider reason, not `answered`. Callers that treat non-empty `text` as success will false-green that result.

`examples/real_model_demo.py` sets `passed` only when `stop == "answered"` and `text` is non-empty. That is still only a non-empty answer, not a correct one. With no API key the script exits 2 and does not write a result.

## Budget gates bypassed by adapters

`Budget.max_tokens` counts what `ModelClient.create` puts in `usage`, plus whatever a caller passes to `Agent.charge`. Adapters never call `charge`. A tool that calls another model and does not `charge` that usage is invisible to the budget.

`adapters.reported_usage` uses `getattr`. A `usage` of `None`, a missing attribute, or a dict counts as zero, because attribute lookup does not read mapping keys. CI does not import the official SDKs, so this file does not claim a live SDK returns a dict. It claims this function counts a dict as zero when one is passed in. Under-reported usage never trips `max_tokens`.

The token budget is not checked when the model ends the turn. `_run` can `charge` an oversized final answer and still return `answered`.

`pause_turn` (the branch comments associate with provider-side tools such as a server `tool_search`) continues after `charge` and does not consult the token budget. Spend is accumulated, and the run ends as `turn_budget` when `max_turns` is hit. Provider-side tool execution is also outside `approve` and outside `max_batch`: those apply only to tool calls the client returned for `_run_batch`.

The `max_tokens=16000` argument passed into `create` is a per-call cap, not `Budget.max_tokens`. An adapter may ignore it. The adapters in this repo forward the argument; CI does not execute those SDK calls.

## Toy eval overclaim

`tests/sample_data.py` writes four hand-authored rows per variant. The `after` variant flips `t02` and repairs `t04` inside the fixture. `tests/test_eval.py` checks that `compare` notices that planted flip. That is the comparator on authored rows, not a fold experiment and not a model run.

`compare` sets `holds` when the after pass rate is at least the before pass rate and no unexpected route change is listed. Canceling flips (one task gains a pass, another loses one) can leave `holds` true.

`per_task` marks a task passed if any rep passed. `summarize` uses the row-level pass rate. One failing rep and one passing rep is `after_pass=True` and a pass rate of 0.5.

`chain_coverage` treats a production chain as covered when its set of names is a subset of some eval chain's set. Order and repeated names do not matter. A reversed chain can look covered.

`examples/agent_redteam_integration.py` uses `FakeModelClient` unless importing `agent_redteam` succeeds. Its `passed` flag means the attack function ran, the stop was `answered`, and the text was non-empty. baseline versus locked is the write gate. There is no folded-subagent variant. The traces file it writes is three copies of that fake route, not a production export, so a coverage OK only means the checker recognized the line the script just wrote. The script prints both limits.

No independent eval corpus is shipped. Do not read these fixtures as evidence that folding subagents helps.
