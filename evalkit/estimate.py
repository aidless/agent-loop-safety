"""
estimate.py — 按已测 variant 估算下一次 run 的 token。
读取 <flow_dir>/<measured>/results.jsonl，按任务取平均 token，乘以新的 reps 与任务数给出估算；
--actual 可把估算与实际跑出来的 token 并排对比（Anthropic kit 的做法：估算来自已测 run，越往后越准）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _per_task_tokens(flow_dir, variant) -> dict:
    p = Path(flow_dir) / variant / "results.jsonl"
    by_task: dict = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        t = r["task_id"]
        tok = r.get("input_tokens", 0) + r.get("output_tokens", 0)
        by_task.setdefault(t, []).append(tok)
    return {t: (sum(v) / len(v) if v else 0) for t, v in by_task.items()}


def estimate(flow_dir, measured, reps, tasks=None) -> dict:
    pt = _per_task_tokens(flow_dir, measured)
    ids = tasks if tasks is not None else list(pt.keys())
    total = sum(pt.get(t, 0) for t in ids) * reps
    return {"measured": measured, "reps": reps, "tasks": len(ids), "estimated_tokens": int(total)}


def with_actual(flow_dir, measured, actual, reps, tasks=None) -> dict:
    est = estimate(flow_dir, measured, reps, tasks)
    p = Path(flow_dir) / actual / "results.jsonl"
    used = 0
    n = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        used += r.get("input_tokens", 0) + r.get("output_tokens", 0)
        n += 1
    return {**est, "actual_tokens": used, "actual_runs": n, "diff": used - est["estimated_tokens"]}


def main(argv):
    if len(argv) < 2:
        print("usage: estimate.py <flow_dir> <measured> [--reps N] [--actual VARIANT] [--tasks a,b]")
        return 2
    flow, measured = argv[0], argv[1]
    reps, actual, tasks = 2, None, None
    i = 2
    while i < len(argv):
        if argv[i] == "--reps":
            reps = int(argv[i + 1]); i += 2
        elif argv[i] == "--actual":
            actual = argv[i + 1]; i += 2
        elif argv[i] == "--tasks":
            tasks = argv[i + 1].split(","); i += 2
        else:
            i += 1
    if actual:
        r = with_actual(flow, measured, actual, reps, tasks)
        print(f"estimated={r['estimated_tokens']}  actual={r['actual_tokens']}  diff={r['diff']}")
    else:
        r = estimate(flow, measured, reps, tasks)
        print(f"estimated_tokens={r['estimated_tokens']}  (measured={measured}, reps={reps}, tasks={r['tasks']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
