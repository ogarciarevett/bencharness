"""Starting plugin: a formula that is one number or one plain number cell; everything else gets a default."""
import json


def run(task):
    out = {}
    for item in task["items"]:
        cells = item["input"]["cells"]
        answer = {}
        for name, content in cells.items():
            if isinstance(content, str) and content.startswith("="):
                body = content[1:].strip()
                value = cells.get(body)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    answer[name] = json.dumps(value)
                else:
                    try:
                        answer[name] = json.dumps(int(body))
                    except ValueError:
                        answer[name] = task["params"]["default"]
        out[item["id"]] = answer
    return out
