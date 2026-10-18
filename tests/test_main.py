import csv
import random
import tempfile
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import (generate, make_unsat, valid, groups, lit, clue_literals,
                  configurations, summarize)


class Tests(unittest.TestCase):
    def test_generation(self):
        for n in (9,):
            for seed in range(5):
                p, s = generate(n, random.Random(seed), .70)
                self.assertTrue(valid(s, p))
                self.assertEqual(len(p), n*n)
                self.assertEqual(sum(x != 0 for x in p), round(.70*n*n))

    def test_groups_and_lit(self):
        for n in (4, 9):
            gg = list(groups(n))
            self.assertEqual(len(gg), 4*n*n)
            self.assertEqual(len({k for row in gg for k in row}), n**3)
            self.assertTrue(all(len(row) == n for row in gg))
            self.assertEqual(len(clue_literals([1] + [0]*(n*n-1), n)), 1)
            self.assertEqual(lit(n, 0, 0, 1), 1)

    def test_unsat_control(self):
        puzzle, solution = generate(9, random.Random(7), .70)
        item = {"id": "s9_test", "size": 9, "puzzle": puzzle,
                "reference_solution": solution, "source": "kaggle"}
        control = make_unsat(item)
        self.assertEqual(control["id"], "s9_test_unsat")
        self.assertEqual(control["source"], "contradictory_control")
        self.assertEqual(control["base_instance"], "s9_test")
        self.assertEqual(control["expected_status"], "UNSAT")
        self.assertFalse(valid(solution, control["puzzle"]))

    def test_summary(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "results.csv"
            rows = [{"size":"9", "method":"maxsat", "solver":"cadical153", "encoding":"seqcounter",
                     "status": s, "expected_status": expected, "runtime_s": t,
                     "variables":"100", "clauses":"200", "constraints":""}
                    for s, expected, t in [("SAT", "SAT", "1"),
                                            ("UNSAT", "UNSAT", "3"),
                                            ("TIMEOUT", "SAT", "30")]]
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
            self.assertEqual(summary["correct"], "2")
            self.assertEqual(summary["labeled"], "3")
            self.assertEqual(summary["accuracy"], "0.666667")

    def test_benchmark_configs(self):
        c = configurations()
        self.assertEqual(len(c), 11)
        self.assertEqual({x["method"] for x in c},
                         {"sat", "maxsat", "multiple", "incremental", "gurobi", "cplex_mip"})
        sat = [x for x in c if x["method"] == "sat"]
        self.assertEqual({x["solver"] for x in sat}, {"cadical153", "glucose4"})
        self.assertEqual({x["encoding"] for x in sat},
                         {"seqcounter", "pairwise", "cardinality"})
        self.assertEqual(len({(x["solver"], x["encoding"]) for x in sat}), 6)


if __name__ == "__main__":
    unittest.main()
