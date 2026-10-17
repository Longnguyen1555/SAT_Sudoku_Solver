# Sudoku Assignment 1 — SAT, MaxSAT, Multiple SAT, Incremental SAT

Python 3.10+ recommended (Windows/Linux/macOS). Reads the **original ZIP in place**; no CSV extraction required. The dataset ZIP is **not embedded** in the source package.

## Setup

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

For optional commercial comparisons install `gurobipy` (Gurobi license), `docplex` + `cplex` (IBM CPLEX MIP license) and/or `docplex` + IBM CP Optimizer engine (CP license). They are **not** needed for the default experiment.

## Prepare data (100,000 archived 9×9 instances plus generated large grids)

```bash
python main.py prepare --archive "archive (1).zip" --out data_100000 --seed 42
```

The defaults sample 100,000 Kaggle 9×9 instances from the full one-million-row archive and generate 1,000 instances for each of 16×16 and 25×25. In each generated size, `--unsat-rate 0.30` replaces 300 instances with deliberate row-clue contradictions, leaving 700 SAT and 300 UNSAT instances. All 102,000 records are shuffled reproducibly using `--seed`.

Generate fewer instances for a smoke test:

```bash
python main.py prepare --archive "archive (1).zip" --out quick_data --n9 10 --n16 10 --n25 10 --pool 100 --unsat-rate 0.30 --seed 42
```

`--keep16 0.60` and `--keep25 0.72` control the fraction of **givens** in generated large instances. Large grids are produced by random row/band/column/stack/digit permutations of a valid base Sudoku and random removal of digits. **Solvable is guaranteed, uniqueness and difficulty are not**. The reference solution is stored for transparency but never passed to the solvers.

## Benchmark

```bash
python main.py benchmark --data data_100000 --out results/sat-all --timeout 30
```

For lighter workloads: `--profile quick` omits **only** the pairwise-Cadical SAT configuration. Optional comparison flags: `--gurobi`, `--cplex-mip`, `--cplex-cp`. Each requires the matching commercial engine and license.

### Run configurations separately

Use `--config` and **distinct `--out` directories** to prevent `results.csv` overwrites:

```bash
python main.py benchmark --data data_100000 --config sat-cadical-seq  --out results/sat-cadical-seq  --timeout 30
python main.py benchmark --data data_100000 --config sat-glucose-seq  --out results/sat-glucose-seq  --timeout 30
python main.py benchmark --data data_100000 --config sat-cadical-pair --out results/sat-cadical-pair --timeout 30
python main.py benchmark --data data_100000 --config maxsat           --out results/maxsat           --timeout 30
python main.py benchmark --data data_100000 --config multiple         --out results/multiple         --timeout 30
python main.py benchmark --data data_100000 --config incremental      --out results/incremental      --timeout 30
python main.py benchmark --data data_100000 --config cpsat            --out results/cpsat            --timeout 30
python main.py benchmark --data data_100000 --config gurobi           --out results/gurobi           --timeout 30
python main.py benchmark --data data_100000 --config cplex-mip        --out results/cplex-mip        --timeout 30
python main.py benchmark --data data_100000 --config cplex-cp         --out results/cplex-cp         --timeout 30
```

Optional, requiring respective commercial solver setup: `--config gurobi`, `--config cplex-mip`, `--config cplex-cp`. For a quick check add `--size 9`; only one size is processed then. Omitting both filters runs the entire selected profile.

You can still use the bundled 32-puzzle smoke dataset with `--data sample_data`.

The standard profile runs these **seven configurations** on every instance:

| Method | Solver | Exactly-one encoding |
|---|---|---|
| SAT | CaDiCaL 1.5.3 (PySAT name `cadical153`) | sequential counter |
| SAT | Glucose 4 (`glucose4`) | sequential counter |
| SAT | CaDiCaL (`cadical153`) | pairwise |
| MaxSAT | RC2 with CaDiCaL | sequential counter |
| Multiple SAT | CaDiCaL | sequential counter |
| Incremental SAT | CaDiCaL | sequential counter |
| CP-SAT | OR-Tools | native AllDifferent constraints |

Optional comparisons: **Gurobi MIP**, **CPLEX MIP**, **CPLEX CP Optimizer**. They are implemented but excluded from the selected default profile to keep experiments and license requirements manageable. Run them selectively if licensed.

## Output schema

- `results/results.csv`: one row per **instance × configuration**, including `status` (`SAT`, `UNSAT`, `TIMEOUT`, `ERROR`), `runtime_s`, `setup_s`, `variables`, `clauses`, `constraints`, `solutions_found`, `unique`, `maxsat_cost`, `valid_solution`, `assumptions`, `soft_clauses`, and `error`.
- `results/summary.csv`: grouped by size, method, solver and encoding, including counts of SAT/UNSAT/timeouts/errors and mean completion time. Timed-out runs are excluded from the mean (the timeout count is shown separately).
- `results/solutions.jsonl`: decoded Sudoku grids when a feasible solution is returned, for verification or display.
- `data/instances.jsonl`: input puzzles plus reference solutions when available.

With 102,000 instances and seven standard configurations there are **714,000 instance × configuration rows**.

**Measurement conventions (important for the report):**

- `runtime_s`: elapsed time for solving an individual instance, including fresh SAT solver construction for normal SAT, Multiple SAT and MaxSAT; including building the CP-SAT/MIP model. Python overhead is included. It **excludes** SAT CNF base generation and initial incremental solver initialization, which are measured separately as `setup_s` (one preparation for all same-size instances of a configuration, repeated per row as descriptive metadata; **do not sum `setup_s` rows**).
- `variables`/`clauses`: **input** CNF size. CNF uses `n³` primary literals and potentially auxiliary sequential-counter variables. Each Sudoku rule is exactly-one: 4n² groups. Standard SAT row includes unit clauses for givens; Multiple SAT additionally includes one blocking clause when it searches for a second solution. Incremental SAT retains **only base clauses** and records givens as `assumptions`. MaxSAT clause count includes weighted `soft_clauses` as well as Sudoku hard clauses. RC2's internal relaxation variables/clauses are **not** included.
- `CP-SAT`: `variables` counts declared **integer** decision variables; `constraints` counts model constraints; `clauses` is blank because native CP-SAT model clauses are not comparable to user-supplied CNF clauses. Gurobi/CPLEX MIP similarly record binary variables and linear constraints; CPLEX CP uses integer variables and native AllDifferent constraints. They leave `clauses` blank. The internal translated solver representation can be much larger.
- `SAT`: standard feasibility test for **all** givens.
- `MaxSAT`: Sudoku logic constraints are **hard**, each given is a unit soft clause of weight 1. `maxsat_cost` = minimum number of clues that must be changed. To make the `status` **comparable to the original puzzle feasibility**, it is `SAT` if optimal cost is 0, and `UNSAT` if optimal cost >0; even for `UNSAT` it can return a **repaired** valid Sudoku grid. This is a **different optimization objective**, not an ordinary SAT decision test.
- `Multiple SAT`: solves and then blocks the *whole Sudoku grid* to check for a second distinct solution (max 2), ignoring auxiliary variables. `unique=true` only when a second solve is UNSAT. Its runtime is not directly a one-solution SAT latency.
- `Incremental SAT`: **reuses one initialized solver per grid size and encoding**, and changes fixed clues via `solve(assumptions=...)`. It must never add clues permanently. Since the initial solver setup is excluded from per-instance latency, compare `runtime_s` and `setup_s` explicitly.
- `TIMEOUT` is a wall-time limit enforced by a worker process. A restart after a timeout may repeat one setup; users should treat this as an additional experiment cost. Solver-native time limits are also set for CP-SAT and Gurobi.
- All feasible returned solutions are checked against Sudoku constraints, and (except MaxSAT repair) against original givens.

**Cautions:** all 16×16/25×25 instances are constructed from a known solution; they are not necessarily representative of hard human-crafted puzzles. Sampling only the first `--pool` valid Kaggle records is reproducible but is not guaranteed representative of the full dataset. For rigorous runtime statistics, repeat experiments with additional seeds, control CPU/thread count and report medians/IQR.

## Test

```bash
python -m unittest discover -s tests -v
```

Tested pure-Python dataset/generator checks independently; solver dependencies must be installed before full SAT/CP-SAT/Gurobi execution.
