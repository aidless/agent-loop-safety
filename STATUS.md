# STATUS: WORK-IN-PROGRESS — not A yet

This repository is a reference / teaching skeleton for measuring your own single-agent loop.
It is not a production runtime. It does not validate Anthropic's claim that folding subagents
improves quality, cost, or latency. Nothing in the test run is that measurement.

## Gaps

- No independent eval corpus ships here. `tests/sample_data.py` is a four-row hand-written fixture. `examples/agent_redteam_integration.py` is a fake-model write-gate demo. Neither is a measured task set.
- Nothing in this repository is a before/after of folding a subagent on a real system.
- Offline CI runs `python -m pytest -q` with empty API-key variables. It does not install the Anthropic or OpenAI SDKs and does not run `examples/real_model_demo.py` against a model. `tests.yml` and `checks.yml` are the same command under two workflow names, not two kinds of coverage.
- The holes in [FAILURE_MODES.md](FAILURE_MODES.md) are current behavior. Tests lock them so a later edit cannot describe them as fixed without changing the test.
- No security review and no maintained production release process.

An A would need those gaps closed with evidence this repository can actually run. A stronger adjective does not close them.

## What was checked in the sources, and what was not

The companion kit's README (https://github.com/anthropics/single-agent-loop-kit, branch `release`, section "What has run on a real model", fetched 2026-11-09) says its recorded example is two runs by one person on one model, and that the merge and upkeep steps were not run on a real model. This repository did not re-run that example.

The article page itself was not re-fetched successfully while writing this status (the fetch timed out). This file does not repeat numeric claims from the article.
