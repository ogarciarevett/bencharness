"""Starting plugin: start and the rdates only; the rule itself is not expanded."""
import json


def run(task):
    out = {}
    for item in task["items"]:
        x = item["input"]
        dates = {x["start"]} | (set(x["rdates"]) if task["params"]["keep_rdates"] else set())
        kept = sorted(d for d in dates - set(x["exdates"]) if x["window"][0] <= d <= x["window"][1])
        out[item["id"]] = {"dates": json.dumps(kept), "count": str(len(kept))}
    return out
