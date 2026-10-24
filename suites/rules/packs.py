"""The rules suite as baseline harness packs: the task context is the complete rule text."""
from harness.loop.packs import Pack

import tasks


def make(task, seed):
    class Rules(Pack):
        name, context = task, tasks.TASKS[task][2]

        def __init__(self):
            self.rows = tasks.rows(task)

        def quality(self, prediction, row):
            return tasks.quality(prediction, row["label"])

        def hard_fail(self, prediction, row):
            return tasks.hard_fail(prediction, row["label"])
    Rules.seed = seed
    return Rules


PACKS = {"ledger": make("ledger", "ledger-naive"), "merge": make("merge", "merge-shallow"),
         "query": make("query", "query-columns"), "recur": make("recur", "recur-start"),
         "sheet": make("sheet", "sheet-literals")}
