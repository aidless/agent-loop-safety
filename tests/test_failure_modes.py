"""Lock the holes described in FAILURE_MODES.md.

A failure here means the hole closed or moved. Update FAILURE_MODES.md in the
same change. These tests are not a safety certificate and they do not call a model.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from adapters import reported_usage
from agent_loop import Agent, Budget, Tool, ModelResponse, _Block
from evalkit.chain_coverage import check
from evalkit.compare import compare, per_task, summarize
from tests.sample_data import make_sample_flow

ROOT = Path(__file__).resolve().parents[1]


class Scripted:
    def __init__(self, responses):
        self.responses = list(responses)

    def create(self, *, model, max_tokens, system, tools, messages):
        if not self.responses:
            raise AssertionError("model was called more times than scripted")
        return self.responses.pop(0)


def _text(text, stop="end_turn", usage=None):
    usage = usage or {"input_tokens": 1, "output_tokens": 1}
    return ModelResponse([_Block("text", text=text)], stop, usage)


def _tool(name, usage=None):
    usage = usage or {"input_tokens": 1, "output_tokens": 1}
    return ModelResponse(
        [_Block("tool_use", id="c1", name=name, input={})], "tool_use", usage)


class TestVerifierFalseGreens(unittest.TestCase):
    def test_second_none_accepts_unchanged_text(self):
        seen = []

        def verify(text, messages):
            seen.append(text)
            return "revise" if len(seen) == 1 else None

        agent = Agent(
            system="s", tools=[], client=Scripted([_text("已完成。"), _text("已完成。")]),
            verify=verify)
        res = agent.run("hi")
        self.assertEqual(seen, ["已完成。", "已完成。"])
        self.assertEqual(res.stop, "answered")
        self.assertEqual(res.text, "已完成。")

    def test_second_complaint_withholds(self):
        agent = Agent(
            system="s", tools=[],
            client=Scripted([_text("draft"), _text("draft")]),
            verify=lambda text, messages: "no")
        res = agent.run("hi")
        self.assertEqual(res.stop, "verify_failed")
        self.assertEqual(res.text, "")

    def test_omitted_verifier_answers(self):
        agent = Agent(system="s", tools=[], client=Scripted([_text("anything")]))
        res = agent.run("hi")
        self.assertEqual(res.stop, "answered")
        self.assertEqual(res.text, "anything")

    def test_refusal_keeps_text_when_verifier_is_silent(self):
        agent = Agent(
            system="s", tools=[],
            client=Scripted([_text("partial", stop="refusal")]),
            verify=lambda text, messages: None)
        res = agent.run("hi")
        self.assertEqual(res.stop, "refusal")
        self.assertEqual(res.text, "partial")


class TestBudgetBypass(unittest.TestCase):
    def test_final_answer_can_exceed_token_budget(self):
        agent = Agent(
            system="s", tools=[],
            client=Scripted([_text("done", usage={"input_tokens": 500, "output_tokens": 0})]),
            budget=Budget(max_tokens=10))
        res = agent.run("hi")
        self.assertEqual(res.stop, "answered")
        self.assertGreater(res.input_tokens, 10)

    def test_zero_usage_never_trips_budget(self):
        ran = {"n": 0}

        def read(_args):
            ran["n"] += 1
            return "ok"

        tools = [Tool("lookup", "r", {"type": "object", "properties": {}}, read)]
        agent = Agent(
            system="s", tools=tools,
            client=Scripted([
                _tool("lookup", usage={"input_tokens": 0, "output_tokens": 0}),
                _text("done", usage={"input_tokens": 0, "output_tokens": 0}),
            ]),
            budget=Budget(max_tokens=1))
        res = agent.run("hi")
        self.assertEqual(ran["n"], 1)
        self.assertEqual(res.stop, "answered")

    def test_reported_tokens_trip_budget_after_a_tool_batch(self):
        tools = [Tool("lookup", "r", {"type": "object", "properties": {}}, lambda a: "ok")]
        agent = Agent(
            system="s", tools=tools,
            client=Scripted([
                _tool("lookup", usage={"input_tokens": 100, "output_tokens": 0}),
                _text("should-not-be-used"),
            ]),
            budget=Budget(max_tokens=10))
        res = agent.run("hi")
        self.assertEqual(res.stop, "token_budget")
        self.assertEqual(res.text, "")

    def test_pause_turn_ignores_token_budget_until_turn_cap(self):
        agent = Agent(
            system="s", tools=[],
            client=Scripted([
                _text("", stop="pause_turn", usage={"input_tokens": 100, "output_tokens": 0}),
                _text("", stop="pause_turn", usage={"input_tokens": 100, "output_tokens": 0}),
            ]),
            budget=Budget(max_tokens=1, max_turns=2))
        res = agent.run("hi")
        self.assertEqual(res.stop, "turn_budget")
        self.assertEqual(res.input_tokens, 200)

    def test_tool_that_forgets_charge_does_not_grow_the_budget(self):
        def run(_args):
            return "ok"  # does not call agent.charge

        tools = [Tool("lookup", "r", {"type": "object", "properties": {}}, run)]
        agent = Agent(
            system="s", tools=tools,
            client=Scripted([
                _tool("lookup", usage={"input_tokens": 5, "output_tokens": 0}),
                _text("done", usage={"input_tokens": 1, "output_tokens": 0}),
            ]),
            budget=Budget(max_tokens=1000))
        res = agent.run("hi")
        self.assertEqual(res.stop, "answered")
        self.assertEqual(agent._used, 6)

    def test_reported_usage_counts_dict_and_none_as_zero(self):
        fields = {"input_tokens": "input_tokens", "output_tokens": "output_tokens"}
        self.assertEqual(reported_usage(None, fields), {"input_tokens": 0, "output_tokens": 0})
        self.assertEqual(
            reported_usage({"input_tokens": 50, "output_tokens": 7}, fields),
            {"input_tokens": 0, "output_tokens": 0})
        live = SimpleNamespace(input_tokens=50, output_tokens=7)
        self.assertEqual(reported_usage(live, fields), {"input_tokens": 50, "output_tokens": 7})
        openai_shape = SimpleNamespace(prompt_tokens=3, completion_tokens=4)
        self.assertEqual(
            reported_usage(openai_shape, {
                "input_tokens": "prompt_tokens",
                "output_tokens": "completion_tokens",
            }),
            {"input_tokens": 3, "output_tokens": 4})

    def test_anthropic_adapter_is_optional(self):
        self._adapter_is_optional("anthropic", "AnthropicModelClient")

    def test_openai_adapter_is_optional(self):
        self._adapter_is_optional("openai", "OpenAIModelClient")

    def _adapter_is_optional(self, module_name, cls_name):
        import adapters
        try:
            __import__(module_name)
        except ImportError:
            with self.assertRaises(RuntimeError):
                getattr(adapters, cls_name)()
        else:
            self.skipTest(f"{module_name} is installed; the SDK client was not constructed")


class TestWriteGateStaysLocal(unittest.TestCase):
    def test_missing_approve_refuses_construction(self):
        tools = [Tool("publish", "w", {"type": "object", "properties": {}}, lambda a: "sent", writes=True)]
        with self.assertRaises(ValueError):
            Agent(system="s", tools=tools, client=Scripted([]))

    def test_zero_usage_does_not_run_a_declined_write(self):
        ran = {"n": 0}

        def write(_args):
            ran["n"] += 1
            return "sent"

        tools = [Tool("publish", "w", {"type": "object", "properties": {}}, write, writes=True)]
        agent = Agent(
            system="s", tools=tools,
            client=Scripted([
                _tool("publish", usage={"input_tokens": 0, "output_tokens": 0}),
                _text("done", usage={"input_tokens": 0, "output_tokens": 0}),
            ]),
            approve=lambda tool, args: False,
            budget=Budget(max_tokens=1))
        res = agent.run("hi")
        self.assertEqual(ran["n"], 0)
        self.assertEqual(res.stop, "answered")


class TestToyEvalOverclaim(unittest.TestCase):
    def test_canceling_flips_still_hold(self):
        with self._flow() as flow:
            self._write(flow, "before", [
                {"task_id": "t1", "passed": True, "route": ["a"], "chain": ["a"]},
                {"task_id": "t2", "passed": False, "route": ["b"], "chain": ["b"]},
            ])
            self._write(flow, "after", [
                {"task_id": "t1", "passed": False, "route": ["a"], "chain": ["a"]},
                {"task_id": "t2", "passed": True, "route": ["b"], "chain": ["b"]},
            ])
            result = compare(flow, "before", "after")
        self.assertEqual(len(result["flips"]), 2)
        self.assertTrue(result["holds"])

    def test_any_rep_marks_the_task_passed(self):
        before = [{"task_id": "t1", "passed": False, "route": ["a"]}]
        after = [
            {"task_id": "t1", "passed": False, "route": ["a"]},
            {"task_id": "t1", "passed": True, "route": ["a"]},
        ]
        rolled = per_task(before, after)
        self.assertTrue(rolled[0]["after_pass"])
        self.assertFalse(rolled[0]["before_pass"])
        self.assertAlmostEqual(summarize(after)["pass_rate"], 0.5)

    def test_chain_coverage_ignores_order(self):
        with self._flow() as flow:
            self._write(flow, "baseline", [
                {"task_id": "t1", "passed": True, "chain": ["publish", "lookup"], "route": []},
            ])
            traces = Path(flow) / "traces.jsonl"
            traces.write_text(
                json.dumps({"chain": ["lookup", "publish"]}) + "\n", encoding="utf-8")
            result = check(flow, "baseline", traces)
        self.assertTrue(result["ok"])
        self.assertEqual(result["missing"], [])

    def test_sample_flow_is_a_four_row_planted_fixture(self):
        flow, tasks = make_sample_flow()
        self.addCleanup(shutil.rmtree, flow, True)
        self.assertEqual([t["task_id"] for t in tasks], ["t01", "t02", "t03", "t04"])
        from evalkit.compare import load_variant
        before = {r["task_id"]: r for r in load_variant(flow, "baseline")}
        after = {r["task_id"]: r for r in load_variant(flow, "after")}
        self.assertTrue(before["t02"]["passed"])
        self.assertFalse(after["t02"]["passed"])
        for row in list(before.values()) + list(after.values()):
            self.assertNotIn("model", row)

    def _flow(self):
        return tempfile.TemporaryDirectory(prefix="evalkit-hole-")

    def _write(self, flow, variant, rows):
        directory = Path(flow) / variant
        directory.mkdir(parents=True)
        text = "".join(json.dumps(r) + "\n" for r in rows)
        (directory / "results.jsonl").write_text(text, encoding="utf-8")

class TestOfflineCommands(unittest.TestCase):
    def _env(self):
        env = {k: v for k, v in os.environ.items()
               if k not in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ALLOW_WRITES"}}
        env["PYTHONPATH"] = str(ROOT)
        return env

    def test_agent_loop_demo_answers_without_a_key(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "agent_loop.py")],
            cwd=ROOT, env=self._env(), capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("stop      = answered", proc.stdout)

    def test_real_model_demo_exits_2_without_a_key(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "examples" / "real_model_demo.py")],
            cwd=ROOT, env=self._env(), capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn("stop       =", proc.stdout)
        self.assertFalse((ROOT / "flow").exists())

    def test_redteam_example_measures_the_gate_on_the_mock(self):
        spec = importlib.util.spec_from_file_location(
            "agent_redteam_integration",
            ROOT / "examples" / "agent_redteam_integration.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        harness, source = module._load_harness()
        self.assertEqual(source, "mock")
        self.assertIsInstance(harness, module._MockRedTeamHarness)
        with self._flow_for_redteam() as flow:
            tasks = [
                {"task_id": "t01", "prompt": "a"},
                {"task_id": "t02", "prompt": "b"},
            ]
            opened = module.RedTeamRunner(flow, tasks, allow_attacks=True)
            opened.run_variant("baseline", reps=1)
            module.RedTeamRunner(flow, tasks, allow_attacks=False).run_variant("locked", reps=1)
            result = opened.compare("baseline", "locked")
            names = {p.name for p in Path(flow).iterdir()}
        self.assertEqual(result["summary_before"]["pass_rate"], 1.0)
        self.assertEqual(result["summary_after"]["pass_rate"], 0.0)
        self.assertFalse(result["holds"])
        self.assertNotIn("fold", names)

    def test_redteam_script_labels_traces_as_not_production(self):
        with tempfile.TemporaryDirectory(prefix="redteam-script-") as flow:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "examples" / "agent_redteam_integration.py"),
                 "--flow-dir", flow],
                cwd=ROOT, env=self._env(), capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("不是折叠子 agent 的测量", proc.stdout)
        self.assertIn("不是生产导出", proc.stdout)

    def _flow_for_redteam(self):
        return tempfile.TemporaryDirectory(prefix="redteam-hole-")


if __name__ == "__main__":
    unittest.main()
