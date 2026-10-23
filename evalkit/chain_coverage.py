"""
chain_coverage.py — 每条生产链路都要有 eval 任务覆盖。
从生产 trace 抽出所有"agent 链路"，检查 baseline 的 eval 结果里是否每条都有对应任务。
若某条生产链路没有任何 eval 任务的 chain 是其超集，则报错——防止"被删的交接漏测"。
可作为 CLI 跑，也可被 runner.BaseEvalRunner.check_coverage() 调用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load_traces(path) -> list[list[str]]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        chain = obj.get("chain") or obj.get("agents") or []
        rows.append(list(chain))
    return rows


def load_eval_chains(flow_dir, baseline) -> set[tuple]:
    p = Path(flow_dir) / baseline / "results.jsonl"
    chains = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        c = obj.get("chain") or []
        if c:
            chains.add(tuple(c))
    return chains


def check(flow_dir, baseline, traces):
    prod = load_traces(traces)
    eval_chains = load_eval_chains(flow_dir, baseline)
    missing = []
    for ch in prod:
        tch = tuple(ch)
        covered = any(set(tch) <= set(ec) for ec in eval_chains)
        if not covered:
            missing.append(list(ch))
    ok = not missing
    lines = [f"chain_coverage: {'OK' if ok else 'FAIL'} "
             f"({len(prod)} prod chains, {len(eval_chains)} eval chains)"]
    if missing:
        lines.append("未覆盖的生产链路:")
        for m in missing:
            lines.append("  " + " -> ".join(m))
    text = "\n".join(lines)
    if __name__ == "__main__":
        print(text)
    return {"ok": ok, "missing": missing, "text": text}


def main(argv):
    if len(argv) < 3:
        print("usage: chain_coverage.py <traces.jsonl> <flow_dir> <baseline>")
        return 2
    check(argv[1], argv[2], argv[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
