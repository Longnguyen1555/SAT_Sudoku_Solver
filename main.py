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
from pathlib import Path


FIELDS = ["instance", "size", "givens", "method", "solver", "encoding", "status",
          "expected_status", "correct_status", "runtime_s", "setup_s", "variables",
          "clauses", "constraints", "var_type", "solutions_found", "optimal",
          "maxsat_cost", "preference_score", "preference_matches", "valid_solution", "assumptions",
          "soft_clauses", "error"]
SAT_METHODS = {"sat", "maxsat", "multiple", "incremental"}
SAT_ENCODINGS = {
    "seqcounter": "seqcounter",
    "pairwise": "pairwise",
    "cardinality": "cardnetwrk",
}


def regions(n):
    b = math.isqrt(n)
    for r in range(n):
        yield [(r, c) for c in range(n)]
    for c in range(n):
        yield [(r, c) for r in range(n)]
    for br in range(0, n, b):
        for bc in range(0, n, b):
            yield [(r, c) for r in range(br, br+b) for c in range(bc, bc+b)]


def valid(grid, check_clues=None):
    n = math.isqrt(len(grid))
    b = math.isqrt(n)
    if n * n != len(grid) or b * b != n:
        return False
    target = set(range(1, n + 1))
    if check_clues and any(v and v != grid[i] for i, v in enumerate(check_clues)):
        return False
    return all({grid[r*n+c] for r, c in cells} == target for cells in regions(n))


def read_kaggle(archive, sample_size, pool, seed):
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
    puzzle = example["puzzle"].copy()
    n = example["size"]
    for r in range(n):
        fixed = [c for c in range(n) if puzzle[r*n+c]]
        empty = [c for c in range(n) if not puzzle[r*n+c]]
        if fixed and empty:
            puzzle[r*n+empty[0]] = puzzle[r*n+fixed[0]]
            return {"id": example["id"] + "_unsat", "size": n,
                    "puzzle": puzzle, "source": "contradictory_control",
                    "base_instance": example["id"], "expected_status": "UNSAT"}
    raise ValueError("Not enough fixed clues to construct an UNSAT control")


def prepare(args):
    rng = random.Random(args.seed)
    instances = []
    for i, (puzzle, solution) in enumerate(
            read_kaggle(args.archive, args.n9, args.pool, args.seed)):
        fixed = [j for j, v in enumerate(puzzle) if v]
        if len(fixed) < args.givens:
            raise ValueError("A sampled puzzle has fewer clues than --givens")
        for j in rng.sample(fixed, len(fixed)-args.givens):
            puzzle[j] = 0
        instances.append({"id": f"s9_{i+1:06d}", "size": 9, "puzzle": puzzle,
                          "reference_solution": solution, "source": "kaggle_reduced",
                          "expected_status": "SAT"})
    instances.extend(make_unsat(inst) for inst in rng.sample(instances, args.n_unsat))
    rng.shuffle(instances)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "instances.jsonl").open("w", encoding="utf-8") as f:
        for inst in instances:
            f.write(json.dumps(inst) + "\n")
    print(f"Created {len(instances)} instances: {dict(Counter(i['size'] for i in instances))} in {out}")


def lit(n, r, c, v):
    return (r*n+c)*n+v


def expected_status(inst):
    if inst.get("expected_status") in ("SAT", "UNSAT"):
        return inst["expected_status"]
    if inst.get("source") == "contradictory_control":
        return "UNSAT"
    return "SAT" if inst.get("reference_solution") is not None else ""


def clue_literals(puzzle, n):
    return [lit(n, i//n, i % n, v) for i, v in enumerate(puzzle) if v]


def preference_clauses(puzzle, n):
    b, clauses = math.isqrt(n), []
    order = sorted(range(n), key=lambda i: (abs(i-(n-1)/2), i))
    for br in range(0, n, b):
        for bc in range(0, n, b):
            box = [(br+i//b, bc+i % b) for i in range(n)]
            cells = [box[i] for i in order if not puzzle[box[i][0]*n+box[i][1]]]
            used = {puzzle[r*n+c] for r, c in box}
            digits = sorted(set(range(1, n+1))-used, reverse=True)
            clauses.extend((lit(n, r, c, v), v)
                           for (r, c), v in zip(cells, digits))
    return clauses


def preference_quality(solution, prefs, n):
    achieved = [(x, w) for x, w in prefs
                if solution[(x-1)//n] == (x-1) % n+1]
    return sum(w for _, w in achieved), len(achieved)


def preference_upper_bound(puzzle, prefs, n):
    total = 0
    for v in range(1, n+1):
        fixed_r = {i//n for i, x in enumerate(puzzle) if x == v}
        fixed_c = {i % n for i, x in enumerate(puzzle) if x == v}
        cells = [((x-1)//n//n, (x-1)//n % n) for x, w in prefs if w == v]
        cells = [(r, c) for r, c in cells if r not in fixed_r and c not in fixed_c]
        total += v*min(len(cells), len({r for r, _ in cells}),
                       len({c for _, c in cells}))
    return total


def groups(n):
    for r in range(n):
        for c in range(n):
            yield [lit(n, r, c, v) for v in range(1, n+1)]
    for cells in regions(n):
        for v in range(1, n+1):
            yield [lit(n, r, c, v) for r, c in cells]


def build_cnf(n, encoding="seqcounter"):
    from pysat.card import CardEnc, EncType
    from pysat.formula import CNF
    if encoding not in SAT_ENCODINGS:
        raise ValueError(f"Unknown SAT encoding: {encoding}")
    cnf = CNF()
    cnf.nv = n**3
    for xs in groups(n):
        cnf.append(xs)
        clauses = CardEnc.atmost(xs, bound=1, top_id=cnf.nv,
                                 encoding=getattr(EncType, SAT_ENCODINGS[encoding]))
        cnf.extend(clauses.clauses)
        cnf.nv = max(cnf.nv, clauses.nv)
    return cnf


def decode(model, n):
    positives = set(x for x in model if 0 < x <= n**3)
    return [next((v for v in range(1, n+1) if lit(n, r, c, v) in positives), 0)
            for r in range(n) for c in range(n)]


def mip_stats(status, n, puzzle, solution):
    return {"status": status, "variables": n**3, "clauses": "",
            "constraints": 4*n*n+sum(map(bool, puzzle)), "var_type": "binary MIP",
            "solutions_found": int(solution is not None), "optimal": "",
            "maxsat_cost": "", "preference_score": "", "preference_matches": "",
            "valid_solution": solution is not None,
            "assumptions": 0, "soft_clauses": 0}


def add_mip_constraints(puzzle, n, x, add, total):
    for r in range(n):
        for c in range(n):
            add(total(x[r, c, k] for k in range(n)) == 1)
    for cells in regions(n):
        for k in range(n):
            add(total(x[r, c, k] for r, c in cells) == 1)
    for i, v in enumerate(puzzle):
        if v:
            add(x[i//n, i % n, v-1] == 1)


def decode_mip(n, value):
    return [next(k+1 for k in range(n) if value(r, c, k) > .5)
            for r in range(n) for c in range(n)]


def solve_maxsat(cnf, givens, prefs, n, solver_name):
    from pysat.solvers import Solver
    from pysat.formula import WCNF
    from pysat.examples.rc2 import RC2
    with Solver(name=solver_name, bootstrap_with=cnf.clauses) as check:
        if not check.solve(assumptions=givens):
            return None, "", "UNSAT"
    wcnf = WCNF()
    for clause in cnf.clauses:
        wcnf.append(clause)
    for x in givens:
        wcnf.append([x])
    for x, weight in prefs:
        wcnf.append([x], weight=weight)
    with RC2(wcnf, solver=solver_name) as rc2:
        model, cost = rc2.compute(), rc2.cost
    if model is None:
        raise RuntimeError("RC2 returned no model after a successful feasibility check")
    return decode(model, n), cost, "SAT"


def optimize_sat(sat, method, puzzle, givens, prefs, n, solver_name, cnf, solution):
    from pysat.card import ITotalizer
    from pysat.solvers import Solver
    score, matches = preference_quality(solution, prefs, n)
    count, variables, added = 1, cnf.nv, 0
    if method == "incremental":
        accepted = []
        with Solver(name=solver_name, bootstrap_with=cnf.clauses) as seed:
            for x, _ in sorted(prefs, key=lambda item: -item[1]):
                if seed.solve(assumptions=givens+accepted+[x]):
                    accepted.append(x)
            if accepted and seed.solve(assumptions=givens+accepted):
                candidate = decode(seed.get_model(), n)
                candidate_score, candidate_matches = preference_quality(candidate, prefs, n)
                if candidate_score > score:
                    solution, score, matches = candidate, candidate_score, candidate_matches
                    count += 1
    weighted = [x for x, weight in prefs for _ in range(weight)]
    total = len(weighted)
    if not total:
        return solution, count, score, matches, variables, added
    with ITotalizer(lits=[-x for x in weighted], ubound=total,
                    top_id=variables) as totalizer:
        sat.append_formula(totalizer.cnf.clauses)
        variables, added = totalizer.top_id, len(totalizer.cnf.clauses)
        high = preference_upper_bound(puzzle, prefs, n)
        if method == "multiple":
            low = score
            while low < high:
                target = (low+high+1)//2
                bound = -totalizer.rhs[total-target]
                if sat.solve(assumptions=givens+[bound]):
                    solution = decode(sat.get_model(), n)
                    score, matches = preference_quality(solution, prefs, n)
                    count, low = count+1, max(target, score)
                else:
                    high = target-1
        else:
            while score < high:
                sat.add_clause([-totalizer.rhs[total-score-1]])
                added += 1
                if not sat.solve(assumptions=givens):
                    break
                solution = decode(sat.get_model(), n)
                new_score, matches = preference_quality(solution, prefs, n)
                if new_score <= score:
                    raise AssertionError("Incremental bound did not improve the score")
                score, count = new_score, count+1
    return solution, count, score, matches, variables, added


def run_sat(puzzle, n, conf, cnf):
    from pysat.solvers import Solver
    method, solver_name = conf["method"], conf["solver"]
    givens, prefs = clue_literals(puzzle, n), preference_clauses(puzzle, n)
    solution, count, optimal, cost, score, matches = None, 0, "", "", "", ""
    variables, added = cnf.nv, 0
    if method == "maxsat":
        solution, cost, status = solve_maxsat(cnf, givens, prefs, n, solver_name)
        count, optimal = int(solution is not None), True if solution else ""
    else:
        with Solver(name=solver_name, bootstrap_with=cnf.clauses) as sat:
            ok = sat.solve(assumptions=givens)
            if ok:
                solution, count = decode(sat.get_model(), n), 1
            if ok and method in {"multiple", "incremental"}:
                solution, count, score, matches, variables, added = optimize_sat(
                    sat, method, puzzle, givens, prefs, n, solver_name, cnf, solution)
                optimal = True
        status = "SAT" if ok else "UNSAT"
    if solution and score == "":
        score, matches = preference_quality(solution, prefs, n)
    clauses = len(cnf.clauses) + (len(givens) if method == "maxsat" else 0) + added
    return {"status": status, "variables": variables, "clauses": clauses,
            "constraints": "", "var_type": "boolean", "solutions_found": count,
            "optimal": optimal, "maxsat_cost": cost, "preference_score": score,
            "preference_matches": matches,
            "valid_solution": solution is not None,
            "assumptions": len(givens),
            "soft_clauses": len(prefs) if method == "maxsat" else 0}, solution


def run_gurobi(puzzle, n, timeout):
    import gurobipy as gp
    from gurobipy import GRB
    m = gp.Model()
    m.Params.OutputFlag = 0
    m.Params.Threads = 1
    m.Params.TimeLimit = timeout
    x = m.addVars(n, n, n, vtype=GRB.BINARY)
    add_mip_constraints(puzzle, n, x, m.addConstr, gp.quicksum)
    m.optimize()
    ok = m.SolCount > 0
    status = "SAT" if ok else "UNSAT" if m.Status == GRB.INFEASIBLE else "TIMEOUT"
    solution = decode_mip(n, lambda r, c, k: x[r, c, k].X) if ok else None
    return mip_stats(status, n, puzzle, solution), solution


def run_cplex_mip(puzzle, n, timeout):
    from docplex.mp.model import Model
    model = Model(name="sudoku")
    model.parameters.timelimit = timeout
    model.parameters.threads = 1
    x = model.binary_var_cube(range(n), range(n), range(n), name="x")
    add_mip_constraints(puzzle, n, x, model.add_constraint, model.sum)
    sol = model.solve(log_output=False)
    detail = str(model.solve_details.status).lower() if model.solve_details else ""
    ok = sol is not None
    status = "SAT" if ok else "UNSAT" if "infeasible" in detail else "TIMEOUT"
    grid = decode_mip(n, lambda r, c, k: sol.get_value(x[r, c, k])) if ok else None
    return mip_stats(status, n, puzzle, grid), grid


def worker(conf, items, timeout, out):
    try:
        method = conf["method"]
        n = items[0]["size"]
        t = time.perf_counter()
        cnf = build_cnf(n, conf["encoding"]) if method in SAT_METHODS else None
        setup_s = time.perf_counter()-t
        out.put(("setup", setup_s, (cnf.nv, len(cnf.clauses)) if cnf else None))
        for inst in items:
            t = time.perf_counter()
            try:
                puzzle = inst["puzzle"]
                if method in SAT_METHODS:
                    stats, solution = run_sat(puzzle, n, conf, cnf)
                elif method == "gurobi":
                    stats, solution = run_gurobi(puzzle, n, timeout)
                else:
                    stats, solution = run_cplex_mip(puzzle, n, timeout)
                if solution and not valid(solution, puzzle):
                    raise AssertionError("Solver returned an invalid Sudoku")
                error = ""
            except Exception as e:
                stats, solution, error = incomplete(inst, conf, "ERROR",
                    repr(e), (cnf.nv, len(cnf.clauses)) if cnf else None), None, repr(e)
            stats.update({"instance": inst["id"], "size": n,
                          "givens": sum(bool(v) for v in inst["puzzle"]),
                          "method": method, "solver": conf["solver"],
                          "encoding": conf["encoding"], "runtime_s": round(time.perf_counter()-t, 6),
                          "setup_s": round(setup_s, 6), "error": error})
            expected = expected_status(inst)
            stats.update({"expected_status": expected,
                          "correct_status": stats["status"] == expected if expected else ""})
            out.put(("result", stats, solution))
    except Exception as e:
        out.put(("fatal", repr(e)))


def configurations():
    return [{"name": name, "method": method, "solver": solver, "encoding": encoding}
            for name, method, solver, encoding in [
        (f"sat-{solver_label}-{encoding_label}", "sat", solver, encoding)
        for solver_label, solver in (("cadical", "cadical153"), ("glucose", "glucose4"))
        for encoding_label, encoding in (("seq", "seqcounter"),
                                         ("pair", "pairwise"),
                                         ("card", "cardinality"))
    ] + [
        ("maxsat", "maxsat", "cadical153", "seqcounter"),
        ("multiple", "multiple", "cadical153", "seqcounter"),
        ("incremental", "incremental", "cadical153", "seqcounter"),
        ("gurobi", "gurobi", "gurobi", "-"),
        ("cplex-mip", "cplex_mip", "cplex", "-")]]


def incomplete(inst, conf, status, error="", cnf_info=None):
    n, method = inst["size"], conf["method"]
    givens = sum(bool(v) for v in inst["puzzle"])
    if method in SAT_METHODS:
        variables = cnf_info[0] if cnf_info else ""
        clauses = cnf_info[1]+(givens if method == "maxsat" else 0) if cnf_info else ""
        constraints, vtype = "", "boolean"
    else:
        variables, clauses = n**3, ""
        constraints, vtype = 4*n*n+givens, "binary MIP"
    expected = expected_status(inst)
    return {"instance": inst["id"], "size": n, "givens": givens, "method": method,
            "solver": conf["solver"], "encoding": conf["encoding"],
            "status": status, "expected_status": expected,
            "correct_status": status == expected if expected else "",
            "runtime_s": "", "setup_s": "", "variables": variables,
            "clauses": clauses, "constraints": constraints, "var_type": vtype,
            "solutions_found": "", "optimal": "", "maxsat_cost": "", "preference_score": "",
            "preference_matches": "", "valid_solution": "",
            "assumptions": givens if method in SAT_METHODS else 0,
            "soft_clauses": len(preference_clauses(inst["puzzle"], n))
            if method == "maxsat" else 0, "error": error}


def summarize(csv_path):
    grouped = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = tuple(row[name] for name in ("size", "method", "solver", "encoding"))
            stats = grouped.setdefault(key, {"instances": 0, "runtime": 0.0,
                                             "completed": 0, "labeled": 0,
                                             "correct": 0, "status": Counter()})
            stats["instances"] += 1
            stats["status"][row["status"]] += 1
            expected = row.get("expected_status", "")
            if expected in ("SAT", "UNSAT"):
                stats["labeled"] += 1
                stats["correct"] += row["status"] == expected
            if row["status"] in ("SAT", "UNSAT") and row["runtime_s"]:
                stats["runtime"] += float(row["runtime_s"])
                stats["completed"] += 1

    columns = ["size", "method", "solver", "encoding", "instances", "sat", "unsat",
               "timeouts", "errors", "correct", "labeled", "accuracy", "mean_runtime_s"]
    path = Path(csv_path).with_name("summary.csv")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for key, stats in sorted(grouped.items(), key=lambda item: (int(item[0][0]), item[0][1:])):
            counts = stats["status"]
            writer.writerow(dict(zip(columns[:4], key)) | {
                "instances": stats["instances"], "sat": counts["SAT"], "unsat": counts["UNSAT"],
                "timeouts": counts["TIMEOUT"], "errors": counts["ERROR"],
                "correct": stats["correct"], "labeled": stats["labeled"],
                "accuracy": round(stats["correct"] / stats["labeled"], 6)
                if stats["labeled"] else "",
                "mean_runtime_s": round(stats["runtime"] / stats["completed"], 6)
                if stats["completed"] else ""})
    return path


def benchmark(args):
    with (Path(args.data) / "instances.jsonl").open(encoding="utf-8") as f:
        items = [json.loads(l) for l in f if l.strip()]
    if not items:
        raise ValueError("Dataset is empty")
    configs = configurations()
    if args.config is not None:
        configs = [c for c in configs if c["name"] == args.config]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ctx = mp.get_context("spawn")
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
                                _, *payload = q.get(timeout=args.timeout)
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
                                break
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
    prep = subs.add_parser("prepare", help="Sample 9x9 puzzles from the zipped Kaggle CSV")
    prep.add_argument("--archive", required=True, help="Path to original Kaggle ZIP")
    prep.add_argument("--out", default="data_preference_1100")
    prep.add_argument("--n9", type=int, default=1000)
    prep.add_argument("--n-unsat", type=int, default=100,
                      help="Number of contradictory controls copied from the SAT sample")
    prep.add_argument("--givens", type=int, default=16,
                      help="Clues retained per SAT puzzle so soft preferences can break ties")
    prep.add_argument("--pool", type=int, default=1000000)
    prep.add_argument("--seed", type=int, default=42)
    bench = subs.add_parser("benchmark", help="Run solver comparisons and write CSV/JSONL")
    bench.add_argument("--data", default="data_preference_1100")
    bench.add_argument("--out", default="results")
    bench.add_argument("--timeout", type=float, default=30.0, help="Wall-time limit per instance")
    bench.add_argument("--config", choices=[c["name"] for c in configurations()],
                       help="Run one configuration only")
    args = parser.parse_args()
    if args.command == "prepare":
        if args.n9 <= 0:
            parser.error("--n9 must be positive")
        if not 0 <= args.n_unsat <= args.n9:
            parser.error("--n-unsat must be between 0 and --n9")
        if not 1 <= args.givens <= 81:
            parser.error("--givens must be between 1 and 81")
        prepare(args)
    else:
        if args.timeout <= 0:
            parser.error("--timeout must be positive")
        benchmark(args)


if __name__ == "__main__":
    main()
