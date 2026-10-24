"""Register the rules suite with our internal pilot runner (round2.py) for harness-surface trials."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [os.environ["PILOT"], str(HERE)]
import round2  # noqa: E402
from harness.loop import loop, packs as meta_packs  # noqa: E402
import packs  # noqa: E402

meta_packs.PACKS.update(packs.PACKS)
loop.SEEDS = HERE / "seeds"
round2.ROOT = HERE
round2.TASK = round2.TASK.replace(
    "using the supplied examples. ",
    "using the supplied examples. The context field of source.json states the complete rules and every "
    "item carries all the evidence it needs. Implement the rules exactly and check your plugin against "
    "the labelled train items. ")
assert "complete rules" in round2.TASK
