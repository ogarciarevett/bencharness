"""Starting plugin: plain-column SELECT over the FROM table only; no WHERE, joins, groups or order."""
import json


def run(task):
    out = {}
    for item in task["items"]:
        x = item["input"]
        words = x["query"].replace(",", " ").split()
        table = words[words.index("FROM") + 1]
        columns, rows = x["tables"][table]["columns"], x["tables"][table]["rows"]
        picked = [w for w in words[1:words.index("FROM")] if w in columns]
        if task["params"]["star_only"] or not picked:
            picked = columns
        result = [[r[columns.index(c)] for c in picked] for r in rows]
        out[item["id"]] = {"columns": json.dumps(picked), "count": str(len(result)), "rows": json.dumps(result)}
    return out
