"""Starting plugin: opening balances plus same-currency deposits; no fees, transfers or rules."""
from decimal import Decimal


def run(task):
    out = {}
    for item in task["items"]:
        x = item["input"]
        bal = {a: Decimal(v["balance"]) for a, v in x["opening"].items()}
        if task["params"]["count_deposits"]:
            for e in x["events"]:
                if e["kind"] == "deposit" and "currency" not in e:
                    bal[e["account"]] += Decimal(e["amount"])
        out[item["id"]] = {a: f"{b:.2f}" for a, b in bal.items()}
    return out
