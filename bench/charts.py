"""Deterministic, dependency-free SVG charts from recorded trials only."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET

# Shared calm chart palette: blue, teal, olive, gold, mauve.
PALETTE = ("#5283a3", "#55958c", "#8b915c", "#b08c55", "#987b9a")


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:70] + "-" + hashlib.sha256(value.encode()).hexdigest()[:8]


def color(value):
    return PALETTE[int(hashlib.sha256(value.encode()).hexdigest(), 16) % len(PALETTE)]


def element(parent, tag, text=None, **attrs):
    node = ET.SubElement(parent, tag, {k.replace("_", "-"): str(v) for k, v in attrs.items()})
    node.text = text
    return node


def label(svg, x, y, text, **attrs):
    return element(svg, "text", str(text), x=x, y=y, **attrs)


def canvas(title, subtitle, height, source):
    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg", viewBox=f"0 0 960 {height}", role="img",
                     attrib={"aria-labelledby": "title description"})
    element(svg, "title", title, id="title")
    element(svg, "desc", subtitle, id="description")
    element(svg, "metadata", json.dumps(source, sort_keys=True))
    element(svg, "style", "text{font:16px system-ui,sans-serif;fill:#59636e}"
            ".title{font-size:24px;font-weight:650;fill:#24292f}"
            ".small{font-size:13px}.grid{stroke:#8b949e;stroke-opacity:.28}"
            "@media(prefers-color-scheme:dark){text{fill:#a1a7b3}.title{fill:#e6edf3}}")
    label(svg, 28, 38, title, **{"class": "title"})
    label(svg, 28, 65, subtitle, **{"class": "small"})
    return svg


def save(svg, path):
    ET.indent(svg)
    write_file(path, ET.tostring(svg, encoding="unicode") + "\n")


def validate_output(directory):
    paths = [directory, *directory.rglob("*")] if directory.exists() else [directory]
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file() and not path.is_dir()):
            raise ValueError("Generated chart output must contain only regular files and directories")


def write_file(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replace the entry atomically rather than following an existing file link.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def stamp(row):
    return datetime.fromisoformat(row["recorded_utc"].replace("Z", "+00:00")).astimezone(timezone.utc)


def score(result):
    value = result.get("score")
    if result.get("status") != "complete" or value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Completed score must be a finite number between zero and one")
    return value


def leaderboard(row, task):
    results = row["tasks"][task]["results"]
    ranked = sorted(results.items(), key=lambda item: (score(item[1]) is None, -(score(item[1]) or 0), item[0]))
    svg = canvas(f"{task} · recorded leaderboard", row["trial"], 140 + 52 * len(ranked), row)
    left, width = 270, 570
    for tick in range(5):
        x = left + width * tick / 4
        element(svg, "line", x1=x, x2=x, y1=85, y2=95 + 52 * len(ranked), **{"class": "grid"})
        label(svg, x, 119 + 52 * len(ranked), f"{tick / 4:.2f}", text_anchor="middle", **{"class": "small"})
    for index, (harness, result) in enumerate(ranked):
        y = 107 + index * 52
        name = label(svg, 28, y + 7, harness[:28])
        element(name, "title", harness)
        value = score(result)
        if value is not None:
            element(svg, "rect", x=left, y=y-13, width=width*value, height=26, fill=color(harness))
        label(svg, 922, y+7, f"{value:.4f}" if value is not None else "not scored", text_anchor="end")
    return svg


def context(row):
    # A harness/task line never crosses a suite, memory, or execution-setting change.
    bench = row.get("bench", "")
    settings = (bench.split(", A=", 1)[0], bench.rsplit(", ", 1)[-1],
                tuple(re.findall(r"\b(?:mode|k)=[^, ]+", bench)))
    return row.get("suite"), row.get("suite_pin"), row.get("memory"), row.get("memory_pin"), settings


def trend(rows, harness):
    tasks = sorted({task for row in rows for task, entry in row["tasks"].items() if harness in entry["results"]})
    svg = canvas(f"{harness} · score history", "UTC · separate panels per task; lines connect matching settings only", 115 + 165*len(tasks), rows)
    first, last = stamp(rows[0]).timestamp(), stamp(rows[-1]).timestamp()
    left, width = 190, 690
    for index, task in enumerate(tasks):
        top = 100 + 165*index
        label(svg, 28, top+30, task)
        groups = defaultdict(list)
        for row in rows:
            result = row["tasks"].get(task, {}).get("results", {}).get(harness)
            value = score(result) if result else None
            if value is None:
                if result:
                    groups[context(row)].append(None)
                continue
            x = left + width*((stamp(row).timestamp()-first)/(last-first) if last > first else .5)
            groups[context(row)].append((x, top+105-80*value, value, row["trial"]))
        for value in (0, .5, 1):
            y = top+105-80*value
            element(svg, "line", x1=left, x2=left+width, y1=y, y2=y, **{"class": "grid"})
            label(svg, left-12, y+5, f"{value:g}", text_anchor="end", **{"class": "small"})
        segments = []
        for observations in groups.values():
            points = []
            for point in observations + [None]:
                if point is None:
                    if points:
                        segments.append(points)
                    points = []
                else:
                    points.append(point)
        annotations = []
        for points in segments:
            if len(points) > 1:
                element(svg, "polyline", points=" ".join(f"{x:.2f},{y:.2f}" for x,y,_,_ in points), fill="none", stroke=color(harness), stroke_width=2)
            for x, y, value, trial in points:
                node = element(svg, "circle", cx=x, cy=y, r=4, fill=color(harness))
                element(node, "title", f"{trial}: {value:.4f}")
                annotations.append((x, y, value))
        clusters = []
        for point in sorted(annotations):
            if clusters and point[2] == clusters[-1][-1][2] and point[0] - clusters[-1][-1][0] < 65:
                clusters[-1].append(point)
            else:
                clusters.append([point])
        for number, points in enumerate(clusters):
            x = sum(p[0] for p in points) / len(points)
            value = f"{points[0][2]:.4f}" + (f" ×{len(points)}" if len(points) > 1 else "")
            label(svg, x, points[0][1]-12-(number%2)*16, value, text_anchor="middle", **{"class": "small"})
        label(svg, left, top+134, stamp(rows[0]).strftime("%m-%d %H:%M"), **{"class": "small"})
        label(svg, left+width, top+134, stamp(rows[-1]).strftime("%m-%d %H:%M"), text_anchor="end", **{"class": "small"})
    return svg


def summary(rows):
    results = [result for row in rows for entry in row["tasks"].values() for result in entry["results"].values()]
    scored = sum(score(result) is not None for result in results)
    svg = canvas("Recorded benchmark summary", "Saved observations only · no pooled ranking across different tasks or settings", 210, rows)
    for x, value, name in [(28, len(rows), "recorded trials"), (335, f"{scored} / {len(results)}", "scored / recorded results"), (665, stamp(rows[-1]).strftime("%Y-%m-%d"), "latest recorded date (UTC)")]:
        label(svg, x, 125, value, **{"class": "title"})
        label(svg, x, 157, name)
    label(svg, 28, 193, "Exploratory trials; no repeated-run confidence intervals. Full settings and notes remain in the raw records.", **{"class": "small"})
    return svg


def featured(rows):
    """Prefer the newest scored comparison with a spread, without pooling trials."""
    for row in reversed(rows):
        spreads = []
        for task, entry in row["tasks"].items():
            values = [value for result in entry["results"].values() if (value := score(result)) is not None]
            if len(values) > 1 and max(values) > min(values):
                spreads.append((max(values)-min(values), task))
        if spreads:
            return row, max(spreads)[1]
    return rows[-1], sorted(rows[-1]["tasks"])[0]


def write(rows, directory):
    """Backfill per-trial images and refresh latest charts; return README fragments."""
    validate_output(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not rows:
        for path in directory.rglob("*"):
            if path.is_file():
                path.unlink()
        return "No recorded trials yet.", "No recorded trials yet."
    rows = sorted(rows, key=lambda row: (stamp(row), row["trial"]))
    latest = directory / "latest"
    expected = set()

    def output(image, path):
        save(image, path)
        expected.add(path)

    output(summary(rows), latest / "summary.svg")
    links = []
    for row in reversed(rows):
        target = directory / "runs" / slug(row["trial"])
        output(summary([row]), target / "summary.svg")
        images = ["![Run summary](summary.svg)"]
        for task in sorted(row["tasks"]):
            name = f"leaderboard-{slug(task)}.svg"
            output(leaderboard(row, task), target / name)
            images.append(f"![Recorded task leaderboard]({name})")
        page = target / "README.md"
        title = html.escape(row['trial']).replace("[", "\\[").replace("]", "\\]")
        write_file(page, f"# {title}\n\n" + "\n\n".join(images) +
                        "\n\nOne recorded trial. [Raw data and settings](../../../../results/trials.jsonl).\n")
        expected.add(page)
        links.append(f"- [{title}](runs/{slug(row['trial'])}/README.md)")
    top = ["![Recorded benchmark summary](docs/charts/latest/summary.svg)"]
    selected, selected_task = featured(rows)
    top.append(f"![Recent task comparison](docs/charts/runs/{slug(selected['trial'])}/leaderboard-{slug(selected_task)}.svg)")
    top.append("The featured task is the most recent recorded comparison with a score spread; ties appear in the full gallery. "
               "If every comparison ties, the latest task is shown. Each chart retains its own experiment scope.")
    gallery = []
    tasks = sorted({task for row in rows for task in row["tasks"]})
    for task in tasks:
        row = next(row for row in reversed(rows) if task in row["tasks"])
        name = f"latest/leaderboard-{slug(task)}.svg"
        output(leaderboard(row, task), directory / name)
        gallery.append(f"![Latest recorded task leaderboard]({name})")
    for harness in sorted({h for row in rows for entry in row["tasks"].values() for h in entry["results"]}):
        name = f"latest/trend-{slug(harness)}.svg"
        output(trend(rows, harness), directory / name)
        gallery.append(f"![Recorded harness score history]({name})")
    page = directory / "README.md"
    write_file(page, "# Recorded charts\n\nEach leaderboard uses the latest recorded trial containing that task; different charts can describe different experiments.\n\n" +
                    "\n\n".join(gallery) + "\n\n## Older runs\n\n" + "\n".join(links) +
                    "\n\n[Raw records](../../results/trials.jsonl) · [Tables and notes](../../RESULTS.md)\n")
    expected.add(page)
    for path in directory.rglob("*"):
        if path.is_file() and path not in expected:
            path.unlink()
    top.append("[All task leaderboards, harness trends and older runs](docs/charts/README.md) · [Raw records](results/trials.jsonl)")
    return "\n\n".join(top), "[Browse chart images and older runs](docs/charts/README.md). Raw tables and trial notes are in [the results appendix](RESULTS.md)."


if __name__ == "__main__":
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    source = root / "results/trials.jsonl"
    write([json.loads(line) for line in source.read_text().splitlines()] if source.exists() else [], root / "docs/charts")
