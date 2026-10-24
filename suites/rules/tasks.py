"""Rules suite: two coding tasks where every item carries all the evidence it needs.

Nothing is retrieved, so memory is never the limit. The task context states the complete rules, and
a plugin scores well only if it implements them exactly. That depends on the harness: how it plans,
reads the rules, tests against the labelled examples and follows through on edge cases.

Data is synthetic and generated here from a fixed seed: no private data, no files to fetch.
Standard library only, so the generators and reference solvers are tested in CI.
"""
import json
import random
from decimal import ROUND_HALF_EVEN, Decimal

ITEMS = 200  # per task; the 20 percent sealed split holds about 40

LEDGER_RULES = """\
Each item is one day of events for a small business wallet. Return the closing balance of every
account in item["opening"], as a string with exactly two decimals, for example "-12.50".

1. item["opening"] maps account -> {"currency": code, "balance": string}. Balances are in the
   account's own currency.
2. Convert an amount from currency A to B with item["rates"]["A->B"]. Same currency: rate 1.
   An event amount is in the account's currency unless the event has "currency"; then convert it
   into the account's currency first (for a transfer: into the source account's currency).
3. Round every money change to cents with half-even rounding at the moment it is applied: each
   converted amount, each fee and each interest credit is rounded on its own.
4. Events run in order. Kinds:
   - deposit {account, amount}: add the amount.
   - withdraw {account, amount}: fee = max(0.50, 1.5 percent of the amount in the account currency).
     Subtract amount + fee.
   - transfer {from, to, amount}: subtract amount from "from"; credit "to" with the amount converted
     from the source currency to the destination currency. When the currencies differ, the source
     also pays an fx fee of 0.25 percent of the amount in the source currency.
   - reverse {ref}: undo the applied event with id ref, with all its fees and credits, exactly as
     they were applied. Reversing an event that was rejected, already reversed, not applied before
     this reverse (unknown or later), a reverse or an interest event does nothing.
   - interest {rate_percent}: every account with a balance above zero gains rate_percent percent
     of its balance at that moment.
5. Overdraft: a withdraw or transfer is rejected (no change at all, no fee) when the source balance
   after amount and fees would be below minus item["overdraft_limit"]. Deposits and reversals are
   never rejected.
6. After all events, every account whose balance is below zero pays one overdraft charge of 5.00.
"""

MERGE_RULES = """\
Each item has config layers (lowest precedence first), an env map and query keys. Return a map from
each query key to json.dumps of its final value (keys sorted, no extra spaces: separators (",", ":")),
or the string "null" when the key is absent. A query key is a dot path such as "db.pool.size".

1. Layers merge in order, starting from an empty object: a higher layer's value replaces the lower
   one, except when the higher value is an object: then it merges key by key, recursively, onto the
   lower value if that is an object, else onto an empty object. So rules 2 and 3 apply at every
   depth, also inside an object that replaces a non-object. Lists are values: their elements are
   never merged.
2. A higher value that is an object with exactly one of these keys is a directive on the lower value:
   - {"$append": list} or {"$prepend": list}: add the items after or before the lower list (a lower
     value that is not a list counts as an empty list).
   - {"$remove": list}: drop every element of the lower list equal to one in the list (a lower value
     that is not a list counts as an empty list).
   - {"$delete": true}: remove the key.
   A directive with no lower value applies to an empty list (or deletes nothing).
3. A key written with a trailing "!" (for example "port!") is locked from that layer on: later
   layers cannot change or delete it or anything inside it. Its final name has no "!". A lock in a later layer still
   applies its own value first unless an earlier layer already locked the key.
4. After the full merge, every string is interpolated from env: "${NAME}" becomes env NAME (empty
   string when unset); "${NAME:-default}" uses default when NAME is unset or empty; "$${" stays as a
   literal "${" and is not interpolated. Object keys are never interpolated.
5. Numbers, booleans and null stay as they are; the strings "true" and "1" stay strings.
"""

CURRENCIES = ["EUR", "USD", "GBP", "JPY"]
WORDS = ["alpha", "beta", "gamma", "delta", "orbit", "lumen", "cedar", "maple", "harbor", "quartz", "falcon", "ember"]


def cents(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


# ---------- ledger ----------

def ledger_item(rng, index):
    names = rng.sample(["ops", "payroll", "tax", "reserve", "travel", "vendor"], 3)
    opening = {n: {"currency": rng.choice(CURRENCIES[:3] if i else CURRENCIES), "balance": f"{rng.randint(0, 5000)}.{rng.randint(0, 99):02d}"}
               for i, n in enumerate(names)}
    rates = {}
    for a in CURRENCIES:
        for b in CURRENCIES:
            if a != b:
                rates[f"{a}->{b}"] = str(Decimal(rng.randint(5000, 20000)) / Decimal(10000) * (Decimal(100) if b == "JPY" else Decimal(1))
                                         / (Decimal(100) if a == "JPY" else Decimal(1)))
    events = []
    for n in range(rng.randint(6, 14)):
        kind = rng.choices(["deposit", "withdraw", "transfer", "reverse", "interest"], [3, 4, 4, 2, 1])[0]
        e = {"id": f"e{n + 1}", "kind": kind}
        amount = f"{rng.randint(1, 3000)}.{rng.randint(0, 99):02d}"
        if kind in ("deposit", "withdraw"):
            e.update(account=rng.choice(names), amount=amount)
        elif kind == "transfer":
            src, dst = rng.sample(names, 2)
            e.update({"from": src, "to": dst, "amount": amount})
        elif kind == "reverse":
            e["ref"] = f"e{rng.randint(1, n + 2)}"  # may point at itself, a later or an unknown event
        else:
            e["rate_percent"] = str(rng.choice(["0.5", "1", "1.25", "2"]))
        if kind in ("deposit", "withdraw", "transfer") and rng.random() < 0.25:
            e["currency"] = rng.choice(CURRENCIES)
        events.append(e)
    return {"opening": opening, "rates": rates, "overdraft_limit": str(rng.choice([0, 100, 500])), "events": events}


def solve_ledger(item):
    cur = {a: v["currency"] for a, v in item["opening"].items()}
    bal = {a: Decimal(v["balance"]) for a, v in item["opening"].items()}
    limit = Decimal(item["overdraft_limit"])
    applied = {}  # event id -> list of (account, change) as applied
    reversed_ = set()

    def convert(amount, a, b):
        return amount if a == b else cents(amount * Decimal(item["rates"][f"{a}->{b}"]))

    for e in item["events"]:
        kind = e["kind"]
        changes = []
        if kind == "deposit":
            acct = e["account"]
            changes = [(acct, convert(Decimal(e["amount"]), e.get("currency", cur[acct]), cur[acct]))]
        elif kind == "withdraw":
            acct = e["account"]
            amount = convert(Decimal(e["amount"]), e.get("currency", cur[acct]), cur[acct])
            fee = cents(max(Decimal("0.50"), amount * Decimal("0.015")))
            if bal[acct] - amount - fee < -limit:
                continue
            changes = [(acct, -amount), (acct, -fee)]
        elif kind == "transfer":
            src, dst = e["from"], e["to"]
            amount = convert(Decimal(e["amount"]), e.get("currency", cur[src]), cur[src])
            fx = cents(amount * Decimal("0.0025")) if cur[src] != cur[dst] else Decimal(0)
            if bal[src] - amount - fx < -limit:
                continue
            changes = [(src, -amount), (dst, convert(amount, cur[src], cur[dst]))] + ([(src, -fx)] if fx else [])
        elif kind == "reverse":
            ref = e["ref"]
            if ref in applied and ref not in reversed_:
                reversed_.add(ref)
                for acct, change in applied[ref]:
                    bal[acct] -= change
            continue
        else:  # interest
            rate = Decimal(e["rate_percent"]) / Decimal(100)
            for acct in bal:
                if bal[acct] > 0:
                    bal[acct] += cents(bal[acct] * rate)
            continue
        for acct, change in changes:
            bal[acct] += change
        applied[e["id"]] = changes
    return {a: f"{b - Decimal(5) if b < 0 else b:.2f}" for a, b in bal.items()}


# ---------- merge ----------

def value(rng, depth):
    r = rng.random()
    if depth > 1 or r < 0.45:
        return rng.choice([rng.randint(0, 99), True, False, None, "true", "1", rng.choice(WORDS),
                           "${" + rng.choice(["HOST", "PORT", "MODE", "EMPTY", "MISSING"]) + "}",
                           "${" + rng.choice(["MODE", "EMPTY", "MISSING"]) + ":-" + rng.choice(WORDS) + "}",
                           "$${" + rng.choice(WORDS) + "}", "x-${HOST}-y"])
    if r < 0.65:
        return [rng.choice(WORDS) for _ in range(rng.randint(0, 3))]
    return {rng.choice(WORDS[:6]): value(rng, depth + 1) for _ in range(rng.randint(1, 3))}


def layer(rng, base):
    out = {}
    for _ in range(rng.randint(2, 5)):
        key = rng.choice(WORDS[:6])
        r = rng.random()
        if r < 0.15:
            v = {rng.choice(["$append", "$prepend", "$remove"]): [rng.choice(WORDS) for _ in range(rng.randint(1, 2))]}
        elif r < 0.22:
            v = {"$delete": True}
        else:
            v = value(rng, 0)
        if key in out or key + "!" in out:
            continue  # one name per layer: "a" next to "a!" would be ambiguous
        if rng.random() < 0.1:
            key += "!"
        out[key] = v
    if base and rng.random() < 0.6:  # also touch a nested key of an earlier object
        parent = rng.choice(list(base))
        if isinstance(base[parent], dict) and base[parent] and not directive(base[parent]):
            name = parent.rstrip("!")
            if name not in out and name + "!" not in out:
                out[name] = {rng.choice(list(base[parent])): value(rng, 1)}
    return out


def merge_item(rng, index):
    layers = []
    for _ in range(rng.randint(2, 4)):
        layers.append(layer(rng, layers[-1] if layers else {}))
    env = {k: v for k, v in {"HOST": rng.choice(["db.local", "10.0.0.5"]), "PORT": str(rng.randint(1000, 9999)),
                             "MODE": rng.choice(["", "fast", "safe"]), "EMPTY": ""}.items() if rng.random() < 0.85}
    keys = set()

    def paths(d, prefix=""):
        for k, v in d.items():
            name = prefix + k.rstrip("!")
            keys.add(name)
            if isinstance(v, dict) and not any(x.startswith("$") for x in v):
                paths(v, name + ".")
    for item_layer in layers:
        paths(item_layer)
    queries = sorted(rng.sample(sorted(keys), min(8, len(keys)))) + ["missing.key"]
    return {"layers": layers, "env": env, "queries": queries}


DIRECTIVES = {"$append", "$prepend", "$remove", "$delete"}


def directive(v):
    return isinstance(v, dict) and len(v) == 1 and next(iter(v)) in DIRECTIVES


def merge_dicts(lower, higher, locked, prefix):
    """Merge layer `higher` onto `lower`; `locked` holds dot paths locked by earlier layers."""
    out = dict(lower)
    for raw, v in higher.items():
        name = raw.rstrip("!")
        path = prefix + name
        if any(path == lock or path.startswith(lock + ".") for lock in locked):
            continue
        old = out.get(name)
        if directive(v):
            (op, arg), = v.items()
            base = old if isinstance(old, list) else []
            if op == "$delete":
                out.pop(name, None)
            else:
                out[name] = base + arg if op == "$append" else arg + base if op == "$prepend" else [x for x in base if x not in arg]
        elif isinstance(v, dict):
            out[name] = merge_dicts(old if isinstance(old, dict) else {}, v, locked, path + ".")[0]
        else:
            out[name] = v
        if raw.endswith("!"):
            locked.add(path)
    return out, locked


def interpolate(v, env):
    if isinstance(v, dict):
        return {k: interpolate(x, env) for k, x in v.items()}
    if isinstance(v, list):
        return [interpolate(x, env) for x in v]
    if not isinstance(v, str):
        return v
    out, i = [], 0
    while i < len(v):
        if v.startswith("$${", i):
            out.append("${")
            i += 3
        elif v.startswith("${", i) and "}" in v[i:]:
            end = v.index("}", i)
            body = v[i + 2:end]
            name, _, default = body.partition(":-")
            got = env.get(name, "")
            out.append(got if got or ":-" not in body else default)
            i = end + 1
        else:
            out.append(v[i])
            i += 1
    return "".join(out)


def solve_merge(item):
    merged, locked = {}, set()
    for item_layer in item["layers"]:
        merged, locked = merge_dicts(merged, item_layer, locked, "")
    final = interpolate(merged, item["env"])
    answer = {}
    for query in item["queries"]:
        node, present = final, True
        for part in query.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                present = False
                break
        answer[query] = json.dumps(node, sort_keys=True, separators=(",", ":")) if present else "null"
    return answer


import query  # noqa: E402  the two harder tasks live in their own modules
import recur  # noqa: E402
import sheet  # noqa: E402

TASKS = {"ledger": (ledger_item, solve_ledger, LEDGER_RULES), "merge": (merge_item, solve_merge, MERGE_RULES),
         "query": (query.item, query.run, query.RULES), "recur": (recur.item, recur.expand, recur.RULES),
         "sheet": (sheet.item, sheet.solve, sheet.RULES)}


def rows(task):
    make, solve, _ = TASKS[task]
    rng = random.Random(f"bencharness-rules-{task}-v1")
    out = []
    for index in range(ITEMS):
        item = make(rng, index)
        out.append({"id": f"{task}-{index:03d}", "key": f"{task}-{index:03d}", "input": item, "label": solve(item)})
    return out


def quality(prediction, label):
    """Share of label keys whose predicted value is exactly right."""
    return sum(prediction.get(k) == v for k, v in label.items()) / len(label)


def hard_fail(prediction, label):
    if not isinstance(prediction, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in prediction.items()):
        return "prediction is not a map of strings to strings"
    if set(prediction) - set(label):
        return "prediction names a key that the item does not have"
