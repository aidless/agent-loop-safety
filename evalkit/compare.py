"""
compare.py — 两套 eval 结果并排对比。
读取 <flow_dir>/<variant>/results.jsonl（每行一个任务运行结果），输出：
  - 汇总对比：通过率 / 延迟 / 模型调用数 / token / 成本 的 before→after→delta
  - 翻转任务（passed 前后变化）
  - 路由变化任务（route 前后变化，非预期变化即视为改动不成立）
既可作为 CLI 跑，也可被 runner.BaseEvalRunner.compare() 调用返回结构化结果。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FIELDS = ["pass_rate", "latency_ms", "model_calls", "input_tokens", "output_tokens", "cost"]


def load_variant(flow_dir, variant) -> list[dict]:
    p = Path(flow_dir) / variant / "results.jsonl"
    if not p.exists():
        raise FileNotFoundError(p)
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    return {
        "n": n,
        "pass_rate": (sum(1 for r in rows if r.get("passed")) / n) if n else 0.0,
        "latency_ms": _mean([r.get("latency_ms", 0) for r in rows]),
        "model_calls": sum(r.get("model_calls", 0) for r in rows),
        "input_tokens": sum(r.get("input_tokens", 0) for r in rows),
        "output_tokens": sum(r.get("output_tokens", 0) for r in rows),
        "cost": sum(r.get("cost", 0.0) for r in rows),
    }


def _routes(rows):
    return sorted({tuple(r.get("route", [])) for r in rows})


def per_task(before, after):
    def idx(rows):
        d = {}
        for r in rows:
            d.setdefault(r["task_id"], []).append(r)
        return d

    b, a = idx(before), idx(after)
    out = []
    for tid in sorted(set(b) | set(a)):
        bp = any(r.get("passed") for r in b.get(tid, []))
        ap = any(r.get("passed") for r in a.get(tid, []))
        br, ar = _routes(b.get(tid, [])), _routes(a.get(tid, []))
        out.append({
            "task_id": tid,
            "before_pass": bp,
            "after_pass": ap,
            "flipped": bp != ap,
            "route_changed": br != ar,
            "before_route": [list(x) for x in br],
            "after_route": [list(x) for x in ar],
        })
    return out


def compare(flow_dir, before, after, exclude=()):
    b = load_variant(flow_dir, before)
    a = load_variant(flow_dir, after)
    sb, sa = summarize(b), summarize(a)
    tasks = per_task(b, a)
    flips = [t for t in tasks if t["flipped"]]
    routes = [t for t in tasks if t["route_changed"] and t["task_id"] not in exclude]
    holds = (sa["pass_rate"] >= sb["pass_rate"]) and not routes

    lines = [f"compare {before} -> {after}  (holds={holds})",
             f"{'metric':14}{'before':>12}{'after':>12}{'delta':>12}"]
    for k in FIELDS:
        lines.append(f"{k:14}{sb[k]:>12.4f}{sa[k]:>12.4f}{sa[k]-sb[k]:>12.4f}")
    if flips:
        lines.append(f"\n任务结果翻转 ({len(flips)}):")
        for t in flips:
            lines.append(f"  {t['task_id']}: {t['before_pass']} -> {t['after_pass']}")
    if routes:
        lines.append(f"\n非预期路由变化 ({len(routes)}):")
        for t in routes:
            lines.append(f"  {t['task_id']}: {t['before_route']} -> {t['after_route']}")
    text = "\n".join(lines)
    if __name__ == "__main__":
        print(text)
    return {"holds": holds, "summary_before": sb, "summary_after": sa,
            "flips": flips, "routes": routes, "text": text}


def main(argv):
    if len(argv) < 3:
        print("usage: compare.py <flow_dir> <before> <after>")
        return 2
    compare(argv[0], argv[1], argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
