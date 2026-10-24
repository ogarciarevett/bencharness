"""query task: run a small SQL-like language over the tables given in each item."""
import json
import re

RULES = """\
Each item has "tables" (name -> {"columns": [...], "rows": [[...], ...]}) and one "query". Run the query
and return {"columns": json.dumps(output column names), "count": str(number of output rows),
"rows": json.dumps(output rows)}. json.dumps uses default settings. Values are JSON numbers, strings,
true/false and null.

Grammar (keywords are upper case, identifiers lower case, no semicolon):
  SELECT [DISTINCT] item {, item} FROM table [alias] [(JOIN | LEFT JOIN) table [alias] ON ref = ref]
  [WHERE expr] [GROUP BY ref {, ref}] [HAVING expr] [ORDER BY key [ASC | DESC] {, key [ASC | DESC]}]
  [LIMIT n [OFFSET m]]
  item: * | expr [AS name]        ref: name | alias.name        key: ref | output name | position
  expr precedence, lowest first: OR, AND, NOT, comparison (= != < <= > >= LIKE, IS [NOT] NULL,
  [NOT] IN (literal {, literal})), + and -, * / %, unary minus, then primary: integer, decimal,
  'string' ('' inside is one quote), NULL, TRUE, FALSE, ref, function call, (expr).
  Functions: COUNT(*), COUNT(expr), COUNT(DISTINCT expr), SUM, MIN, MAX, AVG, LOWER, UPPER, LENGTH,
  COALESCE(expr {, expr}), ABS.

Semantics:
1. FROM order is the input order. JOIN keeps, for each left row in order, the matching right rows
   in order. LEFT JOIN also keeps a left row with no match, with null for every right column. A row
   matches when ON compares true (null never matches). A ref "name" means the first table in the
   FROM clause that has that column; "alias.name" the given table (an unaliased table's alias is its
   own name). "*" is every column of the FROM table, then of the joined table.
2. Null logic is three-valued. A comparison or arithmetic with null is null. NOT null is null.
   AND: false if either side is false, else null if either is null. OR: true if either is true, else
   null if either is null. WHERE and HAVING keep a row only when the expression is true. Operands of
   AND, OR and NOT are always booleans or null in this suite.
3. Comparing a number with a string, or a boolean with anything but a boolean, is null. Booleans
   support only = and !=. Strings compare by Unicode code point; numbers by value (1 = 1.0).
4. x IN (a, b) is true if x = some element is true, else null if any of those comparisons is null
   (for example x or an element is null, or a number meets a string), else false.
   NOT IN is the NOT of that. x IS NULL and x IS NOT NULL are never null.
5. LIKE: % matches any run of characters, _ exactly one; \\% \\_ and \\\\ match a literal %, _ and \\.
   Case-sensitive, whole string. Non-string operands give null.
6. Arithmetic: integers stay integers. / between two integers divides and truncates toward zero;
   % takes the sign of the left operand (truncated division). Division or % by zero is null. Any
   decimal operand gives a decimal. + - * / % on a string or boolean is null.
7. Aggregates ignore nulls. COUNT(*) counts rows. COUNT(DISTINCT x) counts distinct non-null values.
   SUM of integers is an integer. SUM, MIN, MAX and AVG of no values are null. AVG is a decimal.
   MIN and MAX compare as in rule 3 (all values of a column share one type).
8. With GROUP BY, rows group by the listed refs (nulls form one group), groups in order of first
   appearance; each select item is a group ref or contains aggregates. Without GROUP BY, a query
   with an aggregate in SELECT or HAVING forms exactly one group, even over zero rows.
9. LOWER, UPPER, LENGTH of null are null; on a non-string they are null. ABS of a non-number is null.
   COALESCE returns the first non-null argument, else null.
10. Output names: AS name if given, else the column name for a plain ref. Every other item has AS.
11. DISTINCT drops output rows equal to an earlier one (null equals null here), keeping the first.
12. ORDER BY is stable. A key that is a positive integer n is output column n. A key that is an
    output name sorts by that output column; otherwise by the ref, evaluated on the row (grouped
    queries order by output columns only). Values sort numbers by value, strings by code point,
    booleans false before true, and across types booleans before numbers before strings; null sorts
    after every value with ASC and before with DESC.
13. Order of steps: DISTINCT, then ORDER BY, then LIMIT n OFFSET m (skip m rows, keep at most n).
14. Output decimals are rounded with Python round(x, 4); integers stay integers.
"""

TOKEN = re.compile(r"\s*(?:(\d+\.\d+|\d+)|('(?:[^']|'')*')|([A-Za-z_][A-Za-z_0-9]*)|(<=|>=|!=|[=<>*/%+\-(),.]))")
KEYWORDS = {"SELECT", "DISTINCT", "FROM", "JOIN", "LEFT", "ON", "WHERE", "GROUP", "BY", "HAVING", "ORDER", "ASC", "DESC",
            "LIMIT", "OFFSET", "AS", "AND", "OR", "NOT", "LIKE", "IS", "NULL", "IN", "TRUE", "FALSE"}
AGGREGATES = {"COUNT", "SUM", "MIN", "MAX", "AVG"}


def tokenize(text):
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"bad token at {pos}")
        num, string, word, op = m.groups()
        if num:
            out.append(("num", float(num) if "." in num else int(num)))
        elif string:
            out.append(("str", string[1:-1].replace("''", "'")))
        elif word:
            out.append(("kw", word) if word in KEYWORDS or word.upper() == word and word.isalpha() else ("id", word))
        else:
            out.append(("op", op))
        pos = m.end()
    return out


class Parser:
    def __init__(self, text):
        self.t, self.i = tokenize(text), 0

    def peek(self, *values):
        tok = self.t[self.i] if self.i < len(self.t) else (None, None)
        return tok if not values or tok[1] in values else None

    def take(self, *values):
        tok = self.peek(*values)
        if tok is None or tok[0] is None:
            raise ValueError(f"expected {values}")
        self.i += 1
        return tok

    def ref(self):
        name = self.take()[1]
        if self.peek("."):
            self.take(".")
            return ("ref", name, self.take()[1])
        return ("ref", None, name)

    def query(self):
        q = {"distinct": False, "join": None, "where": None, "group": [], "having": None, "order": [], "limit": None, "offset": 0}
        self.take("SELECT")
        if self.peek("DISTINCT"):
            self.take("DISTINCT")
            q["distinct"] = True
        q["items"] = [self.item()]
        while self.peek(","):
            self.take(",")
            q["items"].append(self.item())
        self.take("FROM")
        q["from"] = self.table()
        if self.peek("JOIN", "LEFT"):
            left = self.take()[1] == "LEFT"
            if left:
                self.take("JOIN")
            table = self.table()
            self.take("ON")
            a = self.ref()
            self.take("=")
            q["join"] = {"left": left, "table": table, "on": (a, self.ref())}
        if self.peek("WHERE"):
            self.take("WHERE")
            q["where"] = self.expr()
        if self.peek("GROUP"):
            self.take("GROUP")
            self.take("BY")
            q["group"] = [self.ref()]
            while self.peek(","):
                self.take(",")
                q["group"].append(self.ref())
        if self.peek("HAVING"):
            self.take("HAVING")
            q["having"] = self.expr()
        if self.peek("ORDER"):
            self.take("ORDER")
            self.take("BY")
            while True:
                key = ("pos", self.take()[1]) if self.peek()[0] == "num" else self.ref()
                desc = bool(self.peek("ASC", "DESC")) and self.take()[1] == "DESC"
                q["order"].append((key, desc))
                if not self.peek(","):
                    break
                self.take(",")
        if self.peek("LIMIT"):
            self.take("LIMIT")
            q["limit"] = self.take()[1]
            if self.peek("OFFSET"):
                self.take("OFFSET")
                q["offset"] = self.take()[1]
        if self.i != len(self.t):
            raise ValueError("trailing tokens")
        return q

    def table(self):
        name = self.take()[1]
        alias = self.take()[1] if self.peek() and self.peek()[0] == "id" else name
        return name, alias

    def item(self):
        if self.peek("*"):
            self.take("*")
            return ("star", None)
        e = self.expr()
        if self.peek("AS"):
            self.take("AS")
            return (e, self.take()[1])
        return (e, e[2] if e[0] == "ref" else None)

    def expr(self):
        e = self.conj()
        while self.peek("OR"):
            self.take("OR")
            e = ("or", e, self.conj())
        return e

    def conj(self):
        e = self.neg()
        while self.peek("AND"):
            self.take("AND")
            e = ("and", e, self.neg())
        return e

    def neg(self):
        if self.peek("NOT"):
            self.take("NOT")
            return ("not", self.neg())
        return self.cmp()

    def cmp(self):
        e = self.add()
        if self.peek("=", "!=", "<", "<=", ">", ">=", "LIKE"):
            return ("cmp", self.take()[1], e, self.add())
        if self.peek("IS"):
            self.take("IS")
            negate = bool(self.peek("NOT")) and bool(self.take("NOT"))
            self.take("NULL")
            return ("isnull", negate, e)
        negate = False
        if self.peek("NOT"):
            self.take("NOT")
            negate = True
        if self.peek("IN"):
            self.take("IN")
            self.take("(")
            items = [self.add()]
            while self.peek(","):
                self.take(",")
                items.append(self.add())
            self.take(")")
            return ("not", ("in", e, items)) if negate else ("in", e, items)
        if negate:
            raise ValueError("NOT without IN")
        return e

    def add(self):
        e = self.mul()
        while self.peek("+", "-"):
            e = ("arith", self.take()[1], e, self.mul())
        return e

    def mul(self):
        e = self.unary()
        while self.peek("*", "/", "%"):
            e = ("arith", self.take()[1], e, self.unary())
        return e

    def unary(self):
        if self.peek("-"):
            self.take("-")
            return ("arith", "-", ("lit", 0), self.unary())
        return self.primary()

    def primary(self):
        kind, value = self.peek()
        if kind == "num" or kind == "str":
            self.take()
            return ("lit", value)
        if value in ("NULL", "TRUE", "FALSE"):
            self.take()
            return ("lit", {"NULL": None, "TRUE": True, "FALSE": False}[value])
        if value == "(":
            self.take("(")
            e = self.expr()
            self.take(")")
            return e
        if kind == "kw" or (kind == "id" and self.t[self.i + 1:self.i + 2] == [("op", "(")]):
            name = self.take()[1]
            self.take("(")
            if name == "COUNT" and self.peek("*"):
                self.take("*")
                self.take(")")
                return ("agg", "COUNT*", None, False)
            distinct = bool(self.peek("DISTINCT")) and bool(self.take("DISTINCT"))
            args = [self.expr()]
            while self.peek(","):
                self.take(",")
                args.append(self.expr())
            self.take(")")
            if name in AGGREGATES:
                return ("agg", name, args[0], distinct)
            return ("call", name, args)
        return self.ref()


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def compare(op, a, b):
    if a is None or b is None:
        return None
    if isinstance(a, bool) or isinstance(b, bool):
        if not (isinstance(a, bool) and isinstance(b, bool)) or op not in ("=", "!="):
            return None
    elif is_num(a) != is_num(b):
        return None
    return {"=": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def like(value, pattern):
    if not isinstance(value, str) or not isinstance(pattern, str):
        return None
    regex, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            regex += re.escape(pattern[i + 1])
            i += 2
            continue
        regex += ".*" if c == "%" else "." if c == "_" else re.escape(c)
        i += 1
    return re.fullmatch(regex, value, re.S) is not None


def arith(op, a, b):
    if not is_num(a) or not is_num(b):
        return None
    if op in ("/", "%") and b == 0:
        return None
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if isinstance(a, int) and isinstance(b, int):
        q = abs(a) // abs(b) * (1 if (a >= 0) == (b >= 0) else -1)
        return q if op == "/" else a - b * q
    return a / b if op == "/" else a - b * int(a / b)


class Env:
    def __init__(self, tables, q):
        self.order = [q["from"]] + ([q["join"]["table"]] if q["join"] else [])
        self.columns = {alias: tables[name]["columns"] for name, alias in self.order}

    def lookup(self, row, ref):
        _, alias, name = ref
        if alias is None:
            alias = next(a for _, a in self.order if name in self.columns[a])
        return row[alias][name]


def evaluate(e, row, env, group=None):
    kind = e[0]
    if kind == "lit":
        return e[1]
    if kind == "ref":
        return env.lookup(row, e)
    if kind == "agg":
        return aggregate(e, group, env)
    if kind == "or" or kind == "and":
        a, b = evaluate(e[1], row, env, group), evaluate(e[2], row, env, group)
        stop = kind == "or"
        if a is stop or b is stop:
            return stop
        return None if a is None or b is None else not stop
    if kind == "not":
        v = evaluate(e[1], row, env, group)
        return None if v is None else not v
    if kind == "cmp":
        a, b = evaluate(e[2], row, env, group), evaluate(e[3], row, env, group)
        return like(a, b) if e[1] == "LIKE" else compare(e[1], a, b)
    if kind == "isnull":
        v = evaluate(e[2], row, env, group)
        return (v is not None) if e[1] else (v is None)
    if kind == "in":
        x = evaluate(e[1], row, env, group)
        results = [compare("=", x, evaluate(i, row, env, group)) for i in e[2]]
        return True if True in results else None if None in results else False
    if kind == "arith":
        return arith(e[1], evaluate(e[2], row, env, group), evaluate(e[3], row, env, group))
    if kind == "call":
        args = [evaluate(a, row, env, group) for a in e[2]]
        name, v = e[1], args[0]
        if name == "COALESCE":
            return next((a for a in args if a is not None), None)
        if name == "ABS":
            return abs(v) if is_num(v) else None
        if not isinstance(v, str):
            return None
        return {"LOWER": v.lower(), "UPPER": v.upper(), "LENGTH": len(v)}[name]
    raise ValueError(kind)


def aggregate(e, group, env):
    _, name, arg, distinct = e
    if name == "COUNT*":
        return len(group)
    values = [v for v in (evaluate(arg, r, env) for r in group) if v is not None]
    if distinct:
        values = list(dict.fromkeys(values))
    if name == "COUNT":
        return len(values)
    if not values:
        return None
    if name == "SUM":
        return sum(values)
    if name == "AVG":
        return sum(values) / len(values)
    return (min if name == "MIN" else max)(values)


def has_agg(e):
    return isinstance(e, tuple) and (e[0] == "agg" or any(has_agg(x) for x in e[1:] if isinstance(x, (tuple, list)))) \
        or isinstance(e, list) and any(has_agg(x) for x in e)


def sort_key(v):
    # Null ranks above every value: last with ASC, first with DESC (the sort is reversed for DESC,
    # which Python keeps stable).
    if v is None:
        return (2,)
    return (1, (0, v) if isinstance(v, bool) else (1, v) if is_num(v) else (2, v))


def run(item):
    q = Parser(item["query"]).query()
    tables, env = item["tables"], None
    env = Env(tables, q)
    name, alias = q["from"]
    rows = [{alias: dict(zip(tables[name]["columns"], r))} for r in tables[name]["rows"]]
    if q["join"]:
        jname, jalias = q["join"]["table"]
        right = [dict(zip(tables[jname]["columns"], r)) for r in tables[jname]["rows"]]
        joined = []
        for row in rows:
            matches = [{**row, jalias: r} for r in right if compare("=", *(evaluate(ref, {**row, jalias: r}, env) for ref in q["join"]["on"])) is True]
            joined += matches or ([{**row, jalias: {c: None for c in tables[jname]["columns"]}}] if q["join"]["left"] else [])
        rows = joined
    if q["where"]:
        rows = [r for r in rows if evaluate(q["where"], r, env) is True]
    items = []
    for e, out_name in q["items"]:
        if e == "star":
            items += [(("ref", a, c), c) for _, a in env.order for c in env.columns[a]]
        else:
            items.append((e, out_name))
    grouped = bool(q["group"]) or any(has_agg(e) for e, _ in items) or (q["having"] is not None and has_agg(q["having"]))
    if grouped:
        groups = {}
        for r in rows:
            key = json.dumps([env.lookup(r, g) for g in q["group"]])
            groups.setdefault(key, []).append(r)
        if not q["group"] and not groups:
            groups[""] = []
        out = []
        for members in groups.values():
            first = members[0] if members else None
            if q["having"] is not None and evaluate(q["having"], first, env, members) is not True:
                continue
            out.append(([evaluate(e, first, env, members) for e, _ in items], first))
    else:
        out = [([evaluate(e, r, env) for e, _ in items], r) for r in rows]
    names = [n for _, n in items]
    if q["distinct"]:
        seen, kept = set(), []
        for values, r in out:
            key = json.dumps(values)
            if key not in seen:
                seen.add(key)
                kept.append((values, r))
        out = kept
    for key, desc in reversed(q["order"]):
        if key[0] == "pos":
            pick = lambda pair, n=key[1] - 1: pair[0][n]
        elif key[1] is None and key[2] in names:
            pick = lambda pair, n=names.index(key[2]): pair[0][n]
        else:
            pick = lambda pair, ref=key: env.lookup(pair[1], ref)
        out.sort(key=lambda pair: sort_key(pick(pair)), reverse=desc)
    out = out[q["offset"]:]
    if q["limit"] is not None:
        out = out[:q["limit"]]
    rows_out = [[round(v, 4) if isinstance(v, float) else v for v in values] for values, _ in out]
    return {"columns": json.dumps(names), "count": str(len(rows_out)), "rows": json.dumps(rows_out)}


# ---------- generator ----------

NAMES = ["ann", "bo", "cy", "dee", "eli", "fay", "gus", "ivy", "jo", "kai", "lu", "max", "ann_lee", "50%off", "o'neil", "Zed", "back\\slash"]
TEAMS = ["core", "data", "ops", "web"]
CITIES = ["Oslo", "Lima", "Kyiv", "Pune"]
LIKES = ["%a%", "_o", "ann\\_%", "%\\%%", "__", "Z%", "%\\\\%", "%e%"]  # holds the LIKE escapes for _, % and backslash


def maybe(rng, value, chance=0.15):
    return None if rng.random() < chance else value


def tables(rng):
    staff = [[i + 1, rng.choice(NAMES), maybe(rng, rng.choice(TEAMS + ["lab"])), maybe(rng, rng.randint(30, 120) * 1000),
              maybe(rng, round(rng.uniform(1, 5), 2)), rng.random() < 0.6] for i in range(0 if rng.random() < 0.04 else rng.randint(6, 16))]
    teams = [[t, rng.choice(CITIES), maybe(rng, rng.randint(1, 9) * 100000)] for t in rng.sample(TEAMS, rng.randint(1, 4))]
    if rng.random() < 0.3:
        teams.append([None, rng.choice(CITIES), 0])
    return {"staff": {"columns": ["id", "name", "team", "salary", "rating", "active"], "rows": staff},
            "teams": {"columns": ["team", "city", "budget"], "rows": teams}}


def quote(text):
    return "'" + text.replace("'", "''") + "'"


def condition(rng, depth=0):
    r = rng.random()
    if depth < 2 and r < 0.25:
        return f"({condition(rng, depth + 1)} {rng.choice(['AND', 'OR'])} {condition(rng, depth + 1)})"
    if depth < 2 and r < 0.32:
        return f"NOT {condition(rng, depth + 1)}"
    pick = rng.choices(range(9), [3, 2, 3, 2, 3, 2, 1, 2, 2])[0]
    if pick == 0:
        return f"salary {rng.choice(['>', '>=', '<', '<=', '=', '!='])} {rng.randint(30, 120) * 1000}"
    if pick == 1:
        return f"rating {rng.choice(['>', '<'])} {rng.choice(['2.5', '3', '4.25'])}"
    if pick == 2:
        return f"team {'NOT ' if rng.random() < 0.4 else ''}IN ({', '.join(rng.sample([quote(t) for t in TEAMS] + ['NULL'], 2))})"
    if pick == 3:
        return f"{rng.choice(['team', 'salary', 'rating'])} IS {'NOT ' if rng.random() < 0.5 else ''}NULL"
    if pick == 4:
        return "name LIKE " + quote(rng.choice(LIKES))
    if pick == 5:
        return f"active = {rng.choice(['TRUE', 'FALSE'])}"
    if pick == 6:
        return f"salary {rng.choice(['=', '>'])} {quote('x')}"  # a number against a string is null
    if pick == 7:
        return f"salary % {rng.choice([3, 7, 0])} = {rng.choice([0, 1])}"
    return f"LENGTH(name) {rng.choice(['>', '<='])} {rng.randint(2, 4)}"


def query(rng):
    family = rng.randrange(5)
    limit = f" LIMIT {rng.randint(1, 5)}" + (f" OFFSET {rng.randint(0, 3)}" if rng.random() < 0.4 else "") if rng.random() < 0.35 else ""
    where = f" WHERE {condition(rng)}" if rng.random() < 0.65 else ""
    order_dir = lambda: rng.choice(["", " ASC", " DESC"])
    if family == 0:
        cols = rng.sample(["id", "name", "team", "salary", "rating", "active"], rng.randint(1, 3))
        distinct = "DISTINCT " if rng.random() < 0.3 else ""
        return f"SELECT {distinct}{', '.join(cols)} FROM staff{where} ORDER BY {rng.choice(cols)}{order_dir()}, 1{order_dir()}{limit}"
    if family == 1:
        kind = rng.choice(["JOIN", "LEFT JOIN"])
        return (f"SELECT s.name, t.city, t.budget, s.team FROM staff s {kind} teams t ON s.team = t.team{where.replace('team', 's.team')}"
                f" ORDER BY {rng.choice(['t.city', 'budget', 's.name', '2'])}{order_dir()}, s.id{limit}")
    if family == 2:
        having = f" HAVING COUNT(*) {rng.choice(['>', '>='])} {rng.randint(1, 3)}" if rng.random() < 0.5 else ""
        return (f"SELECT team, COUNT(*) AS n, COUNT(rating) AS rated, SUM(salary) AS total, AVG(rating) AS avg_r, MAX(name) AS last"
                f" FROM staff{where} GROUP BY team{having} ORDER BY {rng.choice(['n', 'total', 'team', '5'])}{order_dir()}, team{limit}")
    if family == 3:
        return (f"SELECT COUNT(*) AS n, COUNT(DISTINCT team) AS teams, MIN(salary) AS low, MAX(rating) AS top,"
                f" SUM(rating) AS points, AVG(salary) AS mean FROM staff{where}")
    return (f"SELECT name, salary / {rng.choice([3, 7, 1000, 0])} AS part, salary % 7 AS rem, -rating AS neg, rating * 2 AS dbl,"
            f" COALESCE(team, {quote('none')}) AS t, LENGTH(name) AS len, UPPER(name) AS up, ABS(-salary) AS mag"
            f" FROM staff{where} ORDER BY {rng.choice(['part', 'neg', 'len', 't', 'name'])}{order_dir()}, id{limit}")


def item(rng, index):
    return {"tables": tables(rng), "query": query(rng)}
