"""
agent_loop_skeleton.py
单 agent 循环 harness 骨架 —— 提炼自 Anthropic reference_loop/loop.py 的安全原语。
只保留"与后端无关、可直接复用"的部分：
    * write gate       写外部状态的工具必须经过审批，缺失闸门绝不默认放行
    * token 预算       含子 agent 花费，跨整次 run 累计
    * turn 预算 / 批次上限
    * 可选 verifier    答案被拒两次则扣留，不返回给用户
    * 渐进披露         skill 系统提示只放一行描述；deferred 工具走 tool_search
不是框架、不依赖 anthropic SDK：把你自己的客户端实现 ModelClient 协议即可接入。
零依赖（仅标准库）。`python agent_loop_skeleton.py` 用内置 FakeModelClient 离线自检。
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


# ───────────────────────── 预算 ─────────────────────────
@dataclass
class Budget:
    max_turns: int = 25
    max_tokens: int = 400_000   # input+output 合计，含子 agent 花费
    max_batch: int = 16         # 单轮最多并行执行的工具调用；超出部分返回错误结果


# ───────────────────────── 工具 ─────────────────────────
@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    run: Callable[[dict], Any]
    writes: bool = False         # 改变对话外的状态 -> 必须过 write gate
    deferred: bool = False       # schema 走 tool_search 按需加载，不进每个请求


@dataclass
class Result:
    text: str
    stop: str                   # answered|verify_failed|turn_budget|token_budget|refusal|max_tokens
    turns: int
    input_tokens: int
    output_tokens: int
    messages: list = field(repr=False, default_factory=list)


def allow_all_writes(tool: Tool, args: dict) -> bool:
    """eval / 离线场景下"无人可问"时显式声明放行。只有无人可问才用它。"""
    return True


# ─────────────────── 模型客户端抽象（解耦 Anthropic SDK） ───────────────────
class _Block:
    """模型返回的内容块。真实 SDK 块有 .type/.text/.id/.name/.input 属性，这里用同名对象对齐。"""
    def __init__(self, type: str, text: str | None = None, id: str | None = None,
                 name: str | None = None, input: dict | None = None):
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input


class ModelResponse:
    def __init__(self, content: list, stop_reason: str, usage: dict):
        self.content = content          # list[_Block]
        self.stop_reason = stop_reason  # "tool_use" | "end_turn" | "refusal" | "max_tokens" ...
        self.usage = usage              # {"input_tokens":int, "output_tokens":int, ...}


class ModelClient(Protocol):
    def create(self, *, model: str, max_tokens: int, system: str,
               tools: list[dict], messages: list[dict]) -> ModelResponse: ...


# ───────────────────────── token 计量 ─────────────────────────
def _tokens(usage: dict) -> tuple[int, int]:
    return (usage.get("input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0),
            usage.get("output_tokens", 0))


def load_skills(directory) -> dict:
    """从 <dir>/<skill>/SKILL.md 读 name+description。带两项安全校验。"""
    skills: dict = {}
    if not directory:
        return skills
    root = Path(directory).resolve()
    for path in sorted(Path(directory).glob("*/SKILL.md")):
        if not path.resolve().is_relative_to(root):   # 符号链接不得把目录外文件拉进来
            raise ValueError(f"Skill {path} points outside {root}.")
        text = path.read_text()
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        meta = dict(re.findall(r"^(\w+):\s*(.+)$", m.group(1), re.M)) if m else {}
        name = meta.get("name", path.parent.name).strip().strip("\"'")
        if name.lower() in skills:                    # 重名 skill 静默替换是隐患 -> 直接报错
            raise ValueError(f"Two skills are named {name}.")
        skills[name] = {"description": meta.get("description", ""), "path": path}
    return skills


class Agent:
    def __init__(self, *, system: str, tools: list[Tool], skills_dir=None, model: str = "",
                 client: ModelClient, budget: Budget | None = None,
                 approve: Callable[[Tool, dict], bool] | None = None,
                 verify: Callable[[str, list], str | None] | None = None,
                 on_usage: Callable[[dict], None] | None = None, cache: bool = True):
        self.model = model
        self.budget = budget or Budget()
        self.client = client
        self.verify = verify
        self.on_usage = on_usage

        # —— 安全校验①：有写工具但没给闸门 -> 拒绝构造，而非默认放行 ——
        unguarded = [t.name for t in tools if t.writes]
        if unguarded and approve is None:
            raise ValueError(
                f"这些工具会写外部状态: {', '.join(unguarded)}。必须传 approve= 闸门；"
                "无人可问的 eval/离线场景用 approve=allow_all_writes 显式声明放行。")

        self._used = 0
        self._lock = threading.Lock()        # 保护跨线程的预算累加
        self._running = threading.Lock()     # 一个 Agent 实例一次只跑一个对话
        self.approve = approve

        self.skills = load_skills(skills_dir)
        self.tools = {t.name: t for t in tools}

        # —— 渐进披露：系统提示只放 skill 的一行描述，load_skill 按需加载全文 ——
        if self.skills:
            self.tools["load_skill"] = Tool(
                "load_skill", "Load the full instructions for one skill, before work it covers.",
                {"type": "object",
                 "properties": {"name": {"type": "string", "enum": sorted(self.skills)}},
                 "required": ["name"]},
                lambda a: self.skills[a["name"]]["path"].read_text())
            listing = "\n".join(f"- {n}: {s['description']}" for n, s in self.skills.items())
            system += f"\n\n## Skills\nLoad a skill with load_skill before doing work it covers.\n\n{listing}"

        if self.tools and all(t.deferred for t in self.tools.values()):
            raise ValueError("At least one tool must not be deferred.")

        self.system = system
        self.extra = {"cache_control": {"type": "ephemeral"}} if cache else {}

    # ── 预算（供子 agent / 工具把花费计入同一预算）──
    def charge(self, usage: dict) -> bool:
        with self._lock:
            self._used += sum(_tokens(usage))
            return not self._over_budget()

    def _over_budget(self) -> bool:
        return self._used > self.budget.max_tokens

    # ── 工具清单：deferred 工具不进每个请求，改走 tool_search ──
    def _tool_params(self) -> list[dict]:
        params = []
        for t in self.tools.values():
            p = {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            if t.deferred:
                p["defer_loading"] = True
            params.append(p)
        if any(t.deferred for t in self.tools.values()):
            params.insert(0, {
                "name": "tool_search",
                "description": "Find tools by keyword; returns matching deferred tool schemas.",
                "input_schema": {"type": "object",
                                 "properties": {"query": {"type": "string"}},
                                 "required": ["query"]}})
        return params

    def _local_tool_search(self, query: str) -> str:
        q = re.findall(r"\w+", (query or "").lower())
        out = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
               for t in self.tools.values()
               if t.deferred and any(k in (t.name + " " + t.description).lower() for k in q)]
        return json.dumps(out, ensure_ascii=False)

    @staticmethod
    def _result(call, content: str, error: bool = False) -> dict:
        r = {"type": "tool_result", "tool_use_id": call.id, "content": str(content)}
        if error:
            r["is_error"] = True
        return r

    # ── 单条工具执行 ──
    def _execute(self, call) -> dict:
        if call.name == "tool_search":
            return self._result(call, self._local_tool_search(call.input.get("query", "")))
        tool = self.tools.get(call.name)
        if tool is None:
            return self._result(call, f"There is no tool named {call.name}.", error=True)
        if self._over_budget():
            return self._result(call, "Token budget for this request is spent; this call did not run.", error=True)
        # —— 安全校验②：写工具没闸门的运行时兜底（构造时一般已拦，但动态加工具时也拦）——
        if tool.writes and self.approve is None:
            return self._result(call, f"{tool.name} writes externally and has no write gate; it did not run.", error=True)
        if tool.writes and not self.approve(tool, call.input):
            return self._result(call, "The user declined this action. Do not retry; ask what they want instead.")
        try:
            out = tool.run(call.input)
        except Exception as exc:   # 让模型看到错误通常能自我恢复
            return self._result(call, f"{type(exc).__name__}: {exc}", error=True)
        return self._result(call, out if isinstance(out, str) else json.dumps(out, default=str))

    # ── 一轮：reads 并行，writes 在 reads 之后按序执行 ──
    def _run_batch(self, calls: list) -> list[dict]:
        limit = self.budget.max_batch
        done: dict[str, dict] = {}
        for c in calls[limit:]:
            done[c.id] = self._result(
                c, f"Too many tool calls in one turn (limit {limit}); this did not run.", error=True)
        reads = [c for c in calls[:limit] if not (self.tools.get(c.name) and self.tools[c.name].writes)]
        if reads:
            with ThreadPoolExecutor(max_workers=min(8, len(reads))) as pool:
                for c, res in zip(reads, pool.map(self._execute, reads)):
                    done[c.id] = res
        for c in calls:
            if c.id not in done:
                done[c.id] = self._execute(c)   # 写操作逐个、按模型给定顺序
        return [done[c.id] for c in calls]

    # ── 对外入口：一次只跑一条用户消息；预算按 run 计 ──
    def run(self, user_text: str, messages: list | None = None) -> Result:
        if not self._running.acquire(blocking=False):
            raise RuntimeError("This Agent is already in a run. Build one Agent per conversation.")
        try:
            return self._run(user_text, messages if messages is not None else [])
        finally:
            self._running.release()

    def _run(self, user_text: str, messages: list) -> Result:
        messages.append({"role": "user", "content": user_text})
        tin = tout = 0
        self._used = 0
        complaints = 0
        for turn in range(1, self.budget.max_turns + 1):
            resp = self.client.create(model=self.model, max_tokens=16000, system=self.system,
                                     tools=self._tool_params(), messages=messages)
            if self.on_usage:
                self.on_usage(resp.usage)
            i, o = _tokens(resp.usage)
            tin, tout = tin + i, tout + o
            self.charge(resp.usage)
            messages.append({"role": "assistant", "content": resp.content})
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

            if resp.stop_reason == "pause_turn":   # 服务端工具（如 tool_search）要再来一轮
                continue
            if resp.stop_reason in ("refusal", "max_tokens", "model_context_window_exceeded"):
                cuts = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
                if cuts:
                    messages.append({"role": "user", "content": [
                        self._result(b, "This call was cut off before it finished and did not run.", error=True)
                        for b in cuts]})
                if self.verify and text and self.verify(text, messages):
                    text = ""
                return Result(text, resp.stop_reason, turn, tin, tout, messages)
            if resp.stop_reason != "tool_use":
                complaint = self.verify(text, messages) if self.verify else None
                if not complaint:
                    return Result(text, "answered", turn, tin, tout, messages)
                complaints += 1
                if complaints > 1:   # 只给一次修正机会，避免无限循环
                    messages.append({"role": "user", "content": "(Neither answer above was shown to the user.)"})
                    return Result("", "verify_failed", turn, tin, tout, messages)
                messages.append({"role": "user", "content": f"Before this goes to the user: {complaint}"})
                continue

            calls = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            messages.append({"role": "user", "content": self._run_batch(calls)})
            if self._over_budget():
                return Result("", "token_budget", turn, tin, tout, messages)
        return Result("", "turn_budget", self.budget.max_turns, tin, tout, messages)


# ───────────────────────── 离线自检（FakeModelClient） ─────────────────────────
class FakeModelClient:
    """确定性假模型：先调 read 工具，再调 write 工具，之后作答。用来验证循环/批次/闸门/verifier。"""
    def __init__(self, read_name: str, write_name: str):
        self.read_name = read_name
        self.write_name = write_name

    def create(self, *, model, max_tokens, system, tools, messages):
        called = set()
        for m in messages:
            c = m.get("content")
            items = c if isinstance(c, list) else []
            for b in items:
                bt = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                bn = b.get("name") if isinstance(b, dict) else getattr(b, "name", None)
                if bt == "tool_use" and bn:
                    called.add(bn)
        if self.read_name and self.read_name not in called:
            return ModelResponse([_Block("tool_use", id="c1", name=self.read_name, input={"q": "x"})],
                                 "tool_use", {"input_tokens": 80, "output_tokens": 20})
        if self.write_name and self.write_name not in called:
            return ModelResponse([_Block("tool_use", id="c2", name=self.write_name, input={"to": "u", "body": "hi"})],
                                 "tool_use", {"input_tokens": 90, "output_tokens": 25})
        return ModelResponse([_Block("text", text="已完成。")],
                             "end_turn", {"input_tokens": 60, "output_tokens": 12})


def _demo():
    def read_tool(args):  return json.dumps({"temp": 22})
    def write_tool(args): return json.dumps({"sent": True})

    tools = [
        Tool("lookup", "Read-only lookup.", {"type": "object", "properties": {"q": {"type": "string"}}},
            read_tool, writes=False),
        Tool("publish", "Writes externally.", {"type": "object",
            "properties": {"to": {"type": "string"}, "body": {"type": "string"}}},
            write_tool, writes=True),
    ]

    # 闸门：这里演示"拒绝全部写操作"；换成 allow_all_writes 即放行。
    approve = lambda tool, args: False

    # verifier：第一次作答挑刺，给模型一次修正机会。
    st = {"n": 0}
    def verify(text, messages):
        st["n"] += 1
        return "请确认这是给用户的最终答复。" if st["n"] == 1 else None

    agent = Agent(system="你是助手。", tools=tools, client=FakeModelClient("lookup", "publish"),
                  approve=approve, verify=verify)
    res = agent.run("查一下温度并发布。")
    print("stop      =", res.stop)
    print("turns     =", res.turns)
    print("tokens    =", res.input_tokens, "/", res.output_tokens)
    print("final text=", repr(res.text))
    # 预期：lookup 跑通 -> publish 被闸门拒绝(declined) -> 作答被 verifier 拦一次 -> 修正后 answered


if __name__ == "__main__":
    _demo()
