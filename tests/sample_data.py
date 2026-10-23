"""测试/演示用的样本数据：构造一个含两个 variant + 生产 trace 的 flow 目录。"""
import json
import tempfile
from pathlib import Path


def make_sample_flow():
    d = Path(tempfile.mkdtemp(prefix="evalkit-"))

    base = [
        {"task_id": "t01", "rep": 0, "passed": True, "latency_ms": 120, "model_calls": 3,
         "input_tokens": 1000, "output_tokens": 200, "cost": 0.01,
         "route": ["router", "lookup"], "writes": [], "chain": ["router", "lookup"]},
        {"task_id": "t02", "rep": 0, "passed": True, "latency_ms": 150, "model_calls": 4,
         "input_tokens": 1200, "output_tokens": 250, "cost": 0.012,
         "route": ["router", "publish"], "writes": ["publish"], "chain": ["router", "publish"]},
        {"task_id": "t03", "rep": 0, "passed": True, "latency_ms": 200, "model_calls": 5,
         "input_tokens": 1500, "output_tokens": 300, "cost": 0.02,
         "route": ["router", "lookup", "publish"], "writes": ["publish"], "chain": ["router", "lookup", "publish"]},
        {"task_id": "t04", "rep": 0, "passed": False, "latency_ms": 300, "model_calls": 6,
         "input_tokens": 2000, "output_tokens": 400, "cost": 0.03,
         "route": ["router", "diagnostics"], "writes": [], "chain": ["router", "diagnostics"]},
    ]
    # after：t02 折叠后失败（route 变了、passed 翻转），t04 修好，其余保持
    after = [
        {"task_id": "t01", "rep": 0, "passed": True, "latency_ms": 110, "model_calls": 3,
         "input_tokens": 980, "output_tokens": 190, "cost": 0.009,
         "route": ["router", "lookup"], "writes": [], "chain": ["router", "lookup"]},
        {"task_id": "t02", "rep": 0, "passed": False, "latency_ms": 160, "model_calls": 4,
         "input_tokens": 1250, "output_tokens": 260, "cost": 0.013,
         "route": ["router", "publish_tool"], "writes": ["publish"], "chain": ["router", "publish"]},
        {"task_id": "t03", "rep": 0, "passed": True, "latency_ms": 190, "model_calls": 5,
         "input_tokens": 1480, "output_tokens": 300, "cost": 0.02,
         "route": ["router", "lookup", "publish_tool"], "writes": ["publish"], "chain": ["router", "lookup", "publish"]},
        {"task_id": "t04", "rep": 0, "passed": True, "latency_ms": 280, "model_calls": 6,
         "input_tokens": 1950, "output_tokens": 390, "cost": 0.029,
         "route": ["router", "diagnostics"], "writes": [], "chain": ["router", "diagnostics"]},
    ]

    (d / "baseline").mkdir()
    (d / "after").mkdir()
    (d / "baseline" / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in base) + "\n", encoding="utf-8")
    (d / "after" / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in after) + "\n", encoding="utf-8")

    traces = [
        {"chain": ["router", "lookup"]},
        {"chain": ["router", "publish"]},
        {"chain": ["router", "lookup", "publish"]},
        {"chain": ["router", "diagnostics"]},
        {"chain": ["router", "billing"]},   # 生产里有、eval 没覆盖 -> coverage FAIL
    ]
    (d / "traces.jsonl").write_text(
        "\n".join(json.dumps(t, ensure_ascii=False) for t in traces) + "\n", encoding="utf-8")

    tasks = [{"task_id": t["task_id"], "chain": t["chain"]} for t in base]
    return d, tasks
