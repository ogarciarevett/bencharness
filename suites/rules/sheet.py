"""sheet task: evaluate every formula cell of a small spreadsheet."""
import json
import math
import re
from decimal import ROUND_HALF_UP, Decimal, localcontext

RULES = """\
Each item has "cells": a map from cell name (column A-H, row 1-15, like "C7") to its content. Return,
for every cell whose content is a formula, {cell: json.dumps(value)} where value is a JSON number,
string or boolean, or an error written as its string, such as "#DIV/0!".

Contents. A JSON number, boolean or string is a constant; a string that starts with "=" is a formula.
A cell not in the map is empty. Constant text never starts with "#".

Formula grammar, lowest precedence first: comparison (= <> < <= > >=, chained left to right:
A1 > B1 > TRUE compares the first result with TRUE), & (text join), + and -, * and /, ^ (left to right), unary minus (binds tighter than ^:
-2^2 is 4), then primary: number (12, 0.5), "text" ("" inside is one quote mark), TRUE, FALSE, a cell
(A1), a range (A1:B3, only as a function argument), NAME(args separated by delimiters), (expr).
Function names are upper case.

Values and coercion:
1. As a number: a number is itself; TRUE is 1, FALSE is 0; empty is 0; text that is a number after
   trimming spaces (Python float() accepts it and it is not "nan" or "inf") is that number; other
   text gives #VALUE!.
2. As text: text is itself; a number with an integral value is written without a decimal point (3),
   any other number as Python repr(float(x)); TRUE and FALSE are "TRUE" and "FALSE"; empty is "".
3. As a boolean (IF, AND, OR, NOT): a boolean is itself; a number is TRUE unless 0; empty is FALSE;
   text "TRUE" or "FALSE" in any case is that boolean; other text gives #VALUE!.
4. + - * / ^ and unary minus convert both sides to numbers. / by 0 gives #DIV/0!. ^ with base 0 and a
   negative exponent gives #DIV/0!; a negative base with a non-integral exponent gives #NUM!. Any
   result that is not a finite number gives #NUM!.
5. & joins both sides as text.
6. Comparison gives a boolean. An empty side takes the other side's type (0, "" or FALSE; both empty
   are equal). Numbers compare by value; text compares case-insensitively; FALSE < TRUE; across
   types every number < every text < every boolean.
7. Errors: #DIV/0!, #VALUE!, #NUM!, #REF!, #NAME?, #CYCLE!. An operation or function that meets an
   error returns it. Operands and arguments are evaluated left to right, and each operand is also
   converted (rules 1-3, as the operation needs) before the next one is evaluated; the first error
   wins (unless a function below says otherwise). An unknown name or a wrong argument count gives
   its error before any argument is evaluated. A reference to an error cell gives that error.
8. Cycles: a cell whose formula refers (directly, or through other formulas, counting every reference
   in its text, inside ranges and in branches that are not taken) back to itself has the value
   #CYCLE!. Other cells are evaluated normally; reading a cycle cell reads #CYCLE!.

Final values: numbers are rounded with Python round(x, 9) and written as an integer when integral.
Empty passes through references, IF, IFERROR and INDEX as empty (so UPPER of it is ""); only a final
value that is empty is written as 0.

Functions. "Numbers in a range" means the cells of the range, row by row, whose value is a number
(text, booleans and empty cells are skipped; an error cell gives its error). A direct argument
that is not a range is converted with rule 1 (a reference to an empty cell counts as 0). Ranges are
allowed only in SUM, AVERAGE, MIN, MAX, COUNT, COUNTA, AND, OR, CONCAT and the range arguments of
SUMIF, COUNTIF and INDEX; anywhere else a range gives #VALUE!. Wrong argument counts give #VALUE!; an
unknown name gives #NAME?. Number arguments used as counts, positions or digits are truncated
toward zero.
- SUM, AVERAGE, MIN, MAX (one or more arguments): over numbers in ranges and direct arguments.
  AVERAGE of no numbers is #DIV/0!; MIN and MAX of no numbers are 0.
- COUNT: counts numbers in ranges and direct arguments that are numbers; it never returns an error
  (errors are skipped). COUNTA: counts non-empty cells and arguments, errors included (a direct
  argument whose value is empty is not counted).
- ROUND(x, digits): half away from zero (ROUND(2.5, 0) is 3, ROUND(-1.25, 1) is -1.3); digits may be
  negative and is truncated to an integer. INT(x): rounds down. ABS(x). MOD(a, b): a - b*INT(a/b),
  the sign of b; b = 0 gives #DIV/0!.
- IF(cond, then, [else]): evaluates only the chosen branch; a missing else gives FALSE.
- AND, OR (one or more arguments), NOT(x): as booleans (rule 3); a range adds its non-empty cells.
  With nothing left to test, AND is TRUE and OR is FALSE.
- IFERROR(x, alt): x, or alt when x is any error.
- LEN, UPPER, LOWER, TRIM (strip, and collapse inner runs of spaces to one): on the text of the
  argument. CONCAT(args): joins the text of every argument; a range adds its cells row by row.
- LEFT(text, [n]) and RIGHT(text, [n]): n characters, default 1; MID(text, start, n): from the
  1-based start. A negative n or a start below 1 gives #VALUE!.
- SUMIF(range, criteria, [sum_range]) and COUNTIF(range, criteria). criteria is a value; as text it
  may start with an operator: >=, <=, <>, >, <, =. The rest is the operand: a number if rule 1 can
  read it, else text. No operator means =. A number criteria value is "=" that number.
  Number operand: = and <> compare number cells by value (<> also matches every non-number cell);
  < <= > >= match only number cells. Text operand: = matches text cells equal case-insensitively,
  where * matches any run of characters, ? one character, and ~* ~? ~~ a literal * ? ~; <> matches
  every cell that = does not match; < <= > >= compare text cells case-insensitively. An empty
  operand ("" or "=") matches empty cells; "<>" matches non-empty cells. Boolean criteria match
  boolean cells equal to them. SUMIF adds the number cells of sum_range at the same offsets as the
  matching cells (default: the range itself); offsets that fall outside sum_range are skipped. Error cells in range never match; error cells added
  from sum_range give their error.
- INDEX(range, row, [col]): the cell at 1-based row and col (default 1) of the range; outside the
  range gives #REF!.
"""

COLS, ROWS = "ABCDEFGH", 15
ERRORS = {"#DIV/0!", "#VALUE!", "#NUM!", "#REF!", "#NAME?", "#CYCLE!"}


class Err(Exception):
    def __init__(self, code):
        self.code = code


class Empty:
    pass


EMPTY = Empty()
TOKEN = re.compile(r'\s*(?:([A-H](?:1[0-5]|[1-9])(?::[A-H](?:1[0-5]|[1-9]))?)(?![A-Z0-9(])|(\d+(?:\.\d+)?)|("(?:[^"]|"")*")|([A-Z][A-Z0-9]*)|(<>|<=|>=|[-+*/^&=<>(),]))')


def tokenize(text):
    out, pos = [], 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"bad token in {text!r} at {pos}")
        ref, num, string, name, op = m.groups()
        out.append(("ref", ref) if ref else ("num", float(num)) if num else ("str", string[1:-1].replace('""', '"')) if string
                   else ("name", name) if name else ("op", op))
        pos = m.end()
    return out


class Parser:
    def __init__(self, text):
        self.t, self.i = tokenize(text), 0

    def peek(self, *ops):
        tok = self.t[self.i] if self.i < len(self.t) else ("end", None)
        return tok if not ops or (tok[0] == "op" and tok[1] in ops) else None

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def parse(self):
        e = self.compare()
        if self.i != len(self.t):
            raise ValueError("trailing tokens")
        return e

    def binary(self, ops, lower):
        e = lower()
        while self.peek(*ops):
            e = ("bin", self.take()[1], e, lower())
        return e

    def compare(self):
        return self.binary(("=", "<>", "<", "<=", ">", ">="), self.join)

    def join(self):
        return self.binary(("&",), self.add)

    def add(self):
        return self.binary(("+", "-"), self.mul)

    def mul(self):
        return self.binary(("*", "/"), self.power)

    def power(self):
        return self.binary(("^",), self.unary)

    def unary(self):
        if self.peek("-"):
            self.take()
            return ("neg", self.unary())
        return self.primary()

    def primary(self):
        kind, value = self.take()
        if kind == "num":
            return ("lit", value)
        if kind == "str":
            return ("lit", value)
        if kind == "ref":
            return ("range", value) if ":" in value else ("ref", value)
        if kind == "name":
            if value in ("TRUE", "FALSE") and not self.peek("("):
                return ("lit", value == "TRUE")
            if not self.peek("("):
                raise ValueError(f"name {value} without call")
            self.take()
            args = []
            if not self.peek(")"):
                args.append(self.compare())
                while self.peek(","):
                    self.take()
                    args.append(self.compare())
            if not self.peek(")"):
                raise ValueError("missing )")
            self.take()
            return ("call", value, args)
        if (kind, value) == ("op", "("):
            e = self.compare()
            self.take()
            return e
        raise ValueError(f"unexpected {value!r}")


def cells_of(rng):
    a, b = rng.split(":")
    c1, r1, c2, r2 = COLS.index(a[0]), int(a[1:]), COLS.index(b[0]), int(b[1:])
    return [[f"{COLS[c]}{r}" for c in range(min(c1, c2), max(c1, c2) + 1)] for r in range(min(r1, r2), max(r1, r2) + 1)]


def refs(e):
    if e[0] == "ref":
        return {e[1]}
    if e[0] == "range":
        return {c for row in cells_of(e[1]) for c in row}
    if e[0] == "bin":
        return refs(e[2]) | refs(e[3])
    if e[0] == "neg":
        return refs(e[1])
    if e[0] == "call":
        return set().union(*map(refs, e[2])) if e[2] else set()
    return set()


def finite(x):
    if math.isinf(x) or math.isnan(x):
        raise Err("#NUM!")
    return x


def to_number(v):
    if isinstance(v, Err):
        raise v
    if v is EMPTY:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    text = v.strip()
    try:
        x = float(text)
    except ValueError:
        raise Err("#VALUE!")
    if math.isnan(x) or math.isinf(x):
        raise Err("#VALUE!")
    return x


def to_text(v):
    if isinstance(v, Err):
        raise v
    if v is EMPTY:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else repr(float(v))
    return v


def to_bool(v):
    if isinstance(v, Err):
        raise v
    if v is EMPTY:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if v.upper() in ("TRUE", "FALSE"):
        return v.upper() == "TRUE"
    raise Err("#VALUE!")


def rank(v):
    return 1 if isinstance(v, bool) else 0 if isinstance(v, (int, float)) else 0.5


def compare(op, a, b):
    for v in (a, b):
        if isinstance(v, Err):
            raise v
    if a is EMPTY and b is EMPTY:
        a = b = 0.0
    elif a is EMPTY:
        a = False if isinstance(b, bool) else 0.0 if isinstance(b, (int, float)) else ""
    elif b is EMPTY:
        b = False if isinstance(a, bool) else 0.0 if isinstance(a, (int, float)) else ""
    ka, kb = rank(a), rank(b)
    if ka != kb:
        x, y = ka, kb
    elif isinstance(a, str):
        x, y = a.lower(), b.lower()
    else:
        x, y = float(a), float(b)
    return {"=": x == y, "<>": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]


def criteria_match(criteria, v):
    if isinstance(v, Err):
        return False
    if isinstance(criteria, bool):
        return isinstance(v, bool) and v == criteria
    if isinstance(criteria, (int, float)):
        op, operand = "=", float(criteria)
    else:
        text = to_text(criteria)
        m = re.match(r"(>=|<=|<>|>|<|=)?(.*)$", text, re.S)
        op, rest = m.group(1) or "=", m.group(2)
        if rest == "":
            return (v is EMPTY) if op == "=" else (v is not EMPTY) if op == "<>" else False
        try:
            operand = to_number(rest)
        except Err:
            operand = rest
    if isinstance(operand, float):
        number = isinstance(v, (int, float)) and not isinstance(v, bool)
        if op == "=":
            return number and float(v) == operand
        if op == "<>":
            return not (number and float(v) == operand)
        return number and {"<": float(v) < operand, "<=": float(v) <= operand, ">": float(v) > operand, ">=": float(v) >= operand}[op]
    if op in ("=", "<>"):
        pattern, i = "", 0
        while i < len(operand):
            c = operand[i]
            if c == "~" and i + 1 < len(operand) and operand[i + 1] in "*?~":
                pattern += re.escape(operand[i + 1])
                i += 2
                continue
            pattern += ".*" if c == "*" else "." if c == "?" else re.escape(c)
            i += 1
        hit = isinstance(v, str) and re.fullmatch(pattern, v, re.S | re.I) is not None
        return hit if op == "=" else not hit
    if not isinstance(v, str):
        return False
    a, b = v.lower(), operand.lower()
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


class Sheet:
    def __init__(self, cells):
        self.raw = cells
        self.ast, self.values = {}, {}
        for name, content in cells.items():
            if isinstance(content, str) and content.startswith("="):
                self.ast[name] = Parser(content[1:]).parse()
        self.cycle = self.cycles()

    def cycles(self):
        graph = {c: refs(e) & set(self.ast) for c, e in self.ast.items()}
        index, low, stack, on, out, counter = {}, {}, [], set(), set(), [0]

        def visit(v):  # Tarjan's strongly connected components
            index[v] = low[v] = counter[0]
            counter[0] += 1
            stack.append(v)
            on.add(v)
            for w in graph[v]:
                if w not in index:
                    visit(w)
                    low[v] = min(low[v], low[w])
                elif w in on:
                    low[v] = min(low[v], index[w])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1 or v in graph[v]:
                    out.update(comp)
        for v in graph:
            if v not in index:
                visit(v)
        return out

    def value(self, name):
        if name in self.values:
            return self.values[name]
        if name in self.cycle:
            result = Err("#CYCLE!")
        elif name in self.ast:
            try:
                result = self.eval(self.ast[name])
            except Err as error:
                result = error
        else:
            result = self.raw.get(name, EMPTY)
            if isinstance(result, int) and not isinstance(result, bool):
                result = float(result)
        self.values[name] = result
        return result

    def eval(self, e):
        kind = e[0]
        if kind == "lit":
            return e[1]
        if kind == "ref":
            v = self.value(e[1])
            if isinstance(v, Err):
                raise v
            return v
        if kind == "range":
            raise Err("#VALUE!")
        if kind == "neg":
            return -to_number(self.eval(e[1]))
        if kind == "bin":
            op, a = e[1], self.eval(e[2])
            if isinstance(a, Err):
                raise a
            if op == "&":
                a = to_text(a)
                return a + to_text(self.eval(e[3]))
            if op in ("=", "<>", "<", "<=", ">", ">="):
                return compare(op, a, self.eval(e[3]))
            x = to_number(a)  # converted before the right side is evaluated (rule 7)
            y = to_number(self.eval(e[3]))
            if op == "+":
                return finite(x + y)
            if op == "-":
                return finite(x - y)
            if op == "*":
                return finite(x * y)
            if op == "/":
                if y == 0:
                    raise Err("#DIV/0!")
                return finite(x / y)
            if x == 0 and y < 0:
                raise Err("#DIV/0!")
            if x < 0 and not y.is_integer():
                raise Err("#NUM!")
            try:
                return finite(x ** y)
            except OverflowError:
                raise Err("#NUM!")
        return self.call(e[1], e[2])

    def range_values(self, arg):
        return [self.value(c) for row in cells_of(arg[1]) for c in row]

    def numbers(self, args):
        out = []
        for arg in args:
            if arg[0] == "range":
                for v in self.range_values(arg):
                    if isinstance(v, Err):
                        raise v
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        out.append(float(v))
            else:
                out.append(to_number(self.eval(arg)))
        return out

    def call(self, name, args):
        n = len(args)
        arity = {"SUM": (1, 99), "AVERAGE": (1, 99), "MIN": (1, 99), "MAX": (1, 99), "COUNT": (1, 99), "COUNTA": (1, 99),
                 "ROUND": (2, 2), "INT": (1, 1), "ABS": (1, 1), "MOD": (2, 2), "IF": (2, 3), "AND": (1, 99), "OR": (1, 99),
                 "NOT": (1, 1), "IFERROR": (2, 2), "LEN": (1, 1), "UPPER": (1, 1), "LOWER": (1, 1), "TRIM": (1, 1),
                 "CONCAT": (1, 99), "LEFT": (1, 2), "RIGHT": (1, 2), "MID": (3, 3), "SUMIF": (2, 3), "COUNTIF": (2, 2),
                 "INDEX": (2, 3)}
        if name not in arity:
            raise Err("#NAME?")
        low, high = arity[name]
        if not low <= n <= high:
            raise Err("#VALUE!")
        ranged = {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTA", "AND", "OR", "CONCAT", "SUMIF", "COUNTIF", "INDEX"}
        if name not in ranged and any(a[0] == "range" for a in args):
            raise Err("#VALUE!")
        if name in ("SUM", "AVERAGE", "MIN", "MAX"):
            values = self.numbers(args)
            if name == "SUM":
                return sum(values)
            if name == "AVERAGE":
                if not values:
                    raise Err("#DIV/0!")
                return sum(values) / len(values)
            return (min if name == "MIN" else max)(values) if values else 0.0
        if name in ("COUNT", "COUNTA"):
            total = 0
            for arg in args:
                values = self.range_values(arg) if arg[0] == "range" else [self.safe(arg)]
                for v in values:
                    if name == "COUNT":
                        total += isinstance(v, (int, float)) and not isinstance(v, bool)
                    else:
                        total += v is not EMPTY
            return float(total)
        if name == "IF":
            if to_bool(self.eval(args[0])):
                return self.eval(args[1])
            return self.eval(args[2]) if n == 3 else False
        if name == "IFERROR":
            v = self.safe(args[0])
            return self.eval(args[1]) if isinstance(v, Err) else v
        if name in ("AND", "OR"):
            values = []
            for arg in args:
                if arg[0] == "range":
                    for v in self.range_values(arg):
                        if isinstance(v, Err):
                            raise v
                        if v is not EMPTY:
                            values.append(to_bool(v))
                else:
                    values.append(to_bool(self.eval(arg)))
            return all(values) if name == "AND" else any(values)
        if name == "CONCAT":
            parts = []
            for arg in args:
                parts += [to_text(v) for v in self.range_values(arg)] if arg[0] == "range" else [to_text(self.eval(arg))]
            return "".join(parts)
        if name in ("SUMIF", "COUNTIF"):
            if args[0][0] != "range" or (n == 3 and args[2][0] != "range"):
                raise Err("#VALUE!")
            criteria = self.eval(args[1])
            if isinstance(criteria, Err):
                raise criteria
            grid = cells_of(args[0][1])
            hits = [(r, c) for r, row in enumerate(grid) for c, cell in enumerate(row) if criteria_match(criteria, self.value(cell))]
            if name == "COUNTIF":
                return float(len(hits))
            sums = cells_of(args[2][1]) if n == 3 else grid
            total = 0.0
            for r, c in hits:
                if r >= len(sums) or c >= len(sums[0]):
                    continue  # only cells inside sum_range are read
                v = self.value(sums[r][c])
                if isinstance(v, Err):
                    raise v
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    total += float(v)
            return total
        if name == "INDEX":
            if args[0][0] != "range":
                raise Err("#VALUE!")
            grid = cells_of(args[0][1])
            row = int(to_number(self.eval(args[1])))
            col = int(to_number(self.eval(args[2]))) if n == 3 else 1
            if not (1 <= row <= len(grid) and 1 <= col <= len(grid[0])):
                raise Err("#REF!")
            v = self.value(grid[row - 1][col - 1])
            if isinstance(v, Err):
                raise v
            return v
        values = [self.eval(a) for a in args]
        for v in values:
            if isinstance(v, Err):
                raise v
        if name == "ROUND":
            x, digits = to_number(values[0]), int(to_number(values[1]))
            with localcontext() as ctx:
                ctx.prec = 1000  # enough digits for any finite float
                return float(Decimal(repr(x)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))
        if name == "INT":
            return float(math.floor(to_number(values[0])))
        if name == "ABS":
            return abs(to_number(values[0]))
        if name == "MOD":
            a, b = to_number(values[0]), to_number(values[1])
            if b == 0:
                raise Err("#DIV/0!")
            return a - b * math.floor(a / b)
        if name == "NOT":
            return not to_bool(values[0])
        text = to_text(values[0])
        if name == "LEN":
            return float(len(text))
        if name == "UPPER":
            return text.upper()
        if name == "LOWER":
            return text.lower()
        if name == "TRIM":
            return re.sub(" +", " ", text.strip(" "))
        if name in ("LEFT", "RIGHT"):
            k = int(to_number(values[1])) if n == 2 else 1
            if k < 0:
                raise Err("#VALUE!")
            return text[:k] if name == "LEFT" else (text[-k:] if k else "")
        start, k = int(to_number(values[1])), int(to_number(values[2]))
        if start < 1 or k < 0:
            raise Err("#VALUE!")
        return text[start - 1:start - 1 + k]

    def safe(self, arg):
        try:
            return self.eval(arg)
        except Err as error:
            return error


def final(v):
    if isinstance(v, Err):
        return v.code
    if v is EMPTY:
        return 0
    if isinstance(v, float):
        x = round(v, 9)
        return int(x) if x.is_integer() else x
    return v


def solve(item):
    sheet = Sheet(item["cells"])
    return {name: json.dumps(final(sheet.value(name))) for name in sorted(sheet.ast)}


# ---------- generator ----------

WORDS = ["apple", "Pear", "plum pie", " 42 ", "1e3", "abc", "x*y", "50%", "TRUE", "q?z", "", "  two  spaces  ", "o~k"]


POOL = {"constants": [], "formulas": []}  # what a formula of the item being generated reads


def cell_name(rng, rows=ROWS):
    r = rng.random()
    if POOL["constants"] and r < 0.7:
        return rng.choice(POOL["constants"])
    if POOL["formulas"] and r < 0.93:
        return rng.choice(POOL["formulas"])
    return f"{rng.choice(COLS)}{rng.randint(1, rows)}"  # any cell: empty, or now and then a later formula (a cycle)


def area(rng):
    c1, c2 = sorted(rng.sample(range(len(COLS)), 2)) if rng.random() < 0.5 else [rng.randrange(len(COLS))] * 2
    r1 = rng.randint(1, 8)
    return f"{COLS[c1]}{r1}:{COLS[c2]}{min(10, r1 + rng.randint(0, 5))}"  # inside the constant rows


def constant(rng):
    r = rng.random()
    if r < 0.6:
        return rng.choice([rng.randint(-20, 99), round(rng.uniform(-10, 10), 2), 0, 2.5, -1.25])
    if r < 0.82:
        return rng.choice(WORDS)
    return rng.random() < 0.5


def operand(rng, depth):
    r = rng.random()
    if depth > 2 or r < 0.45:
        return cell_name(rng)
    if r < 0.6:
        return rng.choice(["3", "0", "0.5", "2", "7", "1.5", '"abc"', '" 7 "', '""', "TRUE", "FALSE", '"say ""hi"""'])
    return formula(rng, depth + 1)


def formula(rng, depth=0):
    o = lambda: operand(rng, depth)
    r = rng.randrange(14)
    if r == 0:
        return f"{o()} {rng.choice(['+', '-', '*', '/', '^'])} {o()}"
    if r == 1:
        return f"-{o()}^2" if rng.random() < 0.5 else f"({o()} + {o()}) * {o()}"
    if r == 2:
        return f"{o()} & {o()}"
    if r == 3:
        return f"{o()} {rng.choice(['=', '<>', '<', '<=', '>', '>='])} {o()}"
    if r == 4:
        return f"{rng.choice(['SUM', 'AVERAGE', 'MIN', 'MAX', 'COUNT', 'COUNTA'])}({area(rng)}{', ' + o() if rng.random() < 0.4 else ''})"
    if r == 5:
        return f"IF({o()}, {o()}{', ' + o() if rng.random() < 0.7 else ''})"
    if r == 6:
        return f"IFERROR({o()}, {o()})"
    if r == 7:
        return f"{rng.choice(['AND', 'OR'])}({o()}, {o()})" if rng.random() < 0.7 else f"NOT({o()})"
    if r == 8:
        return f"{rng.choice(['LEN', 'UPPER', 'LOWER', 'TRIM'])}({o()})"
    if r == 9:
        return rng.choice([f"LEFT({o()}, {rng.randint(0, 3)})", f"RIGHT({o()})", f"MID({o()}, {rng.randint(0, 3)}, 2)", f"CONCAT({area(rng)})"])
    if r == 10:
        crit = rng.choice(['">0"', '"<=2.5"', '"<>apple"', '"p*"', '"?bc"', '"x~*y"', '""', '"<>"', '"=TRUE"', "3", "TRUE", '">=b"', '"42"'])
        return f"SUMIF({area(rng)}, {crit}{', ' + area(rng) if rng.random() < 0.4 else ''})" if rng.random() < 0.5 else f"COUNTIF({area(rng)}, {crit})"
    if r == 11:
        return f"ROUND({o()}, {rng.choice(['0', '1', '-1', '2'])})" if rng.random() < 0.6 else f"{rng.choice(['INT', 'ABS'])}({o()})"
    if r == 12:
        return f"MOD({o()}, {rng.choice(['3', '-3', '0', o()])})"
    if rng.random() < 0.93:
        box = area(rng)
        grid = cells_of(box)
        row = rng.randint(1, len(grid) + (1 if rng.random() < 0.15 else 0))
        col = f", {rng.randint(1, len(grid[0]))}" if rng.random() < 0.5 else ""
        return f"INDEX({box}, {row}{col})"
    return f"{rng.choice(['VLOOKUP', 'SQRT'])}({o()})"


def item(rng, index):
    cells = {}
    POOL["constants"], POOL["formulas"] = [], []
    for _ in range(rng.randint(14, 26)):
        cells[cell_name(rng, 10)] = constant(rng)  # constants in rows 1-10
    POOL["constants"] = list(cells)
    free = [f"{c}{r}" for r in range(11, ROWS + 1) for c in COLS]
    for target in rng.sample(free, rng.randint(10, 18)):  # formulas in rows 11-15
        cells[target] = "=" + formula(rng)
        POOL["formulas"].append(target)
    POOL["constants"], POOL["formulas"] = [], []
    return {"cells": cells}
