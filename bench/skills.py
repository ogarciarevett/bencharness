"""Paired offline skill smoke benchmarks through the existing restricted model seam."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import contextlib
import sys

from bench import guard, seam
from bench.run import ROOT, RUNS, seam as model_seam, sandboxed

PIN = json.loads((Path(__file__).with_name('skills-pin.json')).read_text())
SPEC = dict(model='gpt-6.1-sol', effort='medium', service_tier='standard', budget_active_seconds=180)
# Public task requirements; graders stay outside every model workspace.
TASKS = {
    'diagnosing-bugs': ('expiry-bug', 'Fix cache.py: get(key, now) must expire entries at now >= expires, return None for missing/expired keys, and preserve unexpired values. Add a regression test.', {'cache.py': 'entries = {"a": ("value", 10)}\ndef get(key, now):\n    value, expires = entries[key]\n    return value if now <= expires else None\n'}, 'cache.py', []),
    'tdd': ('retry-code', 'Implement retry.py: retry(fn, attempts) calls fn until success; catches ValueError only; raises last failure when attempts exhausted; rejects attempts < 1 with ValueError before calling fn. Write tests.', {}, 'retry.py', []),
    'prototype': ('reservation-demo', 'Create prototype.html, a standalone throwaway logic demo for a seat reservation. States: available, held, confirmed. Buttons hold, confirm, reset. Confirm is allowed only from held. Mark this as a prototype. No dependencies.', {}, 'prototype.html', ['available', 'held', 'confirmed', '<button', '<script', 'prototype']),
    'research': ('offline-source-research', 'Using only supplied docs/source.md as the primary source, write RESEARCH.md answering the cache TTL, expiry boundary, and whether failures are cached. Cite the source path for each conclusion. Do not infer unsupported behavior.', {'docs/source.md': 'Cache contract v2: TTL is 60 seconds from insertion. Entries expire when now >= expires_at. Failed responses are never cached. Capacity behavior is unspecified.\n'}, 'RESEARCH.md', ['60', '>=', 'fail', 'docs/source.md', 'unspecified']),
    'domain-modeling': ('reservation-domain', 'Write GLOSSARY.md and docs/adr/0001-reservation.md for seat booking. Distinguish temporary Hold from paid Booking; a Hold expires after 10 minutes; a Booking survives Hold expiry. Record the expiry decision and its consequences.', {}, 'GLOSSARY.md', ['hold', 'booking', '10', 'expir']),
    'codebase-design': ('cache-interface', 'Write DESIGN.md proposing a small cache module interface hiding storage, expiration and serialization. Include the interface, invariants, failure behavior and tests through that interface. Avoid exposing storage internals to callers.', {}, 'DESIGN.md', ['interface', 'expir', 'serial', 'fail', 'test']),
    'code-review': ('expiry-review', 'Review change.diff against SPEC.md and STANDARDS.md. Write REVIEW.md with concrete file/line findings for both correctness and standards. Do not edit the implementation. The supplied diff is the review fixed point; no issue tracker is available.', {'SPEC.md': 'Expire at now >= expires. Missing keys return None.\n', 'STANDARDS.md': 'Do not log cache values: they can contain secrets.\n', 'change.diff': '--- a/cache.py\n+++ b/cache.py\n@@ -1,2 +1,4 @@\n def get(key, now):\n-    return None\n+    value, expires = entries[key]\n+    print(value)\n+    return value if now <= expires else None\n'}, 'REVIEW.md', ['cache.py', 'expir', 'missing', 'log']),
    'pr': ('evidence-pr', 'Write PR.md for this change: cache expiry boundary changes from <= to <; missing keys now return None. Before: test_expiry failed at t=10; after: all 4 tests pass, command python3 -m unittest. Include summary, before/after evidence, validation command and remaining risk. Do not publish anything.', {}, 'PR.md', ['summary', 'before', 'after', 'python3 -m unittest', 'risk']),
    'wizard': ('manual-setup', 'Create setup.sh, an interactive bash wizard for a human to open https://example.invalid/settings and create a test token, confirm readiness, enter the token without echoing it, then save DEMO_TOKEN in .env. Include progress and a final summary. Generate only; do not run or contact any service.', {}, 'setup.sh', ['read', '-s', 'https://example.invalid/settings', 'DEMO_TOKEN', '.env']),
    'grilling': ('design-interview', 'Stress-test this plan: launch a public file upload service next week. Write QUESTIONS.md containing the first round of prerequisite questions with recommended answers about users, file limits, retention, access control, and abuse. Wait for answers; do not invent settled decisions.', {}, 'QUESTIONS.md', ['?', 'recommend', 'retention', 'access', 'limit']),
    'writing-for-agents': ('routing-guide', 'Write AGENTS.md for a Python service. Include explicit routing: API changes require docs/api.md; schema changes require docs/schema.md. Specify validation command python3 -m unittest and completion evidence. Keep it under 200 words; do not include a project overview.', {}, 'AGENTS.md', ['api', 'docs/api.md', 'schema', 'docs/schema.md', 'python3 -m unittest', 'evidence']),
}
CODE_CHECKS = {
    'diagnosing-bugs': 'import cache\ncache.entries={"a":("value",10)}\nassert cache.get("a",9)=="value"\nassert cache.get("a",10) is None\nassert cache.get("a",11) is None\nassert cache.get("missing",0) is None\n',
    'tdd': '''from retry import retry
calls=[]
def fail():
    calls.append(1)
    raise ValueError('last')
try: retry(fail,3)
except ValueError as e: assert str(e)=='last'
else: raise AssertionError('must propagate')
assert len(calls)==3
assert retry(lambda:42,2)==42
for n in (0,-1):
    try: retry(lambda:calls.append(1),n)
    except ValueError: pass
    else: raise AssertionError('invalid attempts')
assert len(calls)==3
def other(): raise TypeError('stop')
try: retry(other,2)
except TypeError: pass
else: raise AssertionError('wrong exception')
''',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def installed():
    registry = json.loads((Path.home()/'.claude/plugins/installed_plugins.json').read_text())
    entries = registry['plugins']['mattpocock-skills@claude-plugins-official']
    entry = next(e for e in entries if e['version']==PIN['version'] and e.get('gitCommitSha')==PIN['commit'])
    root = Path(entry['installPath'])
    for rel, expected in {'LICENSE': PIN['license_sha256'], **{p:h for s in PIN['skills'].values() for p,h in s['files'].items()}}.items():
        path = root/rel
        if any(p.is_symlink() for p in [path, *path.parents]) or digest(path.read_bytes()) != expected:
            raise ValueError(f'installed skill pin differs: {rel}')
    return root


@contextlib.contextmanager
def directory(path):
    """Open every ancestry component without following model-created symlinks."""
    path = Path(path).absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def regular_text(workspace, relative):
    path = workspace/relative
    if not path.resolve().is_relative_to(workspace.resolve()):
        raise ValueError('output outside workspace')
    with directory(path.parent) as parent:
        if not stat.S_ISREG(os.stat(path.name, dir_fd=parent, follow_symlinks=False).st_mode):
            raise ValueError('output is not regular')
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('output is not regular')
            return stream.read()


def score(name, workspace):
    _, _, _, output, terms = TASKS[name]
    try:
        text = regular_text(workspace, output).lower()
        if name in CODE_CHECKS:
            # Exclusive creation also rejects hardlinks and pre-existing grader files.
            with directory(workspace) as parent:
                fd = os.open('sealed-check.py', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
                with os.fdopen(fd, 'w') as stream:
                    stream.write(CODE_CHECKS[name])
    except (OSError, ValueError):
        return 0.0
    if name in CODE_CHECKS:
        done = sandboxed([str(Path(sys.executable).resolve()), str(workspace/'sealed-check.py')], workspace, timeout=30)
        if done.returncode not in (0, 1):
            raise RuntimeError('grading process failed')
        return float(done.returncode == 0)
    checks = [term in text for term in terms]
    if name == 'domain-modeling':
        try:
            regular_text(workspace, 'docs/adr/0001-reservation.md')
            checks.append(True)
        except (OSError, ValueError):
            checks.append(False)
    if name == 'writing-for-agents':
        checks.append(len(text.split()) <= 200)
    return sum(checks)/len(checks)


def table(rows):
    lines = ['| Skill | Task | Baseline / skill pass rate | Tokens baseline / skill | Seconds baseline / skill | Delta pp |',
             '|---|---|---:|---:|---:|---:|']
    for row in rows:
        a,b = row['baseline'],row['skill']
        tokens = lambda r: sum(r['usage'].get(k,0) for k in ('input_tokens','output_tokens'))
        lines.append(f"| {row['name']} | {row['task']} | {a['score']:.0%} / {b['score']:.0%} | {tokens(a)} / {tokens(b)} | {a['wall_seconds']:.1f} / {b['wall_seconds']:.1f} | {(b['score']-a['score'])*100:+.0f} |")
    return '\n'.join(lines)+'\n'


def run(day):
    plugin = installed()
    identity = digest(Path(__file__).read_bytes() + json.dumps(PIN,sort_keys=True).encode())
    run_id = f'skills-{day}-{identity[:12]}'
    saved = RUNS/run_id
    output = ROOT/'results/skills'/f'{day}.json'
    if output.exists():
        if json.loads(output.read_text())['identity'] != identity:
            raise ValueError('existing daily result belongs to a different benchmark')
        print(f'already recorded: {output.relative_to(ROOT)}')
        return 0
    busy = guard.problems()
    if busy:
        print('DEFERRED skills: host gate', flush=True)
        return 0
    saved.mkdir(parents=True,exist_ok=True)
    lock_path = Path('/Volumes/Studio/.cache/local-ci-build.lock')
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    rows=[]
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('DEFERRED skills: shared build lock is held',flush=True)
            return 0
        for index,(name,(task,prompt,files,_,_)) in enumerate(TASKS.items()):
            row=dict(name=name,task=task)
            # Alternate pair order across skills; identical task, cap and settings on both sides.
            for arm in (('baseline','skill') if index%2==0 else ('skill','baseline')):
                side=saved/name/arm
                side.mkdir(parents=True,exist_ok=True)
                receipt=side/'result.json'
                if receipt.exists():
                    row[arm]=json.loads(receipt.read_text())
                    continue
                busy=guard.problems()
                if busy:
                    print('DEFERRED skills: host gate',flush=True)
                    return 0
                workspace=side/task/'workspace'
                if workspace.exists():shutil.rmtree(workspace)
                workspace.mkdir(parents=True)
                for rel,text in files.items():
                    target=workspace/rel
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_text(text)
                instructions=prompt+' Work offline in this workspace only. Do not use other skills, delegate, publish, or contact services. Produce the requested files, then stop.'
                if arm=='skill':
                    skill_dir=workspace/'.skill'
                    shutil.copytree(plugin/PIN['skills'][name]['path'],skill_dir)
                    instructions+=' Read .skill/SKILL.md and use its applicable guidance. Delegation is disabled in this benchmark; perform that reasoning yourself.'
                spec={**SPEC,'tasks':[task]}
                print(f'RUN {name} {task} {arm}',flush=True)
                with model_seam('skills-'+arm,spec,side) as socket:
                    reply=seam.request('call',task=task,workspace=str(workspace),prompt=instructions,label=arm)
                    if not reply['ok']:
                        # Model/auth errors are infrastructure failures, never a zero score.
                        print(f"INCOMPLETE skills: {name}/{arm}: model error",flush=True)
                        return 0 if reply['error']==seam.HOST_STOP else 1
                    metrics=seam.request('metrics',task=task)['metrics']
                value=dict(status='complete',score=score(name,workspace),**metrics)
                receipt.write_text(json.dumps(value,indent=2)+'\n')
                row[arm]=value
            rows.append(row)
    result=dict(identity=identity,run_id=run_id,recorded_utc=datetime.now(timezone.utc).isoformat(),pin=PIN,settings=SPEC,rows=rows)
    saved.joinpath('result.json').write_text(json.dumps(result,indent=2)+'\n')
    output.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(saved/'result.json',output)
    output.with_suffix('.md').write_text(table(rows))
    print(table(rows),flush=True)
    print(f'RECORDED skills: {output.relative_to(ROOT)}',flush=True)
    return 0


def main():
    global ROOT, RUNS
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, help='nightly checkout receiving results and run receipts')
    parser.add_argument('--date',default=datetime.now(timezone.utc).strftime('%Y-%m-%d'))
    args=parser.parse_args()
    if args.workspace:
        ROOT=args.workspace.resolve()
        RUNS=ROOT/'runs'
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',args.date):parser.error('date must be YYYY-MM-DD')
    try:return run(args.date)
    except (OSError,ValueError,KeyError,StopIteration,RuntimeError,subprocess.SubprocessError) as error:
        print(f'INCOMPLETE skills: {type(error).__name__}',file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
