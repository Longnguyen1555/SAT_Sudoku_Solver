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


def valid(grid, check_clues=None):
    """Check a complete n x n Sudoku; optionally check all fixed clues."""
    n = math.isqrt(len(grid))
    b = math.isqrt(n)
    if n * n != len(grid) or b * b != n:
        return False
    target = set(range(1, n + 1))
    if check_clues and any(v and v != grid[i] for i, v in enumerate(check_clues)):
        return False
    for r in range(n):
        if set(grid[r*n:(r+1)*n]) != target:
            return False
        if {grid[c*n+r] for c in range(n)} != target:
            return False
    return all({grid[(br+i)*n+bc+j] for i in range(b) for j in range(b)} == target
               for br in range(0, n, b) for bc in range(0, n, b))


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
    rng, selected = random.Random(seed), []
    with zipfile.ZipFile(archive) as zf:
        with zf.open("sudoku.csv") as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
            for j, row in enumerate(reader):
                if j >= pool:
                    break
                quiz, sol = row["quizzes"], row["solutions"]
                puzzle = [int(x) for x in quiz]
                solution = [int(x) for x in sol]
                data = (puzzle, solution)
                if len(selected) < sample_size:
                    selected.append(data)
                else:
                    k = rng.randint(0, j)
                    if k < sample_size:
                        selected[k] = data
    return selected


def make_unsat(example):
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
        instances.append({"id": f"s9_{i+1:03d}", "size": 9, "puzzle": p,
                          "reference_solution": s, "source": "kaggle"})
    for n, count, keep in [(16, args.n16, args.keep16), (25, args.n25, args.keep25)]:
        for i in range(count):
            p, s = generate(n, rng, keep)
            instances.append({"id": f"s{n}_{i+1:03d}", "size": n, "puzzle": p,
                              "reference_solution": s, "source": "generated"})
    if args.unsat_controls:
        for n in (9, 16, 25):
            ex = next((p for p in instances if p["size"] == n), None)
            if ex:
                instances.append(make_unsat(ex))
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
    b = math.isqrt(n)
    for r in range(n):
        for c in range(n):
            yield [lit(n, r, c, v) for v in range(1, n+1)]
    for r in range(n):
        for v in range(1, n+1):
            yield [lit(n, r, c, v) for c in range(n)]
    for c in range(n):
        for v in range(1, n+1):
            yield [lit(n, r, c, v) for r in range(n)]
    for br in range(0, n, b):
        for bc in range(0, n, b):
            for v in range(1, n+1):
                yield [lit(n, r, c, v)
                       for r in range(br, br+b) for c in range(bc, bc+b)]


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




def worker(conf, items, timeout, out):
    reusable = None
    try:
        n = items[0]["size"]
        t = time.perf_counter()
        cnf = build_cnf(n, conf["encoding"]) if conf["method"] in (
            "sat", "maxsat", "multiple", "incremental") else None
        if conf["method"] == "incremental":
            from pysat.solvers import Solver
            reusable = Solver(name=conf["solver"], bootstrap_with=cnf.clauses)
        setup_s = time.perf_counter()-t
        out.put(("setup", setup_s, (cnf.nv, len(cnf.clauses)) if cnf else None))
        for inst in items:
            t = time.perf_counter()
            try:
                puzzle = inst["puzzle"]
                if conf["method"] in ("sat", "maxsat", "multiple", "incremental"):
                    stats, solution = run_sat(puzzle, n, conf, cnf, reusable)
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


def configurations(profile):
    # Config IDs also allow running one method/solver/encoding at a time.
    conf = [{"name": name, "method": m, "solver": s, "encoding": e}
            for name, m, s, e in [
        ("sat-cadical-seq", "sat", "cadical153", "seqcounter"),
        ("sat-glucose-seq", "sat", "glucose4", "seqcounter"),
        ("sat-cadical-pair", "sat", "cadical153", "pairwise"),
        ("maxsat", "maxsat", "cadical153", "seqcounter"),
        ("multiple", "multiple", "cadical153", "seqcounter"),
        ("incremental", "incremental", "cadical153", "seqcounter"),
        ( "ortools", "-")]]
    return conf


def incomplete(inst, conf, status, error="", cnf_info=None):
    n, method = inst["size"], conf["method"]
    givens = sum(bool(v) for v in inst["puzzle"])
    is_sat = method in ("sat", "maxsat", "multiple", "incremental")
    
    variables = cnf_info[0] if cnf_info else ""
    clauses = cnf_info[1]+(0 if method == "incremental" else givens) if cnf_info else ""
    constraints, vtype = "", "boolean"
    return {"instance": inst["id"], "size": n, "givens": givens, "method": method,
            "solver": conf["solver"], "encoding": conf["encoding"],
            "status": status, "runtime_s": "", "setup_s": "", "variables": variables,
            "clauses": clauses, "constraints": constraints, "var_type": vtype,
            "solutions_found": "", "unique": "", "maxsat_cost": "", "valid_solution": "",
            "assumptions": givens if method == "incremental" else 0,
            "soft_clauses": givens if method == "maxsat" else 0, "error": error}


def summarize(csv_path):
    """Produce small per-configuration summaries, keeping failures explicit."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    grouped = {}
    for row in rows:
        key = tuple(row[f] for f in ("size", "method", "solver", "encoding"))
        grouped.setdefault(key, []).append(row)

    columns = ["size", "method", "solver", "encoding", "instances", "sat", "unsat",
               "timeouts", "errors", "mean_runtime_s"]
    path = Path(csv_path).with_name("summary.csv")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for key, group in sorted(grouped.items(), key=lambda item: (int(item[0][0]), item[0][1:])):
            counts = Counter(r["status"] for r in group)
            completed = [r for r in group if r["status"] in ("SAT", "UNSAT")]
            durations = [float(r["runtime_s"]) for r in completed if r["runtime_s"]]
            writer.writerow(dict(zip(columns[:4], key)) | {
                "instances": len(group), "sat": counts["SAT"], "unsat": counts["UNSAT"],
                "timeouts": counts["TIMEOUT"], "errors": counts["ERROR"],
                "mean_runtime_s": round(sum(durations) / len(durations), 6)
                if durations else ""})
    return path


def benchmark(args):
    with (Path(args.data) / "instances.jsonl").open(encoding="utf-8") as f:
        items = [json.loads(l) for l in f if l.strip()]
    if args.size is not None:
        items = [item for item in items if item["size"] == args.size]
    if not items:
        raise ValueError("No instances match --size")
    configs = configurations(args.profile)
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
    prep.add_argument("--n9", type=int, default=12)
    prep.add_argument("--n16", type=int, default=10)
    prep.add_argument("--n25", type=int, default=10)
    prep.add_argument("--pool", type=int, default=10000)
    prep.add_argument("--seed", type=int, default=42)
    prep.add_argument("--keep16", type=float, default=.60)
    prep.add_argument("--keep25", type=float, default=.72)
    prep.add_argument("--unsat-controls", action="store_true")
    bench = subs.add_parser("benchmark", help="Run solver comparisons and write CSV/JSONL")
    bench.add_argument("--data", default="data")
    bench.add_argument("--out", default="results")
    bench.add_argument("--timeout", type=float, default=30.0, help="Wall-time limit per instance")
    bench.add_argument("--profile", choices=["standard", "quick"], default="standard")
    bench.add_argument("--config", choices=["sat-cadical-seq", "sat-glucose-seq",
        "sat-cadical-pair", "maxsat", "multiple", "incremental"], help="Run one configuration only")
    bench.add_argument("--size", type=int, choices=[9, 16, 25],
                       help="Run only this Sudoku board size")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
    else:
        if args.timeout <= 0:
            parser.error("--timeout must be positive")
        benchmark(args)


if __name__ == "__main__":
    main()
