"""Integration checks activate when optional solver packages are installed."""
import importlib.util
import random
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import build_cnf, generate, run_sat, valid


@unittest.skipUnless(importlib.util.find_spec("pysat"), "Install python-sat")
class SATIntegration(unittest.TestCase):
    def setUp(self):
        self.puzzle, self.solution = generate(4, random.Random(23), 1.0)

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

    def test_multiple_unique_and_ambiguous(self):
        cnf = build_cnf(4)
        cfg = {"method": "multiple", "solver": "cadical153", "encoding": "seqcounter"}
        one, _ = run_sat(self.puzzle, 4, cfg, cnf)
        self.assertEqual((one["solutions_found"], one["unique"]), (1, True))
        many, _ = run_sat([0]*16, 4, cfg, cnf)
        self.assertEqual((many["solutions_found"], many["unique"]), (2, False))

    def test_maxsat_clue_repair(self):
        cnf = build_cnf(4)
        cfg = {"method": "maxsat", "solver": "cadical153", "encoding": "seqcounter"}
        bad = self.puzzle.copy()
        bad[1] = bad[0]
        result, grid = run_sat(bad, 4, cfg, cnf)
        self.assertEqual(result["status"], "UNSAT")
        self.assertEqual(result["maxsat_cost"], 1)
        self.assertTrue(valid(grid))

    def test_incremental_solver_reuse(self):
        from pysat.solvers import Solver
        cnf = build_cnf(4)
        cfg = {"method": "incremental", "solver": "cadical153", "encoding": "seqcounter"}
        other, _ = generate(4, random.Random(25), 1.0)
        with Solver(name="cadical153", bootstrap_with=cnf.clauses) as solver:
            for puzzle in (self.puzzle, other, self.puzzle):
                result, grid = run_sat(puzzle, 4, cfg, cnf, solver)
                self.assertEqual(result["status"], "SAT")
                self.assertTrue(valid(grid, puzzle))
                self.assertEqual(result["assumptions"], 16)
                self.assertEqual(result["clauses"], len(cnf.clauses))


if __name__ == "__main__":
    unittest.main()
