import csv
import random
import tempfile
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import generate, make_unsat, valid, groups, lit, clue_literals, configurations, summarize


class Tests(unittest.TestCase):
    def test_generation(self):
        for n in (9, 16, 25):
            for seed in range(5):
                p, s = generate(n, random.Random(seed), .70)
                self.assertTrue(valid(s, p))
                self.assertEqual(len(p), n*n)
                self.assertEqual(sum(x != 0 for x in p), round(.70*n*n))

    def test_groups_and_lit(self):
        for n in (4, 9, 16, 25):
            gg = list(groups(n))
            self.assertEqual(len(gg), 4*n*n)
            self.assertEqual(len({k for row in gg for k in row}), n**3)
            self.assertTrue(all(len(row) == n for row in gg))
            self.assertEqual(len(clue_literals([1] + [0]*(n*n-1), n)), 1)
            self.assertEqual(lit(n, 0, 0, 1), 1)

    def test_unsat_control(self):
        p, s = generate(16, random.Random(7), .72)
        unsat = make_unsat({"id": "test", "size": 16, "puzzle": p})
        self.assertNotEqual(p, unsat["puzzle"])
        self.assertFalse(valid(s, unsat["puzzle"]))
        self.assertEqual(unsat["source"], "contradictory_control")

    def test_summary(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "results.csv"
            rows = [{"size":"9", "method":"sat", "solver":"glucose4", "encoding":"seqcounter",
                     "status": s, "runtime_s": t, "variables":"100", "clauses":"200", "constraints":""}
                    for s, t in [("SAT", "1"), ("UNSAT", "3"), ("TIMEOUT", "30")]]
            with path.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0])
                writer.writeheader()
                writer.writerows(rows)
            summary_path = summarize(path)
            with summary_path.open() as sf:
                summary = list(csv.DictReader(sf))[0]
            self.assertEqual(summary["mean_runtime_s"], "2.0")
            self.assertFalse(any(name.startswith("median_") for name in summary))
            self.assertEqual(summary["timeouts"], "1")
            self.assertEqual(summary["instances"], "3")

    def test_benchmark_configs(self):
        c = configurations("standard")
        self.assertEqual(len(c), 7)
        self.assertEqual({x["method"] for x in c},
                         {"sat", "maxsat", "multiple", "incremental", "cpsat"})


if __name__ == "__main__":
    unittest.main()
