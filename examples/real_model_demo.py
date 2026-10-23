"""
real_model_demo.py — 接真实模型的端到端示例（Anthropic / OpenAI 二选一）。

零改动即可跑骨架；只需提供对应的 API key 环境变量，并 pip install 对应 SDK。
说明：安全原语（写闸门 / 预算 / verifier）仍由 Agent 负责，与后端无关——
适配器只做消息格式的双向翻译。

用法:
    export ANTHROPIC_API_KEY=sk-...        # 用 Anthropic 后端
    # 或 export OPENAI_API_KEY=sk-...      # 用 OpenAI 后端
    pip install -e ".[anthropic]"          # 或 ".[openai]"
    export ALLOW_WRITES=1                  # 可选：放行写工具（否则默认拒绝，演示"人在环"）
    python examples/real_model_demo.py \
        --flow-dir flow/real_run           # 可选：把结果写入 evalkit 布局，便于直接 compare
        --task "泰安今天天气怎么样？用 get_weather 工具查。"

无 key 时运行会打印接入说明并以 exit code 2 退出，不会抛错。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_loop import Agent, Tool, allow_all_writes


def _make_tools():
    """两个示例工具：只读天气 + 写外部（发消息）。"""

    def get_weather(args):
        return f"Weather in {args.get('city', '泰安')}: 22C, clear."

    def send_message(args):
        return f"sent to {args.get('to')}: {args.get('body')}"

    return [
        Tool("get_weather", "Look up the weather for a city (read-only).",
             {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
             get_weather, writes=False),
        Tool("send_message", "Send a message to a recipient (writes externally).",
             {"type": "object",
              "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
              "required": ["to", "body"]},
             send_message, writes=True),
    ]


def _make_approver():
    """写闸门：默认拒绝；设置 ALLOW_WRITES=1 才放行（演示"人在环"）。"""
    if os.environ.get("ALLOW_WRITES") == "1":
        return allow_all_writes

    def approve(tool, args):
        print(f"[write gate] declined write to {tool.name}: {args}")
        return False

    return approve


def _build_client():
    """按环境变量选后端；无 key 时返回 None 并打印说明。"""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from adapters import AnthropicModelClient
            client = AnthropicModelClient()
        except RuntimeError as e:
            print(f"[error] {e}")
            print('请先: pip install -e ".[anthropic]"')
            sys.exit(2)
        print("[client] using Anthropic backend")
        return client

    if os.environ.get("OPENAI_API_KEY"):
        try:
            from adapters import OpenAIModelClient
            client = OpenAIModelClient()
        except RuntimeError as e:
            print(f"[error] {e}")
            print('请先: pip install -e ".[openai]"')
            sys.exit(2)
        print("[client] using OpenAI backend")
        return client

    print("=" * 60)
    print("未检测到 API key，演示无法连接真实模型。")
    print('  export ANTHROPIC_API_KEY=sk-...   # 然后 pip install -e ".[anthropic]"')
    print('  export OPENAI_API_KEY=sk-...      # 然后 pip install -e ".[openai]"')
    print("可选：export ALLOW_WRITES=1 放行写工具（否则默认拒绝）")
    print("=" * 60)
    return None


def _route_writes(messages, write_names):
    route, writes = [], []
    for m in messages:
        c = m.get("content")
        items = c if isinstance(c, list) else []
        for b in items:
            name = b.get("name") if isinstance(b, dict) else getattr(b, "name", None)
            typ = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
            if typ == "tool_use" and name:
                route.append(name)
                if name in write_names:
                    writes.append(name)
    return route, writes


def main():
    ap = argparse.ArgumentParser(description="Real-model end-to-end demo for agent-loop-safety.")
    ap.add_argument("--flow-dir", default=None,
                    help="把结果写入该目录下的 <variant>/results.jsonl（evalkit 布局）")
    ap.add_argument("--task", default="泰安今天天气怎么样？用 get_weather 工具查。", help="要运行的任务文本")
    ap.add_argument("--variant", default="real_run", help="写入 flow-dir 时的 variant 名")
    args = ap.parse_args()

    client = _build_client()
    if client is None:
        sys.exit(2)

    tools = _make_tools()
    calls = {"n": 0}
    agent = Agent(
        system="你是简洁的助手。优先用工具获取事实，再作答。",
        tools=tools,
        client=client,
        approve=_make_approver(),
        model=os.environ.get("AGENT_MODEL", ""),
        on_usage=lambda u: calls.__setitem__("n", calls["n"] + 1),
    )

    t0 = time.time()
    res = agent.run(args.task)
    latency = (time.time() - t0) * 1000
    route, writes = _route_writes(res.messages, {"send_message"})
    print("-" * 60)
    print("stop       =", res.stop)
    print("turns      =", res.turns)
    print("tokens     =", res.input_tokens, "/", res.output_tokens)
    print("model calls=", calls["n"])
    print("latency_ms =", round(latency, 1))
    print("route      =", route)
    print("writes     =", writes)
    print("final text =", repr(res.text))

    if args.flow_dir:
        d = Path(args.flow_dir)
        (d / args.variant).mkdir(parents=True, exist_ok=True)
        row = {
            "task_id": args.variant, "rep": 0,
            "passed": res.stop == "answered" and bool(res.text),
            "latency_ms": round(latency, 1),
            "model_calls": calls["n"],
            "input_tokens": res.input_tokens,
            "output_tokens": res.output_tokens,
            "cost": 0.0,
            "route": route, "writes": writes, "chain": route,
        }
        (d / args.variant / "results.jsonl").write_text(
            json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        (d / "traces.jsonl").write_text(
            json.dumps({"chain": route}, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\n[evalkit] wrote {d / args.variant / 'results.jsonl'}")
        print(f"[evalkit] 可直接: python -m evalkit.compare {d} {args.variant} <other_variant>")


if __name__ == "__main__":
    main()
