import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from evalkit.compare import compare, load_variant
from evalkit.chain_coverage import check
from evalkit.coupling_map import coupling
from evalkit.estimate import estimate, with_actual
from evalkit.runner import BaseEvalRunner, EvalResult
from tests.sample_data import make_sample_flow


class TestEval(unittest.TestCase):
    def setUp(self):
        self.d, self.tasks = make_sample_flow()

    def test_compare_detects_flip_and_route(self):
        r = compare(self.d, "baseline", "after")
        self.assertFalse(r["holds"])
        self.assertTrue(any(t["task_id"] == "t02" and t["flipped"] for t in r["flips"]))
        self.assertTrue(any(t["task_id"] == "t02" and t["route_changed"] for t in r["routes"]))

    def test_coverage_fails_on_missing_chain(self):
        r = check(self.d, "baseline", str(self.d / "traces.jsonl"))
        self.assertFalse(r["ok"])
        self.assertTrue(any("billing" in m for m in r["missing"]))

    def test_coupling(self):
        c = coupling(str(self.d / "traces.jsonl"))
        self.assertEqual(c["runs"], 5)
        self.assertIn("router", c["per_agent"])

    def test_estimate(self):
        e = estimate(self.d, "baseline", 2)
        self.assertGreater(e["estimated_tokens"], 0)
        a = with_actual(self.d, "baseline", "after", 2)
        self.assertEqual(
            a["actual_tokens"],
            sum(r["input_tokens"] + r["output_tokens"] for r in load_variant(self.d, "after")))


class StubRunner(BaseEvalRunner):
    def run_one(self, task):
        return EvalResult(task_id=task["task_id"], passed=True, chain=task.get("chain", []))


class TestRunner(unittest.TestCase):
    def test_run_variant_writes(self):
        d, tasks = make_sample_flow()
        r = StubRunner(d / "runner_test", tasks)
        rows = r.run_variant("v1", reps=1)
        self.assertEqual(len(rows), len(tasks))
        self.assertTrue((d / "runner_test" / "v1" / "results.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
