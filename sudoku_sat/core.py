from pysat.card import CardEnc, EncType
from pysat.formula import CNF, IDPool

ENCODINGS = {
    "pairwise": EncType.pairwise,
    "cardinality": EncType.cardnetwrk,
    "sequential": EncType.seqcounter,
    "totalizer": EncType.totalizer,
}

def var(row, col, digit):
    return row * 81 + col * 9 + digit

def read(puzzel):
    if len(puzzel) != 81 or not all(ch in '0123456789' for ch in puzzel):
        raise ValueError("Invalid puzzle format")
    return [[int(puzzel[r * 9 + c]) for c in range(9)] for r in range(9)]

def build_cnf(encoding):
    cnf = CNF()
    pool = IDPool(start_from=730)

    def exactly_one(vars):
        if encoding == "pairwise":
            for i in range(len(vars)):
                for j in range(i + 1, len(vars)):
                    cnf.append([-vars[i], -vars[j]])
            cnf.append(vars)
        else:
            card = CardEnc.equals(lits=vars, bound=1, vpool=pool, encoding=ENCODINGS[encoding])
            cnf.extend(card.clauses)

    for row in range(9):
        for col in range(9):
            vars = [var(row, col, digit) for digit in range(1, 10)]
            exactly_one(vars)

    for row in range(9):
        for digit in range(1, 10):
            vars = [var(row, col, digit) for col in range(9)]
            exactly_one(vars)

    for col in range(9):
        for digit in range(1, 10):
            vars = [var(row, col, digit) for row in range(9)]
            exactly_one(vars)

    for box_row in range(3):
        for box_col in range(3):
            for digit in range(1, 10):
                vars = [var(box_row * 3 + r, box_col * 3 + c, digit) for r in range(3) for c in range(3)]
                exactly_one(vars)

def solution_literals(model):
    return [x for x in model if 1 <= x <= 729]

def solution_string(model):
    grid = [[0] * 9 for _ in range(9)]
    for literal in solution_literals(model):
        literal -= 1
        row = literal // 81
        col = (literal % 81) // 9
        digit = (literal % 9) + 1
        grid[row][col] = digit
    return ''.join(str(grid[r][c]) for r in range(9) for c in range(9))