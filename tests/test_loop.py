import json
import unittest

from agent_loop import Agent, Tool, ModelResponse, _Block


class FakeModelClient:
    """确定性假模型：先调 read，再调 write，之后作答。"""
    def __init__(self, read_name, write_name):
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


class TestLoop(unittest.TestCase):
    def test_write_gate_declines_then_verifier_passes(self):
        tools = [
            Tool("lookup", "Read-only.", {"type": "object", "properties": {"q": {"type": "string"}}},
                lambda a: json.dumps({"temp": 22}), writes=False),
            Tool("publish", "Writes externally.", {"type": "object",
                 "properties": {"to": {"type": "string"}, "body": {"type": "string"}}},
                lambda a: json.dumps({"sent": True}), writes=True),
        ]
        approve = lambda tool, args: False   # 闸门拒绝一切写操作
        st = {"n": 0}

        def verify(text, messages):
            st["n"] += 1
            return "请确认这是给用户的最终答复。" if st["n"] == 1 else None

        agent = Agent(system="你是助手。", tools=tools,
                      client=FakeModelClient("lookup", "publish"),
                      approve=approve, verify=verify)
        res = agent.run("查一下温度并发布。")
        self.assertEqual(res.stop, "answered")
        self.assertEqual(res.turns, 4)
        self.assertEqual(res.text, "已完成。")
        complaints = [
            m.get("content") for m in res.messages
            if isinstance(m.get("content"), str) and "请确认这是给用户的最终答复。" in m["content"]
        ]
        self.assertEqual(len(complaints), 1)

    def test_two_reads_in_one_response_both_run(self):
        """Both read calls in one response run. This does not show they overlapped in time."""
        seen = []

        def make(name):
            def run(args):
                seen.append(name)
                return name
            return run

        tools = [
            Tool("alpha", "A", {"type": "object", "properties": {}}, make("alpha")),
            Tool("beta", "B", {"type": "object", "properties": {}}, make("beta")),
        ]

        class TwoReadsThenAnswer:
            def __init__(self):
                self.n = 0

            def create(self, *, model, max_tokens, system, tools, messages):
                self.n += 1
                if self.n == 1:
                    return ModelResponse([
                        _Block("tool_use", id="1", name="alpha", input={}),
                        _Block("tool_use", id="2", name="beta", input={}),
                    ], "tool_use", {"input_tokens": 1, "output_tokens": 1})
                return ModelResponse([_Block("text", text="ok")],
                                     "end_turn", {"input_tokens": 1, "output_tokens": 1})

        agent = Agent(system="s", tools=tools, client=TwoReadsThenAnswer())
        res = agent.run("go")
        self.assertEqual(res.stop, "answered")
        self.assertCountEqual(seen, ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
