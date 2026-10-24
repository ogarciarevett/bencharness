"""recur task: expand a calendar recurrence rule into its dates inside a window."""
import calendar
import json
from datetime import date, timedelta

RULES = """\
Each item has "start" (a date, YYYY-MM-DD), a "rule", "exdates", "rdates" and a "window" [first, last]
(inclusive). Return {"dates": json.dumps(sorted occurrence dates inside the window, as YYYY-MM-DD
strings), "count": str(how many)}. There are no times or time zones.

rule keys: "freq" (DAILY, WEEKLY, MONTHLY or YEARLY), "interval" (default 1), at most one of "count"
and "until" (a date, inclusive), and optional "bymonth" (1-12), "bymonthday" (1-31 or -1..-31),
"byday" (MO TU WE TH FR SA SU, optionally with an ordinal: "2TU", "-1FR") and "bysetpos" (lists).

1. Periods. DAILY: each day. WEEKLY: each week, weeks run Monday to Sunday. MONTHLY: each calendar
   month. YEARLY: each calendar year. The rule visits the period that holds start, then every
   interval-th period after it (week, month or year counted from start's week, month or year).
2. Candidates in one period:
   - DAILY: the day itself; it is kept only if it passes every BY filter given (bymonth,
     bymonthday, byday weekdays; ordinals are not used with DAILY).
   - WEEKLY: byday weekdays in that week (default: start's weekday); bymonth filters them.
   - MONTHLY: the month is skipped unless it is in bymonth (when given). With bymonthday: those days
     of the month (-1 is the last day; a day the month lacks, like 31 in April, is skipped). With
     byday: every listed weekday in the month, or the nth one for an ordinal ("2TU" the second
     Tuesday, "-1FR" the last Friday; a missing nth is skipped). With both: only days in both sets.
     With neither: start's day of the month (skipped when the month lacks it).
   - YEARLY: for each month in bymonth (default: start's month), the MONTHLY candidates of that
     month as above; ordinals count inside the month.
3. bysetpos: from the sorted candidates of one period, keep those at the listed positions (1 is the
   first, -1 the last; positions that do not exist are skipped). Without bysetpos keep all.
4. Candidates before start are dropped. start is always the first occurrence, even if it does not
   fit the rule, and it is not added twice.
5. count: the rule yields its first count occurrences (start included). until: no occurrence after
   it; an until before start means the rule has no occurrences at all, not even start. Without
   either, the rule goes on forever (only the window matters).
6. Then every exdate is removed and every rdate is added (once). Finally keep only dates in the
   window.
"""

DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def iso(d):
    return d.isoformat()


def month_days(year, month):
    return calendar.monthrange(year, month)[1]


def parse_byday(token):
    return (int(token[:-2]) if token[:-2] else None), DAYS.index(token[-2:])


def monthly_candidates(year, month, rule, start):
    last = month_days(year, month)
    sets = []
    if rule.get("bymonthday"):
        days = set()
        for n in rule["bymonthday"]:
            day = n if n > 0 else last + 1 + n
            if 1 <= day <= last:
                days.add(day)
        sets.append(days)
    if rule.get("byday"):
        days = set()
        for token in rule["byday"]:
            nth, weekday = parse_byday(token)
            matching = [d for d in range(1, last + 1) if date(year, month, d).weekday() == weekday]
            if nth is None:
                days.update(matching)
            elif 1 <= abs(nth) <= len(matching):
                days.add(matching[nth - 1] if nth > 0 else matching[nth])
        sets.append(days)
    if not sets:
        sets.append({start.day} if start.day <= last else set())
    return sorted(date(year, month, d) for d in set.intersection(*sets))


def period_candidates(rule, start, anchor):
    freq = rule["freq"]
    if freq == "DAILY":
        d = anchor
        ok = (not rule.get("bymonth") or d.month in rule["bymonth"]) and \
             (not rule.get("bymonthday") or any(d.day == (n if n > 0 else month_days(d.year, d.month) + 1 + n) for n in rule["bymonthday"])) and \
             (not rule.get("byday") or DAYS[d.weekday()] in rule["byday"])
        return [d] if ok else []
    if freq == "WEEKLY":
        weekdays = [parse_byday(t)[1] for t in rule.get("byday", [])] or [start.weekday()]
        days = sorted(anchor + timedelta(days=w) for w in set(weekdays))
        return [d for d in days if not rule.get("bymonth") or d.month in rule["bymonth"]]
    if freq == "MONTHLY":
        if rule.get("bymonth") and anchor.month not in rule["bymonth"]:
            return []
        return monthly_candidates(anchor.year, anchor.month, rule, start)
    out = []
    for month in sorted(rule.get("bymonth") or [start.month]):
        out += monthly_candidates(anchor.year, month, rule, start)
    return out


def anchors(rule, start):
    step = rule.get("interval", 1)
    freq = rule["freq"]
    if freq == "DAILY":
        d = start
        while True:
            yield d
            d += timedelta(days=step)
    elif freq == "WEEKLY":
        d = start - timedelta(days=start.weekday())
        while True:
            yield d
            d += timedelta(weeks=step)
    elif freq == "MONTHLY":
        y, m = start.year, start.month
        while True:
            yield date(y, m, 1)
            m += step
            y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    else:
        y = start.year
        while True:
            yield date(y, 1, 1)
            y += step


def expand(item):
    rule, start = item["rule"], date.fromisoformat(item["start"])
    last = date.fromisoformat(item["window"][1])
    until = date.fromisoformat(rule["until"]) if "until" in rule else None
    stop = min(d for d in (until, last) if d is not None)
    count = rule.get("count")
    out = [start]
    for anchor in anchors(rule, start):
        if anchor > stop:
            break
        candidates = period_candidates(rule, start, anchor)
        if rule.get("bysetpos"):
            picked = []
            for pos in rule["bysetpos"]:
                if 1 <= abs(pos) <= len(candidates):
                    picked.append(candidates[pos - 1] if pos > 0 else candidates[pos])
            candidates = sorted(set(picked))
        for d in candidates:
            if d <= start or (until and d > until):
                continue
            if count is not None and len(out) >= count:
                break
            out.append(d)
        if count is not None and len(out) >= count:
            break
    if until and start > until:
        out = []  # start after until: even start is not an occurrence of the rule
    dates = (set(out) - {date.fromisoformat(x) for x in item["exdates"]}) | {date.fromisoformat(x) for x in item["rdates"]}
    first = date.fromisoformat(item["window"][0])
    kept = sorted(d for d in dates if first <= d <= last)
    return {"dates": json.dumps([iso(d) for d in kept]), "count": str(len(kept))}


# ---------- generator ----------

def item(rng, index):
    start = date(2026, 1, 1) + timedelta(days=rng.randint(0, 700))
    freq = rng.choice(["DAILY", "WEEKLY", "MONTHLY", "MONTHLY", "YEARLY"])
    rule = {"freq": freq}
    if rng.random() < 0.6:
        rule["interval"] = rng.choice([1, 2, 3])
    if freq == "DAILY":
        if rng.random() < 0.5:
            rule["byday"] = rng.sample(DAYS, rng.randint(1, 3))
        if rng.random() < 0.3:
            rule["bymonthday"] = rng.sample([1, 15, 28, 29, 30, 31, -1, -2], rng.randint(1, 2))
        if rng.random() < 0.3:
            rule["bymonth"] = sorted(rng.sample(range(1, 13), rng.randint(1, 4)))
    elif freq == "WEEKLY":
        if rng.random() < 0.7:
            rule["byday"] = rng.sample(DAYS, rng.randint(1, 3))
        if rng.random() < 0.2:
            rule["bymonth"] = sorted(rng.sample(range(1, 13), rng.randint(2, 6)))
        if rng.random() < 0.2:
            rule["bysetpos"] = [rng.choice([1, -1])]
    else:
        r = rng.random()
        if r < 0.35:
            rule["bymonthday"] = rng.sample([1, 10, 29, 30, 31, -1, -3, -31], rng.randint(1, 2))
        elif r < 0.75:
            rule["byday"] = rng.sample([rng.choice(["", "1", "2", "3", "4", "5", "-1", "-2"]) + d for d in DAYS], rng.randint(1, 3))
        elif r < 0.9:
            rule["bymonthday"] = list(range(rng.randint(1, 7), rng.randint(8, 14)))
            rule["byday"] = rng.sample(DAYS, 1)
        if rng.random() < 0.25:
            rule["bysetpos"] = rng.sample([1, 2, -1, -2, 9], rng.randint(1, 2))
        if freq == "YEARLY" or rng.random() < 0.2:
            rule["bymonth"] = sorted(rng.sample(range(1, 13), rng.randint(1, 3)))
    if rng.random() < 0.4:
        rule["count"] = rng.randint(1, 12)
    elif rng.random() < 0.5:
        rule["until"] = iso(start + timedelta(days=rng.randint(-5, 400)))
    window_start = start + timedelta(days=rng.randint(-30, 120))
    window = [iso(window_start), iso(window_start + timedelta(days=rng.randint(30, 500)))]
    near = lambda: iso(start + timedelta(days=rng.randint(0, 200)))
    return {"start": iso(start), "rule": rule, "exdates": [near() for _ in range(rng.randint(0, 2))],
            "rdates": [near() for _ in range(rng.randint(0, 2))], "window": window}
