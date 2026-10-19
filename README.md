# Sudoku  — SAT encoding and solver benchmark

Python 3.10+ recommended (Windows/Linux/macOS). Reads the **original ZIP in place**; no CSV extraction required. The dataset ZIP is **not embedded** in the source package.

## Setup

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

`python-sat` provides both CaDiCaL and Glucose. Install `gurobipy` and
`docplex` + `cplex` with working Gurobi and IBM CPLEX MIP licenses to run the
complete 11-configuration benchmark.

## Prepare the soft-preference dataset

```bash
python main.py prepare --archive "archive (1).zip" --out data_preference_1100 --n9 1000 --n-unsat 100 --givens 16 --seed 42
```

The defaults sample 1,000 Kaggle puzzles and retain 16 clues in each so that
multiple completions remain available for the soft preference to distinguish.
They also add 100 contradictory UNSAT controls and reproducibly shuffle all
1,100 instances. The original `data_1100` dataset is left unchanged.

Generate fewer instances for a smoke test:

```bash
python main.py prepare --archive "archive (1).zip" --out quick_data --n9 10 --n-unsat 2 --givens 16 --pool 100 --seed 42
```

SAT instances store the Kaggle reference solution, which is never passed to a
solver. Each UNSAT control is independently certified by two equal fixed clues
in one row and therefore does not need a reference solution.

## Benchmark

```bash
python main.py benchmark --data data_preference_1100 --out results/preference_1100_optimized --timeout 30
```

This single command runs the full benchmark: the 2 solver × 3 encoding SAT
matrix, MaxSAT, Multiple SAT, Incremental SAT, Gurobi MIP and CPLEX MIP. With
1,100 puzzles it writes 12,100 result rows.

On Windows, the project solver environment can be called directly without
activating it:

```powershell
.\.venv_solvers\Scripts\python.exe main.py benchmark --data data_preference_1100 --out results/preference_1100_optimized --timeout 30
```

### Run configurations separately

Use `--config` and **distinct `--out` directories** to prevent `results.csv` overwrites:

```bash
python main.py benchmark --data data_preference_1100 --config maxsat      --out results/maxsat      --timeout 30
python main.py benchmark --data data_preference_1100 --config multiple    --out results/multiple_optimized --timeout 30
python main.py benchmark --data data_preference_1100 --config incremental --out results/incremental_optimized --timeout 30
```

Gurobi and CPLEX MIP require their respective Python packages and licenses.

The small `data` directory can be used for a smoke run before the complete
1,100-instance benchmark.

The benchmark runs these **11 configurations** on every instance. The six
ordinary SAT configurations form a balanced matrix, so solver comparisons can
hold the encoding fixed and encoding comparisons can hold the solver fixed:

| Method | Solver | Exactly-one encoding |
|---|---|---|
| MaxSAT | RC2 with CaDiCaL | sequential counter |
| Multiple SAT | CaDiCaL | sequential counter |
| Incremental SAT | CaDiCaL | sequential counter |
| SAT | CaDiCaL | sequential counter |
| SAT | CaDiCaL | pairwise |
| SAT | CaDiCaL | cardinality network |
| SAT | Glucose 4 | sequential counter |
| SAT | Glucose 4 | pairwise |
| SAT | Glucose 4 | cardinality network |
| Gurobi MIP | Gurobi | binary MIP |
| CPLEX MIP | IBM CPLEX | binary MIP |

## Output schema

- `results/results.csv`: one row per **instance × configuration**, including
  `status`, `expected_status`, `correct_status`, timing, model-size fields and
  validation details.
- `results/summary.csv`: grouped by size, method, solver and encoding, including
  SAT/UNSAT/timeout/error counts, `correct`, `labeled`, `accuracy`, and mean
  completion time. Timed-out runs are excluded from the mean but count as an
  incorrect classification when an expected label is available.
- `results/solutions.jsonl`: decoded Sudoku grids when a feasible solution is returned, for verification or display.
- `data_preference_1100/instances.jsonl`: 1,000 reduced-clue SAT puzzles plus 100 contradictory UNSAT controls.

With 1,100 instances and 11 configurations there are **12,100 instance × configuration rows**.

**Measurement conventions (important for the report):**

- `runtime_s`: elapsed time for solving an individual instance, including solver construction and every optimization call for MaxSAT, Multiple SAT and Incremental SAT. CNF generation is recorded separately as `setup_s`.
- `variables`/`clauses`: input CNF size. Pairwise introduces no auxiliary
  variables, while sequential counter and cardinality network do. Multiple SAT
  and Incremental SAT add a weighted totalizer objective, while
  MaxSAT records givens as hard clauses and preferred placements as weighted
  soft clauses.
- Gurobi/CPLEX MIP record binary variables and linear constraints and leave `clauses` blank because their internal representation is not directly comparable to CNF.
- `MaxSAT`: Sudoku rules and givens are hard. In each 3×3 box, unfilled cells
  are ordered `11, 10, 12, 02, 20, 01, 21, 00, 22`; missing digits are paired
  as `9, 8, ..., 1`. Each desired pair is a soft unit clause weighted by its
  digit. `maxsat_cost` is the total weight of unsatisfied preferences;
  `preference_score` is the achieved weight for every SAT method; and
  `preference_matches` is the unweighted number of preferred pairs achieved.
- `Multiple SAT`: repeatedly calls CaDiCaL while binary-searching a weighted
  totalizer bound. A SAT call supplies a better feasible preference score; an
  UNSAT call lowers the bound. It stops only when the optimum is proved, writes
  `optimal=true`, and records the number of improving models in
  `solutions_found`. This is an exact iterative SAT optimization, not a
  two-solution uniqueness check and not unrestricted model enumeration.
- `Incremental SAT`: for each instance, repeatedly solves with the same CaDiCaL object. After a feasible solution of score `S`, it permanently adds the totalizer constraint `preference_score >= S + 1`. The last feasible model is optimal when the next bound is UNSAT (or a valid upper bound is reached). It does not share mutable solver state between different instances.
- `TIMEOUT` is a wall-time limit enforced by a worker process. Solver-native time limits are also set for Gurobi and CPLEX MIP.
- All feasible returned solutions, including MaxSAT solutions, are checked
  against Sudoku constraints and original givens.

**Caution:** sampling only the first `--pool` valid Kaggle records is reproducible but is not guaranteed representative of the full dataset. For rigorous runtime statistics, repeat experiments with additional seeds and control CPU/thread count.

## Test

```bash
python -m unittest discover -s tests -v
```

Solver dependencies and commercial licenses must be installed before running
all 11 configurations. The SAT-only configurations require only `python-sat`.
