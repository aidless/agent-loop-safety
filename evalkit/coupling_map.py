"""
coupling_map.py — 从 trace 看哪些子 agent 扛流量、哪些总一起跑。
输入每条 trace 是一个 {"chain":[...agents...]} 或 {"agents":[...]} 的 JSON 行。
输出：每个 agent 被多少 run 触及；以及"总是一起出现"的 agent 对（共现次数 == 两者各自触及次数）。
这决定哪些子 agent 可以合并（总一起跑的优先合并）。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def coupling(traces_path) -> dict:
    per_agent: Counter = Counter()
    co: Counter = Counter()
    n = 0
    for line in Path(traces_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        agents = list(obj.get("chain") or obj.get("agents") or [])
        if not agents:
            continue
        n += 1
        for a in agents:
            per_agent[a] += 1
        for i in range(len(agents)):
            for j in range(i + 1, len(agents)):
                pair = tuple(sorted([agents[i], agents[j]]))
                co[pair] += 1
    always = {
        " & ".join(p): c
        for p, c in co.items()
        if c == min(per_agent[p[0]], per_agent[p[1]]) and per_agent[p[0]] > 1 and per_agent[p[1]] > 1
    }
    return {"runs": n, "per_agent": dict(per_agent), "always_together": always}


def main(argv):
    if len(argv) < 1:
        print("usage: coupling_map.py <traces.jsonl>")
        return 2
    r = coupling(argv[0])
    print(f"runs={r['runs']}")
    print("per-agent traffic:")
    for a, c in sorted(r["per_agent"].items(), key=lambda x: -x[1]):
        print(f"  {a}: {c}")
    print("always-run-together pairs:")
    for p, c in r["always_together"].items():
        print(f"  {p}: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
