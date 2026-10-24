"""Starting plugin: top-level overwrite of the layers; no deep merge, directives, locks or env."""
import json


def run(task):
    out = {}
    for item in task["items"]:
        x = item["input"]
        merged = {}
        for layer in x["layers"]:
            for key, value in layer.items():
                merged[key.rstrip("!") if task["params"]["strip_locks"] else key] = value
        answer = {}
        for query in x["queries"]:
            node = merged
            for part in query.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            answer[query] = "null" if node is None else json.dumps(node, sort_keys=True, separators=(",", ":"))
        out[item["id"]] = answer
    return out
