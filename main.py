import argparse
import csv
import io
import json
import math
import multiprocessing as mp
import queue
import random
import time
import zipfile
from collections import Counter
from itertools import combinations
from pathlib import Path


FIELDS = ["instance", "size", "givens", "method", "solver", "encoding", "status",
          "runtime_s", "setup_s", "variables", "clauses", "constraints", "var_type",
          "solutions_found", "unique", "maxsat_cost", "valid_solution", "assumptions", "soft_clauses", "error"]
SAT_METHODS = {"sat", "maxsat", "multiple", "incremental"}


def regions(n):
    """Row, column and box coordinates shared by every solver."""
    b = math.isqrt(n)
    for r in range(n):
        yield [(r, c) for c in range(n)]
    for c in range(n):
        yield [(r, c) for r in range(n)]
    for br in range(0, n, b):
        for bc in range(0, n, b):
            yield [(r, c) for r in range(br, br+b) for c in range(bc, bc+b)]


def valid(grid, check_clues=None):
    """Check a complete n x n Sudoku; optionally check all fixed clues."""
    n = math.isqrt(len(grid))
    b = math.isqrt(n)
    if n * n != len(grid) or b * b != n:
        return False
    target = set(range(1, n + 1))
    if check_clues and any(v and v != grid[i] for i, v in enumerate(check_clues)):
        return False
    return all({grid[r*n+c] for r, c in cells} == target for cells in regions(n))


def generate(n, rng, keep):
    """Permuted cyclic Latin Sudoku: guaranteed feasible; NOT guaranteed unique."""
    b = math.isqrt(n)
    if b * b != n or not 0 < keep <= 1:
        raise ValueError("size must be a perfect square and keep in (0,1]")

    def order():
        bands = list(range(b))
        rng.shuffle(bands)
        return [band*b + offset for band in bands
                for offset in rng.sample(range(b), b)]

    rows, cols, digits = order(), order(), rng.sample(range(1, n+1), n)
    solution = [digits[(b*(r % b) + r//b + c) % n]
                for r in rows for c in cols]
    puzzle = solution.copy()
    for i in rng.sample(range(n*n), n*n - round(keep*n*n)):
        puzzle[i] = 0
    return puzzle, solution


def read_kaggle(archive, sample_size, pool, seed):
    """Reservoir sampling from at most `pool` CSV records; no 164 MB extraction."""
    rng, selected = random.Random(seed), []
    with zipfile.ZipFile(archive) as zf:
        with zf.open("sudoku.csv") as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
            for j, row in enumerate(reader):
                if j >= pool:
                    break
                if len(selected) < sample_size:
                    k = len(selected)
                else:
                    k = rng.randint(0, j)
                    if k >= sample_size:
                        continue
                selected[k:k+1] = [([int(x) for x in row["quizzes"]],
                                    [int(x) for x in row["solutions"]])]
    if len(selected) < sample_size:
        raise ValueError("CSV has fewer valid records than requested")
    return selected


def make_unsat(example):
    """Introduce two incompatible fixed clues in one row."""
    bad = example["puzzle"].copy()
    n = example["size"]
    for r in range(n):
        fixed = [c for c in range(n) if bad[r*n+c]]
        if len(fixed) >= 2:
            bad[r*n+fixed[1]] = bad[r*n+fixed[0]]
            return {"id": example["id"] + "_unsat", "size": n, "puzzle": bad,
                    "source": "contradictory_control"}
    raise ValueError("Not enough clues to construct a contradiction")


def prepare(args):
    rng = random.Random(args.seed)
    instances = []
    for i, (p, s) in enumerate(read_kaggle(args.archive, args.n9, args.pool, args.seed)):
        instances.append({"id": f"s9_{i+1:06d}", "size": 9, "puzzle": p,
                          "reference_solution": s, "source": "kaggle"})
    for n, count, keep in [(16, args.n16, args.keep16), (25, args.n25, args.keep25)]:
        unsat_indexes = set(rng.sample(range(count), round(count * args.unsat_rate)))
        batch = []
        for i in range(count):
            p, s = generate(n, rng, keep)
            item = {"id": f"s{n}_{i+1:06d}", "size": n, "puzzle": p,
                    "reference_solution": s, "source": "generated"}
            batch.append(make_unsat(item) if i in unsat_indexes else item)
        rng.shuffle(batch)
        instances.extend(batch)
    rng.shuffle(instances)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "instances.jsonl").open("w", encoding="utf-8") as f:
        for inst in instances:
            f.write(json.dumps(inst) + "\n")
    print(f"Created {len(instances)} instances: {dict(Counter(i['size'] for i in instances))} in {out}")


def lit(n, r, c, v):
    return (r*n+c)*n+v  # v is 1-based


def clue_literals(puzzle, n):
    return [lit(n, i//n, i % n, v) for i, v in enumerate(puzzle) if v]


def groups(n):
    for r in range(n):
        for c in range(n):
            yield [lit(n, r, c, v) for v in range(1, n+1)]
    for cells in regions(n):
        for v in range(1, n+1):
            yield [lit(n, r, c, v) for r, c in cells]


def build_cnf(n, encoding):
    from pysat.card import CardEnc, EncType
    from pysat.formula import CNF
    cnf = CNF()
    cnf.nv = n**3  # reserve primary variables BEFORE CardEnc allocates auxiliaries
    for xs in groups(n):
        cnf.append(xs)  # at least one (ALO)
        if encoding == "pairwise":
            for a, b in combinations(xs, 2):
                cnf.append([-a, -b])
        else:
            clauses = CardEnc.atmost(xs, bound=1, top_id=cnf.nv,
                                     encoding=EncType.seqcounter)
            cnf.extend(clauses.clauses)
            cnf.nv = max(cnf.nv, clauses.nv)
    return cnf


def decode(model, n):
    positives = set(x for x in model if 0 < x <= n**3)
    return [next((v for v in range(1, n+1) if lit(n, r, c, v) in positives), 0)
            for r in range(n) for c in range(n)]


def native_stats(status, variables, constraints, var_type, solution):
    return {"status": status, "variables": variables, "clauses": "",
            "constraints": constraints, "var_type": var_type,
            "solutions_found": int(solution is not None), "unique": "",
            "maxsat_cost": "", "valid_solution": solution is not None,
            "assumptions": 0, "soft_clauses": 0}


def run_sat(puzzle, n, conf, cnf, reusable=None):
    from pysat.solvers import Solver
    givens = clue_literals(puzzle, n)
    method, solver_name = conf["method"], conf["solver"]
    solution, count, unique, cost = None, 0, "", ""
    added = 0
    if method == "maxsat":
        from pysat.formula import WCNF
        from pysat.examples.rc2 import RC2
        wcnf = WCNF()
        for clause in cnf.clauses:
            wcnf.append(clause)  # Sudoku rules = hard constraints
        for x in givens:
            wcnf.append([x], weight=1)  # puzzle clues = soft constraints
        with RC2(wcnf, solver=solver_name) as rc2:
            model = rc2.compute()
            cost = rc2.cost if model is not None else ""
        if model is not None:
            solution, count = decode(model, n), 1
        if model is None:
            raise RuntimeError("MaxSAT engine returned no model for satisfiable Sudoku rules")
        # SAT/UNSAT evaluates ALL original clues, not relaxed MaxSAT feasibility.
        status = "SAT" if cost == 0 else "UNSAT"
    else:
        sat = reusable if reusable is not None else Solver(name=solver_name,
                                                            bootstrap_with=cnf.clauses)
        try:
            if method == "incremental":
                ok = sat.solve(assumptions=givens)
            else:
                for x in givens:
                    sat.add_clause([x])
                ok = sat.solve()
            if ok:
                solution, count = decode(sat.get_model(), n), 1
            if method == "multiple" and ok:
                # Block the entire CELL assignment, not auxiliary SAT variables.
                sat.add_clause([-lit(n, i//n, i % n, v)
                                for i, v in enumerate(solution)])
                added = 1
                has_second = sat.solve()
                if has_second:
                    count, unique = 2, False
                else:
                    unique = True
            status = "SAT" if ok else "UNSAT"
        finally:
            if reusable is None:
                sat.delete()
    solved = solution is not None
    assumptions = len(givens) if method == "incremental" else 0
    soft_clauses = len(givens) if method == "maxsat" else 0
    # Assumptions are NOT permanently added as clauses to incremental CNF.
    clauses = len(cnf.clauses) + (0 if assumptions else len(givens)) + added
    return {"status": status, "variables": cnf.nv, "clauses": clauses,
            "constraints": "", "var_type": "boolean", "solutions_found": count,
            "unique": unique, "maxsat_cost": cost, "valid_solution": solved,
            "assumptions": assumptions, "soft_clauses": soft_clauses}, solution


def run_cpsat(puzzle, n, timeout):
    from ortools.sat.python import cp_model
    model = cp_model.CpModel()
    x = [[model.NewIntVar(1, n, f"x_{r}_{c}") for c in range(n)] for r in range(n)]
    for cells in regions(n):
        model.AddAllDifferent([x[r][c] for r, c in cells])
    for i, v in enumerate(puzzle):
        if v:
            model.Add(x[i//n][i % n] == v)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.max_time_in_seconds = timeout
    result = solver.Solve(model)
    ok = result in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    status = "SAT" if ok else "UNSAT" if result == cp_model.INFEASIBLE else "TIMEOUT"
    solution = [solver.Value(x[r][c]) for r in range(n) for c in range(n)] if ok else None
    return native_stats(status, n*n, 3*n+sum(map(bool, puzzle)), "integer", solution), solution


def run_gurobi(puzzle, n, timeout):
    import gurobipy as gp
    from gurobipy import GRB
    m = gp.Model()
    m.Params.OutputFlag = 0
    m.Params.Threads = 1
    m.Params.TimeLimit = timeout
    x = m.addVars(n, n, n, vtype=GRB.BINARY)
    for r in range(n):
        for c in range(n):
            m.addConstr(gp.quicksum(x[r, c, k] for k in range(n)) == 1)
    for cells in regions(n):
        for k in range(n):
            m.addConstr(gp.quicksum(x[r, c, k] for r, c in cells) == 1)
    for i, v in enumerate(puzzle):
        if v:
            m.addConstr(x[i//n, i % n, v-1] == 1)
    m.optimize()
    ok = m.SolCount > 0
    status = "SAT" if ok else "UNSAT" if m.Status == GRB.INFEASIBLE else "TIMEOUT"
    solution = ([next(k+1 for k in range(n) if x[r, c, k].X > .5)
                 for r in range(n) for c in range(n)] if ok else None)
    return native_stats(status, n**3, 4*n*n+sum(map(bool, puzzle)),
                        "binary MIP", solution), solution


def run_cplex_mip(puzzle, n, timeout):
    from docplex.mp.model import Model
    model = Model(name="sudoku")
    model.parameters.timelimit = timeout
    model.parameters.threads = 1
    x = model.binary_var_cube(range(n), range(n), range(n), name="x")
    for r in range(n):
        for c in range(n):
            model.add_constraint(model.sum(x[r, c, k] for k in range(n)) == 1)
    for cells in regions(n):
        for k in range(n):
            model.add_constraint(model.sum(x[r, c, k] for r, c in cells) == 1)
    for i, v in enumerate(puzzle):
        if v:
            model.add_constraint(x[i//n, i % n, v-1] == 1)
    sol = model.solve(log_output=False)
    detail = str(model.solve_details.status).lower() if model.solve_details else ""
    ok = sol is not None
    status = "SAT" if ok else "UNSAT" if "infeasible" in detail else "TIMEOUT"
    grid = ([next(k+1 for k in range(n) if sol.get_value(x[r, c, k]) > .5)
             for r in range(n) for c in range(n)] if ok else None)
    return native_stats(status, n**3, 4*n*n+sum(map(bool, puzzle)),
                        "binary MIP", grid), grid


def run_cplex_cp(puzzle, n, timeout):
    from docplex.cp.model import CpoModel, integer_var, all_diff
    model = CpoModel()
    x = [[integer_var(min=1, max=n, name=f"x_{r}_{c}")
          for c in range(n)] for r in range(n)]
    for cells in regions(n):
        model.add(all_diff([x[r][c] for r, c in cells]))
    for i, v in enumerate(puzzle):
        if v:
            model.add(x[i//n][i % n] == v)
    result = model.solve(TimeLimit=timeout, Workers=1, LogVerbosity="Quiet")
    ok = result is not None and result.is_solution()
    detail = str(result.get_solve_status()).lower() if result else ""
    status = "SAT" if ok else "UNSAT" if "infeasible" in detail else "TIMEOUT"
    grid = ([result.get_value(x[r][c]) for r in range(n) for c in range(n)] if ok else None)
    return native_stats(status, n*n, 3*n+sum(map(bool, puzzle)), "integer CP", grid), grid


def worker(conf, items, timeout, out):
    """Keep a SAT base CNF (and incremental solver) alive across all items of one size."""
    reusable = None
    try:
        n = items[0]["size"]
        t = time.perf_counter()
        cnf = build_cnf(n, conf["encoding"]) if conf["method"] in SAT_METHODS else None
        if conf["method"] == "incremental":
            from pysat.solvers import Solver
            reusable = Solver(name=conf["solver"], bootstrap_with=cnf.clauses)
        setup_s = time.perf_counter()-t
        out.put(("setup", setup_s, (cnf.nv, len(cnf.clauses)) if cnf else None))
        for inst in items:
            t = time.perf_counter()
            try:
                puzzle = inst["puzzle"]
                if conf["method"] in SAT_METHODS:
                    stats, solution = run_sat(puzzle, n, conf, cnf, reusable)
                elif conf["method"] == "cpsat":
                    stats, solution = run_cpsat(puzzle, n, timeout)
                elif conf["method"] == "gurobi":
                    stats, solution = run_gurobi(puzzle, n, timeout)
                elif conf["method"] == "cplex_mip":
                    stats, solution = run_cplex_mip(puzzle, n, timeout)
                else:
                    stats, solution = run_cplex_cp(puzzle, n, timeout)
                clues = None if conf["method"] == "maxsat" else puzzle
                if solution and not valid(solution, clues):
                    raise AssertionError("Solver returned an invalid Sudoku")
                error = ""
            except Exception as e:
                stats, solution, error = incomplete(inst, conf, "ERROR",
                    repr(e), (cnf.nv, len(cnf.clauses)) if cnf else None), None, repr(e)
            stats.update({"instance": inst["id"], "size": n,
                          "givens": sum(bool(v) for v in inst["puzzle"]),
                          "method": conf["method"], "solver": conf["solver"],
                          "encoding": conf["encoding"], "runtime_s": round(time.perf_counter()-t, 6),
                          "setup_s": round(setup_s, 6), "error": error})
            out.put(("result", stats, solution))
    except Exception as e:
        out.put(("fatal", repr(e)))
    finally:
        if reusable is not None:
            reusable.delete()


def configurations(profile, gurobi=False, cplex_mip=False, cplex_cp=False):
    # Config IDs also allow running one method/solver/encoding at a time.
    conf = [{"name": name, "method": m, "solver": s, "encoding": e}
            for name, m, s, e in [
        ("sat-cadical-seq", "sat", "cadical153", "seqcounter"),
        ("sat-glucose-seq", "sat", "glucose4", "seqcounter"),
        ("sat-cadical-pair", "sat", "cadical153", "pairwise"),
        ("maxsat", "maxsat", "cadical153", "seqcounter"),
        ("multiple", "multiple", "cadical153", "seqcounter"),
        ("incremental", "incremental", "cadical153", "seqcounter"),
        ("cpsat", "cpsat", "ortools", "-")]]
    if profile == "quick":
        conf = [c for c in conf if c["name"] != "sat-cadical-pair"]
    for enabled, name, method, solver in [
        (gurobi, "gurobi", "gurobi", "gurobi"),
        (cplex_mip, "cplex-mip", "cplex_mip", "cplex"),
        (cplex_cp, "cplex-cp", "cplex_cp", "cp_optimizer")]:
        if enabled:
            conf.append({"name": name, "method": method, "solver": solver, "encoding": "-"})
    return conf


def incomplete(inst, conf, status, error="", cnf_info=None):
    n, method = inst["size"], conf["method"]
    givens = sum(bool(v) for v in inst["puzzle"])
    is_sat = method in SAT_METHODS
    if is_sat:
        variables = cnf_info[0] if cnf_info else ""
        clauses = cnf_info[1]+(0 if method == "incremental" else givens) if cnf_info else ""
        constraints, vtype = "", "boolean"
    else:
        variables = n*n if method in ("cpsat", "cplex_cp") else n**3
        clauses = ""
        constraints = (3*n if method in ("cpsat", "cplex_cp") else 4*n*n)+givens
        vtype = "integer" if method in ("cpsat", "cplex_cp") else "binary MIP"
    return {"instance": inst["id"], "size": n, "givens": givens, "method": method,
            "solver": conf["solver"], "encoding": conf["encoding"],
            "status": status, "runtime_s": "", "setup_s": "", "variables": variables,
            "clauses": clauses, "constraints": constraints, "var_type": vtype,
            "solutions_found": "", "unique": "", "maxsat_cost": "", "valid_solution": "",
            "assumptions": givens if method == "incremental" else 0,
            "soft_clauses": givens if method == "maxsat" else 0, "error": error}


def summarize(csv_path):
    """Stream results into per-configuration summaries."""
    grouped = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = tuple(row[name] for name in ("size", "method", "solver", "encoding"))
            stats = grouped.setdefault(key, {"instances": 0, "runtime": 0.0,
                                             "completed": 0, "status": Counter()})
            stats["instances"] += 1
            stats["status"][row["status"]] += 1
            if row["status"] in ("SAT", "UNSAT") and row["runtime_s"]:
                stats["runtime"] += float(row["runtime_s"])
                stats["completed"] += 1

    columns = ["size", "method", "solver", "encoding", "instances", "sat", "unsat",
               "timeouts", "errors", "mean_runtime_s"]
    path = Path(csv_path).with_name("summary.csv")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for key, stats in sorted(grouped.items(), key=lambda item: (int(item[0][0]), item[0][1:])):
            counts = stats["status"]
            writer.writerow(dict(zip(columns[:4], key)) | {
                "instances": stats["instances"], "sat": counts["SAT"], "unsat": counts["UNSAT"],
                "timeouts": counts["TIMEOUT"], "errors": counts["ERROR"],
                "mean_runtime_s": round(stats["runtime"] / stats["completed"], 6)
                if stats["completed"] else ""})
    return path


def benchmark(args):
    with (Path(args.data) / "instances.jsonl").open(encoding="utf-8") as f:
        items = [json.loads(l) for l in f if l.strip()]
    if args.size is not None:
        items = [item for item in items if item["size"] == args.size]
    if not items:
        raise ValueError("No instances match --size")
    configs = configurations(args.profile,
                             args.gurobi or args.config == "gurobi",
                             args.cplex_mip or args.config == "cplex-mip",
                             args.cplex_cp or args.config == "cplex-cp")
    if args.config is not None:
        configs = [c for c in configs if c["name"] == args.config]
    if not configs:
        raise ValueError("No configuration matches --config (try --profile standard)")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ctx = mp.get_context("spawn")  # Windows / Linux compatible
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as csvfile, \
         (out / "solutions.jsonl").open("w", encoding="utf-8") as solutions_file:
        writer = csv.DictWriter(csvfile, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for conf in configs:
            for n in sorted({i["size"] for i in items}):
                batch = [i for i in items if i["size"] == n]
                index = 0
                while index < len(batch):
                    q = ctx.Queue()
                    p = ctx.Process(target=worker, args=(conf, batch[index:], args.timeout, q))
                    p.start()
                    try:
                        kind, *payload = q.get(timeout=max(90.0, args.timeout*2))
                        if kind == "fatal":
                            for inst in batch[index:]:
                                row = incomplete(inst, conf, "ERROR", payload[0])
                                writer.writerow(row)
                                print(row["instance"], conf["method"], "ERROR:", payload[0])
                            break
                        setup_time, cnf_info = payload
                        while index < len(batch):
                            try:
                                kind, *payload = q.get(timeout=args.timeout)
                                row, grid = payload
                            except queue.Empty:
                                row = incomplete(batch[index], conf, "TIMEOUT", cnf_info=cnf_info)
                                row["runtime_s"] = args.timeout
                                row["setup_s"] = round(setup_time, 6)
                                grid = None
                            writer.writerow(row)
                            csvfile.flush()
                            if grid is not None:
                                solutions_file.write(json.dumps({"instance": row["instance"],
                                    "method": row["method"], "solver": row["solver"],
                                    "encoding": row["encoding"], "grid": grid}) + "\n")
                                solutions_file.flush()
                            print(f"{row['instance']:16} {row['method']:12} {row['solver']:12} "
                                  f"{row['encoding']:10} {row['status']:8} {row['runtime_s']}")
                            index += 1
                            if row["status"] == "TIMEOUT":
                                break  # restart fresh worker for the remaining instances
                    except queue.Empty:
                        for inst in batch[index:]:
                            row = incomplete(inst, conf, "ERROR", "Worker setup timed out")
                            writer.writerow(row)
                        break
                    finally:
                        if p.is_alive():
                            p.terminate()
                        p.join(timeout=5)
                        q.close()
                csvfile.flush()
    summary_path = summarize(out / "results.csv")
    print(f"Results: {out / 'results.csv'}; Summary: {summary_path}; "
          f"Solutions: {out / 'solutions.jsonl'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    prep = subs.add_parser("prepare", help="Sample zipped Kaggle CSV and generate larger puzzles")
    prep.add_argument("--archive", required=True, help="Path to original Kaggle ZIP")
    prep.add_argument("--out", default="data")
    prep.add_argument("--n9", type=int, default=100000)
    prep.add_argument("--n16", type=int, default=1000)
    prep.add_argument("--n25", type=int, default=1000)
    prep.add_argument("--pool", type=int, default=1000000)
    prep.add_argument("--seed", type=int, default=42)
    prep.add_argument("--keep16", type=float, default=.60)
    prep.add_argument("--keep25", type=float, default=.72)
    prep.add_argument("--unsat-rate", type=float, default=.30,
                      help="UNSAT fraction within generated 16x16 and 25x25 sets")
    bench = subs.add_parser("benchmark", help="Run solver comparisons and write CSV/JSONL")
    bench.add_argument("--data", default="data")
    bench.add_argument("--out", default="results")
    bench.add_argument("--timeout", type=float, default=30.0, help="Wall-time limit per instance")
    bench.add_argument("--profile", choices=["standard", "quick"], default="standard")
    bench.add_argument("--config", choices=["sat-cadical-seq", "sat-glucose-seq",
        "sat-cadical-pair", "maxsat", "multiple", "incremental", "cpsat",
        "gurobi", "cplex-mip", "cplex-cp"], help="Run one configuration only")
    bench.add_argument("--size", type=int, choices=[9, 16, 25],
                       help="Run only this Sudoku board size")
    bench.add_argument("--gurobi", action="store_true", help="Optional licensed Gurobi MIP")
    bench.add_argument("--cplex-mip", action="store_true", help="Optional licensed CPLEX MIP")
    bench.add_argument("--cplex-cp", action="store_true", help="Optional licensed CPLEX CP Optimizer")
    args = parser.parse_args()
    if args.command == "prepare":
        if not 0 <= args.unsat_rate <= 1:
            parser.error("--unsat-rate must be between 0 and 1")
        prepare(args)
    else:
        if args.timeout <= 0:
            parser.error("--timeout must be positive")
        benchmark(args)


if __name__ == "__main__":
    main()
