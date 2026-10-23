"""
runner.py — 把你的 eval 体系挂到这套工具上的接入点。
你只需要：
  1) 继承 BaseEvalRunner，实现 run_one(task) -> EvalResult（在里面调你的 agent / 系统）；
  2) 把任务定义（含每条任务覆盖的 chain）传给构造器；
  3) 调 run_variant("baseline", reps=2) 产出标准 results.jsonl；
  4) 每次改动后 run_variant("vN", ...) 再 compare() / check_coverage()。
标准结果布局与 Anthropic kit 的 /claude/hillclimb/<name>/<variant>/ 对齐，可直接喂给 compare / chain_coverage。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class EvalResult:
    task_id: str
    passed: bool
    latency_ms: float = 0.0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    route: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    chain: list[str] = field(default_factory=list)
    rep: int = 0


class BaseEvalRunner:
    def __init__(self, flow_dir, tasks):
        self.flow_dir = Path(flow_dir)
        self.tasks = tasks

    def run_one(self, task) -> EvalResult:
        raise NotImplementedError("子类实现 run_one(task) -> EvalResult")

    def run_variant(self, variant: str, reps: int = 1):
        out = self.flow_dir / variant
        out.mkdir(parents=True, exist_ok=True)
        rows = []
        for rep in range(reps):
            for t in self.tasks:
                r = self.run_one(t)
                r.rep = rep
                rows.append(r)
        with (out / "results.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        return rows

    def compare(self, before, after, **kw):
        from .compare import compare
        return compare(self.flow_dir, before, after, **kw)

    def check_coverage(self, traces, baseline):
        from .chain_coverage import check
        return check(self.flow_dir, baseline, traces)
