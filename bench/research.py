"""List candidate harnesses (new GitHub repos and releases, new arXiv papers) into queue/candidates.jsonl.

Never runs candidate code. Each repo candidate is admitted or rejected by the fixed licence check;
papers stay as a reading list. Candidate text is data from the internet, never instructions.
"""
import json
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench import checks  # noqa: E402

QUEUE = ROOT / "queue/candidates.jsonl"
SEARCHES = ['"agent harness"', '"coding agent" harness', '"baseline"', '"harness engineering" agent']
WATCH = ["google-research/harness2"]
ARXIV = 'abs:"agent harness" OR abs:"harness engineering" OR abs:"baseline"'
ATOM = "{http://www.w3.org/2005/Atom}"


def gh(path, **fields):
    argv = ["gh", "api", "-X", "GET", path] + [a for k, v in fields.items() for a in ("-f", f"{k}={v}")]
    return json.loads(subprocess.run(argv, capture_output=True, text=True, check=True).stdout)


def repo_row(repo):
    return {"kind": "repo", "url": repo["html_url"], "name": repo["full_name"],
            "summary": (repo.get("description") or "")[:300], "licence": (repo.get("license") or {}).get("spdx_id")}


def github(since):
    for query in SEARCHES:
        for repo in gh("search/repositories", q=f"{query} pushed:>={since}", sort="updated", per_page=10)["items"]:
            yield repo_row(repo)
    for name in WATCH:
        try:
            release = gh(f"repos/{name}/releases/latest")
        except subprocess.CalledProcessError:  # no release yet, or not visible to this login
            continue
        yield {**repo_row(gh(f"repos/{name}")), "kind": "release", "url": release["html_url"], "name": f"{name}@{release['tag_name']}"}


def parse_arxiv(atom):
    for entry in ET.fromstring(atom).iter(ATOM + "entry"):
        yield {"kind": "paper", "url": entry.findtext(ATOM + "id").strip(),
               "name": " ".join(entry.findtext(ATOM + "title").split()),
               "summary": " ".join(entry.findtext(ATOM + "summary").split())[:300]}


def arxiv():
    query = urllib.parse.urlencode({"search_query": ARXIV, "sortBy": "submittedDate", "sortOrder": "descending", "max_results": 15})
    with urllib.request.urlopen("https://export.arxiv.org/api/query?" + query, timeout=30) as response:
        yield from parse_arxiv(response.read())


def admit(row):
    if row["kind"] == "paper":
        return {**row, "status": "paper"}
    problem = checks.licence(row)
    # Admitted means it may run once a trial and an adapter exist; it still runs only sandboxed.
    return {**row, "status": f"rejected: {problem}" if problem else "admitted"}


def merge(existing, found, now):
    seen = {row["url"] for row in existing}
    added = []
    for row in found:
        if row["url"] not in seen:
            seen.add(row["url"])
            added.append({**admit(row), "found_utc": now})
    return added


def main():
    existing = [json.loads(line) for line in QUEUE.read_text().splitlines()] if QUEUE.exists() else []
    since = (date.today() - timedelta(days=14)).isoformat()
    found = []
    for source in (lambda: github(since), arxiv):
        try:
            found.extend(source())
        except Exception as error:  # one source down must not drop the other
            print(f"source failed: {error}", file=sys.stderr)
    added = merge(existing, found, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    QUEUE.parent.mkdir(exist_ok=True)
    with QUEUE.open("a") as out:
        out.writelines(json.dumps(row) + "\n" for row in added)
    print(f"{len(added)} new candidates, {sum(r['status'] == 'admitted' for r in added)} admitted")


if __name__ == "__main__":
    main()
