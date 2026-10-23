"""
adapters.py — 真实模型客户端适配器（示例）
把 agent_loop.ModelClient 协议接到 Anthropic / OpenAI 的 SDK。
两个依赖都是可选的：没装就不导入，骨架本体仍零依赖可跑。

用法：
    from agent_loop import Agent, Tool
    from adapters import AnthropicModelClient
    client = AnthropicModelClient(model="claude-opus-4-5")
    agent = Agent(system=..., tools=[...], client=client, approve=..., verify=...)

注意：适配器只负责"消息格式 <-> SDK"的翻译。write gate / 预算 / verifier
仍由 Agent 负责，与后端无关。
"""
from __future__ import annotations

import json
import os
from typing import Any

from agent_loop import ModelClient, ModelResponse, _Block


def _block_to_dict(b: Any) -> dict:
    """把 _Block 或 SDK 内容块统一成 dict（Anthropic 请求要 dict）。"""
    if isinstance(b, dict):
        return b
    t = getattr(b, "type", None)
    if t is None:
        return dict(b) if hasattr(b, "items") else {}
    out = {"type": t}
    for attr in ("text", "id", "name", "input"):
        v = getattr(b, attr, None)
        if v is not None:
            out[attr] = v
    return out


def _normalize_messages(messages: list) -> list[dict]:
    """把 Agent 内部累积的 messages（可能含 _Block 对象或 SDK 块）转成 plain dict。"""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if isinstance(content, list):
            out.append({"role": role, "content": [_block_to_dict(b) for b in content]})
        else:
            out.append({"role": role, "content": content})
    return out


class AnthropicModelClient:
    """Anthropic 官方 SDK 适配器。"""
    def __init__(self, model: str | None = None, client: Any | None = None,
                 api_key: str | None = None):
        try:
            import anthropic  # lazy
        except ImportError as e:
            raise RuntimeError("请先 `pip install anthropic`") from e
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-5")
        self.client = client or anthropic.Anthropic(api_key=api_key)

    def create(self, *, model: str, max_tokens: int, system: str,
               tools: list[dict], messages: list[dict]) -> ModelResponse:
        resp = self.client.messages.create(
            model=model or self.model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=_normalize_messages(messages),
        )
        usage = resp.usage
        content = [
            _Block(b.type,
                   text=getattr(b, "text", None),
                   id=getattr(b, "id", None),
                   name=getattr(b, "name", None),
                   input=dict(getattr(b, "input", {}) or {}))
            for b in resp.content
        ]
        return ModelResponse(content, resp.stop_reason, {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
        })


class OpenAIModelClient:
    """OpenAI 兼容适配器（示例）。消息格式与 Anthropic 差异较大，这里做完整翻译。

    注意：OpenAI 不支持 Anthropic 的 defer_loading / tool_search 服务端工具，
    使用 deferred 工具时请走你自己的工具检索层。
    """
    def __init__(self, model: str | None = None, client: Any | None = None,
                 api_key: str | None = None):
        try:
            from openai import OpenAI  # lazy
        except ImportError as e:
            raise RuntimeError("请先 `pip install openai`") from e
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-5")
        self.client = client or OpenAI(api_key=api_key)

    @staticmethod
    def _to_openai_tools(tools: list[dict]) -> list[dict]:
        out = []
        for t in tools:
            if t.get("name") == "tool_search":
                continue  # OpenAI 无对应服务端工具
            out.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        return out

    @classmethod
    def _to_openai_messages(cls, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role == "user" and isinstance(content, list):
                texts, tool_results = [], []
                for b in content:
                    b = _block_to_dict(b)
                    if b.get("type") == "tool_result":
                        tool_results.append({"role": "tool",
                                            "tool_call_id": b.get("tool_use_id"),
                                            "content": b.get("content", "")})
                    elif b.get("type") == "text":
                        texts.append(b.get("text", ""))
                if texts:
                    out.append({"role": "user", "content": "\n".join(texts)})
                out.extend(tool_results)
                continue
            if role == "assistant" and isinstance(content, list):
                text_parts, tool_calls = [], []
                for b in content:
                    b = _block_to_dict(b)
                    if b.get("type") == "text":
                        text_parts.append(b.get("text", ""))
                    elif b.get("type") == "tool_use":
                        tool_calls.append({
                            "id": b.get("id"),
                            "type": "function",
                            "function": {"name": b.get("name"),
                                         "arguments": json.dumps(b.get("input", {}), ensure_ascii=False)},
                        })
                msg: dict = {"role": "assistant"}
                if text_parts:
                    msg["content"] = "\n".join(text_parts)
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                out.append(msg)
                continue
            out.append({"role": role, "content": content})
        return out

    def create(self, *, model: str, max_tokens: int, system: str,
               tools: list[dict], messages: list[dict]) -> ModelResponse:
        oa_tools = self._to_openai_tools(tools)
        oa_messages = [{"role": "system", "content": system}] + self._to_openai_messages(messages)
        resp = self.client.chat.completions.create(
            model=model or self.model,
            max_tokens=max_tokens,
            messages=oa_messages,
            tools=oa_tools or None,
        )
        msg = resp.choices[0].message
        content: list = []
        if msg.content:
            content.append(_Block("text", text=msg.content))
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            content.append(_Block("tool_use", id=tc.id, name=tc.function.name, input=args))
        usage = resp.usage
        stop = "tool_use" if msg.tool_calls else "end_turn"
        return ModelResponse(content, stop, {
            "input_tokens": getattr(usage, "prompt_tokens", 0),
            "output_tokens": getattr(usage, "completion_tokens", 0),
        })
