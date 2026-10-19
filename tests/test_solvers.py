"""Integration checks activate when optional solver packages are installed."""
import importlib.util
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import build_cnf, preference_clauses, run_sat, valid


@unittest.skipUnless(importlib.util.find_spec("pysat"), "Install python-sat")
class SATIntegration(unittest.TestCase):
    def setUp(self):
        self.solution = [1, 2, 3, 4, 3, 4, 1, 2, 2, 1, 4, 3, 4, 3, 2, 1]
        self.puzzle = self.solution.copy()

    def test_sat_solver_encoding_matrix_and_unsat(self):
        for solver in ("cadical153", "glucose4"):
            for encoding in ("seqcounter", "pairwise", "cardinality"):
                with self.subTest(solver=solver, encoding=encoding):
                    cnf = build_cnf(4, encoding)
                    cfg = {"method": "sat", "solver": solver, "encoding": encoding}
                    yes, grid = run_sat(self.puzzle, 4, cfg, cnf)
                    self.assertEqual(yes["status"], "SAT")
                    self.assertTrue(valid(grid, self.puzzle))
                    bad = self.puzzle.copy()
                    bad[1] = bad[0]
                    no, grid = run_sat(bad, 4, cfg, cnf)
                    self.assertEqual(no["status"], "UNSAT")
                    self.assertIsNone(grid)

    def test_encoding_sizes_differ(self):
        sizes = {}
        for encoding in ("seqcounter", "pairwise", "cardinality"):
            cnf = build_cnf(9, encoding)
            sizes[encoding] = cnf.nv, len(cnf.clauses)
        self.assertEqual(len(set(sizes.values())), 3)

    def test_multiple_finds_optimal_preference(self):
        cnf = build_cnf(4)
        multiple = {"method": "multiple", "solver": "cadical153", "encoding": "seqcounter"}
        maxsat = {"method": "maxsat", "solver": "cadical153", "encoding": "seqcounter"}
        result, grid = run_sat([0]*16, 4, multiple, cnf)
        optimum, _ = run_sat([0]*16, 4, maxsat, cnf)
        self.assertEqual(result["status"], "SAT")
        self.assertTrue(result["optimal"])
        self.assertTrue(valid(grid))
        self.assertEqual(result["preference_score"], optimum["preference_score"])
        self.assertGreaterEqual(result["solutions_found"], 1)

    def test_maxsat_rejects_inconsistent_clues(self):
        cnf = build_cnf(4)
        cfg = {"method": "maxsat", "solver": "cadical153", "encoding": "seqcounter"}
        bad = self.puzzle.copy()
        bad[1] = bad[0]
        result, grid = run_sat(bad, 4, cfg, cnf)
        self.assertEqual(result["status"], "UNSAT")
        self.assertIsNone(grid)

    def test_maxsat_soft_preferences(self):
        cnf = build_cnf(4)
        cfg = {"method": "maxsat", "solver": "cadical153", "encoding": "seqcounter"}
        result, grid = run_sat([0]*16, 4, cfg, cnf)
        self.assertEqual(result["status"], "SAT")
        self.assertTrue(valid(grid))
        self.assertEqual(result["soft_clauses"], len(preference_clauses([0]*16, 4)))
        self.assertGreater(result["preference_matches"], 0)
        self.assertGreater(result["preference_score"], 0)

    def test_incremental_finds_optimal_preference(self):
        cnf = build_cnf(4)
        incremental = {"method": "incremental", "solver": "cadical153", "encoding": "seqcounter"}
        maxsat = {"method": "maxsat", "solver": "cadical153", "encoding": "seqcounter"}
        result, grid = run_sat([0]*16, 4, incremental, cnf)
        optimum, _ = run_sat([0]*16, 4, maxsat, cnf)
        self.assertEqual(result["status"], "SAT")
        self.assertTrue(result["optimal"])
        self.assertTrue(valid(grid))
        self.assertEqual(result["preference_score"], optimum["preference_score"])
        self.assertGreaterEqual(result["solutions_found"], 1)
        self.assertGreater(result["clauses"], len(cnf.clauses))


if __name__ == "__main__":
    unittest.main()
