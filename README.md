# Sudoku Assignment 1 — SAT encoding and solver benchmark

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

## Prepare 1,000 SAT + 100 UNSAT Sudoku 9×9 instances

```bash
python main.py prepare --archive "archive (1).zip" --out data_1100 --n9 1000 --n-unsat 100 --seed 42
```

The defaults sample 1,000 SAT puzzles from Kaggle, copy 100 distinct samples,
insert an explicit same-row clue contradiction, and reproducibly shuffle all
1,100 instances using `--seed`. UNSAT controls have an `_unsat` ID suffix,
`source=contradictory_control`, and a `base_instance` field.

Generate fewer instances for a smoke test:

```bash
python main.py prepare --archive "archive (1).zip" --out quick_data --n9 10 --n-unsat 2 --pool 100 --seed 42
```

SAT instances store the Kaggle reference solution, which is never passed to a
solver. Each UNSAT control is independently certified by two equal fixed clues
in one row and therefore does not need a reference solution.

## Benchmark

```bash
python main.py benchmark --data data_1100 --out results/run_1100_9x9 --timeout 30
```

This single command runs the full benchmark: the 2 solver × 3 encoding SAT
matrix, MaxSAT, Multiple SAT, Incremental SAT, Gurobi MIP and CPLEX MIP. With
1,100 puzzles it writes 12,100 result rows.

On Windows, the project solver environment can be called directly without
activating it:

```powershell
.\.venv_solvers\Scripts\python.exe main.py benchmark --data data_1100 --out results/run_1100_9x9 --timeout 30
```

### Run configurations separately

Use `--config` and **distinct `--out` directories** to prevent `results.csv` overwrites:

```bash
python main.py benchmark --data data_1100 --config sat-cadical-seq    --out results/sat-cadical-seq    --timeout 30
python main.py benchmark --data data_1100 --config sat-cadical-pair   --out results/sat-cadical-pair   --timeout 30
python main.py benchmark --data data_1100 --config sat-cadical-card   --out results/sat-cadical-card   --timeout 30
python main.py benchmark --data data_1100 --config sat-glucose-seq    --out results/sat-glucose-seq    --timeout 30
python main.py benchmark --data data_1100 --config sat-glucose-pair   --out results/sat-glucose-pair   --timeout 30
python main.py benchmark --data data_1100 --config sat-glucose-card   --out results/sat-glucose-card   --timeout 30
python main.py benchmark --data data_1100 --config maxsat           --out results/maxsat           --timeout 30
python main.py benchmark --data data_1100 --config multiple         --out results/multiple         --timeout 30
python main.py benchmark --data data_1100 --config incremental      --out results/incremental      --timeout 30
python main.py benchmark --data data_1100 --config gurobi           --out results/gurobi           --timeout 30
python main.py benchmark --data data_1100 --config cplex-mip        --out results/cplex-mip        --timeout 30
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
- `data_1100/instances.jsonl`: 1,000 SAT puzzles plus 100 contradictory UNSAT controls.

With 1,100 instances and 11 configurations there are **12,100 instance × configuration rows**.

**Measurement conventions (important for the report):**

- `runtime_s`: elapsed time for solving an individual instance, including fresh solver construction for Multiple SAT and MaxSAT and model construction for MIP. It excludes CNF generation and initial Incremental SAT solver initialization, which are recorded as `setup_s`.
- `variables`/`clauses`: input CNF size. Pairwise introduces no auxiliary
  variables, while sequential counter and cardinality network do. Multiple SAT
  adds one blocking clause, Incremental SAT records givens as assumptions, and
  MaxSAT records givens as weighted soft clauses.
- Gurobi/CPLEX MIP record binary variables and linear constraints and leave `clauses` blank because their internal representation is not directly comparable to CNF.
- `MaxSAT`: Sudoku logic constraints are **hard**, each given is a unit soft clause of weight 1. `maxsat_cost` = minimum number of clues that must be changed. To make the `status` **comparable to the original puzzle feasibility**, it is `SAT` if optimal cost is 0, and `UNSAT` if optimal cost >0; even for `UNSAT` it can return a **repaired** valid Sudoku grid. This is a **different optimization objective**, not an ordinary SAT decision test.
- `Multiple SAT`: solves and then blocks the *whole Sudoku grid* to check for a second distinct solution (max 2), ignoring auxiliary variables. `unique=true` only when a second solve is UNSAT. Its runtime is not directly a one-solution SAT latency.
- `Incremental SAT`: **reuses one initialized solver per grid size and encoding**, and changes fixed clues via `solve(assumptions=...)`. It must never add clues permanently. Since the initial solver setup is excluded from per-instance latency, compare `runtime_s` and `setup_s` explicitly.
- `TIMEOUT` is a wall-time limit enforced by a worker process. Solver-native time limits are also set for Gurobi and CPLEX MIP.
- All feasible returned solutions are checked against Sudoku constraints, and (except MaxSAT repair) against original givens.

**Caution:** sampling only the first `--pool` valid Kaggle records is reproducible but is not guaranteed representative of the full dataset. For rigorous runtime statistics, repeat experiments with additional seeds and control CPU/thread count.

## Test

```bash
python -m unittest discover -s tests -v
```

Solver dependencies and commercial licenses must be installed before running
all 11 configurations. The SAT-only configurations require only `python-sat`.
