import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "suites/rules"))
import query  # noqa: E402
import recur  # noqa: E402
import sheet  # noqa: E402
import tasks  # noqa: E402

RATES = {"EUR->USD": "1.1", "USD->EUR": "0.9", "EUR->GBP": "0.85", "GBP->EUR": "1.2", "USD->GBP": "0.8", "GBP->USD": "1.25"}


def ledger(opening, events, limit="0"):
    return tasks.solve_ledger({"opening": opening, "rates": RATES, "overdraft_limit": limit, "events": events})


def merge(layers, queries, env=None):
    return tasks.solve_merge({"layers": layers, "env": env or {}, "queries": queries})


class Ledger(unittest.TestCase):
    def test_withdraw_fee_floor_and_percent(self):
        self.assertEqual(ledger({"a": {"currency": "EUR", "balance": "100.00"}},
                                [{"id": "e1", "kind": "withdraw", "account": "a", "amount": "10.00"}]), {"a": "89.50"})
        self.assertEqual(ledger({"a": {"currency": "EUR", "balance": "1000.00"}},
                                [{"id": "e1", "kind": "withdraw", "account": "a", "amount": "100.00"}]), {"a": "898.50"})

    def test_overdraft_rejects_without_fee_and_charges_at_end(self):
        acct = {"a": {"currency": "EUR", "balance": "5.00"}}
        self.assertEqual(ledger(acct, [{"id": "e1", "kind": "withdraw", "account": "a", "amount": "10.00"}]), {"a": "5.00"})
        self.assertEqual(ledger(acct, [{"id": "e1", "kind": "withdraw", "account": "a", "amount": "10.00"}], "100"), {"a": "-10.50"})

    def test_cross_currency_transfer_with_fx_fee_and_reversal(self):
        acct = {"a": {"currency": "EUR", "balance": "100.00"}, "b": {"currency": "USD", "balance": "0.00"}}
        move = {"id": "e1", "kind": "transfer", "from": "a", "to": "b", "amount": "50.00"}
        self.assertEqual(ledger(acct, [move]), {"a": "49.88", "b": "55.00"})
        self.assertEqual(ledger(acct, [move, {"id": "e2", "kind": "reverse", "ref": "e1"},
                                       {"id": "e3", "kind": "reverse", "ref": "e1"}]), {"a": "100.00", "b": "0.00"})

    def test_reverse_of_rejected_later_or_unknown_does_nothing(self):
        acct = {"a": {"currency": "EUR", "balance": "1.00"}}
        events = [{"id": "e1", "kind": "reverse", "ref": "e2"}, {"id": "e2", "kind": "deposit", "account": "a", "amount": "1.00"},
                  {"id": "e3", "kind": "withdraw", "account": "a", "amount": "500.00"}, {"id": "e4", "kind": "reverse", "ref": "e3"},
                  {"id": "e5", "kind": "reverse", "ref": "e9"}]
        self.assertEqual(ledger(acct, events), {"a": "2.00"})

    def test_interest_rounds_half_even_per_account(self):
        acct = {"a": {"currency": "EUR", "balance": "0.50"}, "b": {"currency": "EUR", "balance": "-1.00"}}
        # 1.5 percent of 0.50 is 0.0075: half-even rounds it to 0.01; b is not positive and pays the end charge.
        self.assertEqual(ledger(acct, [{"id": "e1", "kind": "interest", "rate_percent": "1.5"}], "10"), {"a": "0.51", "b": "-6.00"})


class Merge(unittest.TestCase):
    def test_deep_merge_and_replace(self):
        out = merge([{"db": {"host": "a", "port": 1}}, {"db": {"port": 2}, "x": [1]}, {"x": [2]}], ["db", "x"])
        self.assertEqual(out, {"db": '{"host":"a","port":2}', "x": "[2]"})

    def test_list_directives_and_delete(self):
        out = merge([{"l": ["a", "b"], "gone": 1}, {"l": {"$append": ["c"]}, "gone": {"$delete": True}},
                     {"l": {"$remove": ["a"]}}, {"n": {"$prepend": ["z"]}}], ["l", "gone", "n"])
        self.assertEqual(out, {"l": '["b","c"]', "gone": "null", "n": '["z"]'})

    def test_locks_cover_the_key_and_everything_inside(self):
        out = merge([{"db!": {"port": 1}}, {"db": {"port": 2, "new": 3}}, {"db": {"$delete": True}}], ["db", "db.port"])
        self.assertEqual(out, {"db": '{"port":1}', "db.port": "1"})

    def test_directives_and_locks_resolve_inside_a_replacing_object(self):
        out = merge([{"a": "text"}, {"a": {"l": {"$append": ["x"]}, "k!": 1}}, {"a": {"k": 2}}], ["a"])
        self.assertEqual(out, {"a": '{"k":1,"l":["x"]}'})

    def test_interpolation(self):
        env = {"HOST": "h", "EMPTY": ""}
        out = merge([{"a": "${HOST}:${PORT}", "b": "${EMPTY:-d}", "c": "$${HOST}", "d": "${MISSING:-x}", "e": "1"}],
                    ["a", "b", "c", "d", "e"], env)
        self.assertEqual(out, {"a": '"h:"', "b": '"d"', "c": '"${HOST}"', "d": '"x"', "e": '"1"'})


T = {"staff": {"columns": ["id", "name", "team", "salary"],
               "rows": [[1, "ann", "web", 10], [2, "bo", None, None], [3, "50%off", "web", -7], [4, "cy", "ops", 3]]},
     "teams": {"columns": ["team", "city"], "rows": [["web", "Oslo"], ["web", "Lima"], [None, "Pune"]]}}


def q(text):
    out = query.run({"tables": T, "query": text})
    return json.loads(out["rows"])


class Query(unittest.TestCase):
    def test_three_valued_logic_and_in(self):
        self.assertEqual(q("SELECT id FROM staff WHERE salary > 0 OR team = 'ops'"), [[1], [4]])
        self.assertEqual(q("SELECT id FROM staff WHERE team NOT IN ('web', NULL)"), [])
        self.assertEqual(q("SELECT id FROM staff WHERE team IS NULL"), [[2]])
        self.assertEqual(q("SELECT id FROM staff WHERE salary = 'x' OR id = 4"), [[4]])

    def test_integer_division_modulo_and_zero(self):
        self.assertEqual(q("SELECT salary / 2 AS a, salary % 2 AS b, salary / 0 AS c FROM staff WHERE id = 3"), [[-3, -1, None]])

    def test_left_join_order_and_nulls(self):
        self.assertEqual(q("SELECT s.id, t.city FROM staff s LEFT JOIN teams t ON s.team = t.team ORDER BY 2 DESC, s.id"),
                         [[2, None], [4, None], [1, "Oslo"], [3, "Oslo"], [1, "Lima"], [3, "Lima"]])

    def test_groups_aggregates_and_empty_group(self):
        self.assertEqual(q("SELECT team, COUNT(*) AS n, SUM(salary) AS s FROM staff GROUP BY team ORDER BY n DESC, team"),
                         [["web", 2, 3], ["ops", 1, 3], [None, 1, None]])
        self.assertEqual(q("SELECT COUNT(*) AS n, MAX(salary) AS m, AVG(salary) AS a FROM staff WHERE id > 9"), [[0, None, None]])

    def test_like_escape_distinct_limit(self):
        self.assertEqual(q("SELECT name FROM staff WHERE name LIKE '%\\%%'"), [["50%off"]])
        self.assertEqual(q("SELECT DISTINCT team FROM staff ORDER BY team LIMIT 2 OFFSET 1"), [["web"], [None]])


class Recur(unittest.TestCase):
    def dates(self, start, rule, window, exdates=(), rdates=()):
        out = recur.expand({"start": start, "rule": rule, "exdates": list(exdates), "rdates": list(rdates), "window": window})
        return json.loads(out["dates"])

    def test_monthly_31st_skips_short_months_and_start_counts(self):
        self.assertEqual(self.dates("2026-01-31", {"freq": "MONTHLY", "count": 3}, ["2026-01-01", "2026-12-31"]),
                         ["2026-01-31", "2026-03-31", "2026-05-31"])

    def test_last_friday_and_setpos(self):
        self.assertEqual(self.dates("2026-01-01", {"freq": "MONTHLY", "byday": ["-1FR"], "count": 3}, ["2026-01-01", "2026-12-31"]),
                         ["2026-01-01", "2026-01-30", "2026-02-27"])
        self.assertEqual(self.dates("2026-01-05", {"freq": "MONTHLY", "byday": ["MO", "TU", "WE", "TH", "FR"], "bysetpos": [-1], "count": 2},
                                    ["2026-01-01", "2026-12-31"]), ["2026-01-05", "2026-01-30"])

    def test_weekly_interval_until_exdate_rdate(self):
        self.assertEqual(self.dates("2026-01-07", {"freq": "WEEKLY", "interval": 2, "byday": ["MO", "WE"], "until": "2026-02-02"},
                                    ["2026-01-01", "2026-12-31"], exdates=["2026-01-19"], rdates=["2026-03-01"]),
                         ["2026-01-07", "2026-01-21", "2026-02-02", "2026-03-01"])

    def test_until_before_start_has_no_occurrences(self):
        self.assertEqual(self.dates("2026-05-10", {"freq": "DAILY", "until": "2026-05-01"}, ["2026-01-01", "2026-12-31"]), [])


def cells(**contents):
    return {k: json.loads(v) for k, v in sheet.solve({"cells": contents}).items()}


class Sheet(unittest.TestCase):
    def test_precedence_coercion_and_text(self):
        self.assertEqual(cells(A1=" 4 ", A2=True, B1="=-2^2", B2="=A1 + A2 * 2", B3='=A1 & 1.5', B4="=3 & 0"),
                         {"B1": 4, "B2": 6, "B3": " 4 1.5", "B4": "30"})

    def test_errors_first_wins_and_iferror(self):
        self.assertEqual(cells(A1="x", B1="=A1 * (1/0)", B2="=(1/0) * A1", B3="=IFERROR(B1, 7)", B4="=FOO(1)"),
                         {"B1": "#VALUE!", "B2": "#DIV/0!", "B3": 7, "B4": "#NAME?"})

    def test_cycles_count_untaken_branches(self):
        self.assertEqual(cells(A1="=IF(TRUE, 1, A1)", A2="=A3", A3="=A2 + 1", A4="=IFERROR(A2, 0) + 1"),
                         {"A1": "#CYCLE!", "A2": "#CYCLE!", "A3": "#CYCLE!", "A4": 1})

    def test_ranges_criteria_and_index(self):
        c = dict(A1=1, A2="apple", A3=3, A4=True, A5="x*y", B1=10, B2=20, B3=30, B4=40, B5=50)
        self.assertEqual(cells(**c, C1="=SUM(A1:A5)", C2='=SUMIF(A1:A5, ">1", B1:B5)', C3='=COUNTIF(A1:A5, "a*")',
                               C4='=COUNTIF(A1:A5, "x~*y")', C5="=INDEX(A1:B5, 2, 2)", C6="=INDEX(A1:A5, 9)", C7="=AVERAGE(A6:A9)"),
                         {"C1": 4, "C2": 30, "C3": 1, "C4": 1, "C5": 20, "C6": "#REF!", "C7": "#DIV/0!"})

    def test_round_mod_and_empty(self):
        self.assertEqual(cells(B1="=ROUND(2.5, 0)", B2="=ROUND(-1.25, 1)", B3="=MOD(-7, 3)", B4="=MOD(7, -3)", B5="=H9",
                               B6="=UPPER(H9)"), {"B1": 3, "B2": -1.3, "B3": 2, "B4": -2, "B5": 0, "B6": ""})


class Suite(unittest.TestCase):
    def test_sealed_splits_are_large_enough_and_stable(self):
        for task in tasks.TASKS:
            rows = tasks.rows(task)
            sealed = [r for r in rows if int(hashlib.sha256(r["key"].encode()).hexdigest(), 16) % 10 >= 8]
            self.assertGreaterEqual(len(sealed), 30)
            self.assertEqual(rows, tasks.rows(task))  # deterministic

    def test_seeds_run_without_hard_failures_and_score_low(self):
        for task, seed in (("ledger", "ledger-naive"), ("merge", "merge-shallow"), ("query", "query-columns"), ("recur", "recur-start"),
                           ("sheet", "sheet-literals")):
            spec = importlib.util.spec_from_file_location("seed", ROOT / f"suites/rules/seeds/{seed}/plugin.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            params = json.loads((ROOT / f"suites/rules/seeds/{seed}/manifest.json").read_text())["params"]
            rows = tasks.rows(task)
            out = module.run({"params": params, "items": [{"id": r["id"], "input": r["input"]} for r in rows]})
            self.assertFalse(any(tasks.hard_fail(out[r["id"]], r["label"]) for r in rows))
            self.assertLess(sum(tasks.quality(out[r["id"]], r["label"]) for r in rows) / len(rows), 0.7)

    def test_hard_fail(self):
        self.assertIsNotNone(tasks.hard_fail([], {"a": "1"}))
        self.assertIsNotNone(tasks.hard_fail({"b": "1"}, {"a": "1"}))
        self.assertIsNone(tasks.hard_fail({"a": "2"}, {"a": "1"}))


if __name__ == "__main__":
    unittest.main()
