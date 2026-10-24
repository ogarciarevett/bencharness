"""Guarded, serial app trial. Only authenticated Codex lives outside the candidate leaf sandbox."""
import hashlib
import contextlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tempfile

from bench import app_broker, app_score, guard, repo_env, safety
from suites.desktop import task

PIN = '65be0f24685b11ad271a903331c03e3d1a408b87'
PROMPT = 'Read TASK.md and implement the application. Use python3 check.py and your own tests. Stay within the task and its offline/device restrictions.'
ROOT = Path(__file__).resolve().parent.parent


@contextlib.contextmanager
def exclusive():
    path = Path(os.environ.get('BENCHARNESS_BUILD_LOCK', ROOT / '.cache/local-ci-build.lock'))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            yield False;return
        rows=subprocess.check_output(['/bin/ps','-axo','comm=,args='],text=True)
        busy=False
        for row in rows.splitlines():
            parts=row.strip().split(None,1)
            if len(parts)!=2:continue
            executable,args=Path(parts[0]).name,parts[1]
            if executable in ('cargo','rustc') or (executable in ('npm','node') and 'npm run package' in args) or (executable=='docker' and 'build' in args):busy=True
        yield not busy


def common_root():
    path = subprocess.check_output(['git','-C',str(ROOT),'rev-parse','--path-format=absolute','--git-common-dir'],text=True).strip()
    return Path(path).parent


def assets():
    runs = common_root()/'runs'
    return runs/'desktop-tooling', runs/'omnigent-review/src', runs/'omnigent-review/venv'


def fingerprint_roots(roots):
    result=hashlib.sha256()
    for index,root in enumerate(roots):
        if root.is_symlink() or not root.exists():raise ValueError('dependency root is missing or a symlink')
        for path in ([root] if root.is_file() else sorted(p for p in root.rglob('*') if p.is_file())):
            if not path.resolve().is_relative_to(root.resolve()):raise ValueError('dependency symlink escapes root')
            result.update(json.dumps([index,str(path.relative_to(root)),path.stat().st_size]).encode())
            with path.open('rb') as stream:
                for part in iter(lambda:stream.read(1<<20),b''):result.update(part)
    return result.hexdigest()


def fingerprint(tooling):
    if not list((tooling/'.rust-deps').glob('*.rlib')):raise ValueError('staged Rust dependencies are missing')
    return fingerprint_roots([tooling/name for name in ('package.json','package-lock.json','src-tauri/Cargo.lock',
                                                       '.rust-deps','node_modules','src-tauri/target')])


def python_fingerprint(venv):
    executable=(venv/'bin/python').resolve()
    return fingerprint_roots([executable,executable.parent.parent/'lib',venv/'lib/python3.12/site-packages'])


def _refuse(spec):
    fixed = {'model':'gpt-6.1-sol','effort':'medium','service_tier':'standard',
             'budget_active_seconds':1200,'tasks':['desktop-queue'],
             'harnesses':['native-codex-app','omnigent'],'mode':'repo-app'}
    errors = [f'{k} differs from the reviewed app trial' for k,v in fixed.items() if spec.get(k)!=v]
    if spec.get('pins',{}).get('omnigent') != PIN: errors.append('Omnigent source pin differs')
    tooling, source, venv = assets()
    for path in [tooling/'node_modules/jsdom/package.json',tooling/'.rust-deps',tooling/'src-tauri/target',source/'omnigent/inner/codex_executor.py',venv/'bin/python']:
        if not path.exists():errors.append('offline app dependency is missing: '+path.name)
    if errors:return errors
    head = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    dirty = subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True).strip()
    if head!=PIN or dirty:errors.append('reviewed candidate source changed')
    pin = spec.get('suite_pin',{}).get('commit','')
    checked = subprocess.run(['git','-C',str(ROOT),'diff','--exit-code',pin,'--','suites/desktop','bench/app_score.py','bench/app_worker.py'],capture_output=True)
    if not pin or checked.returncode:errors.append('suite/scorer/client no longer matches its pin')
    if python_fingerprint(venv)!=spec.get('python_sha256'):errors.append('Python dependency fingerprint differs')
    if fingerprint(tooling)!=spec.get('tooling_sha256'):errors.append('offline dependency fingerprint differs')
    version=subprocess.check_output([str(safety.CODEX),'--version'],text=True).strip()
    if version!=spec.get('pins',{}).get('native-codex-app'):errors.append('native Codex executable version differs')
    return errors


def refuse(spec):
    try:return _refuse(spec)
    except (OSError,ValueError,subprocess.SubprocessError):return ['cannot verify pinned offline app assets']


def directory(path):
    path = Path(path).absolute()
    for parent in [*reversed(path.parents),path]:
        if parent.is_symlink():raise ValueError('trial directory is a symlink')
    path.mkdir(parents=True,exist_ok=True)
    return path


def write(path, value):
    directory(path.parent)
    # Replace planted symlinks; never follow them while writing receipts.
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as stream:
        temporary=Path(stream.name)
        stream.write(json.dumps(value,sort_keys=True)+'\n')
    try:temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def turn_reason(broker, elapsed, remaining):
    with broker.io:
        if broker.expired:return 'budget'
        if broker.errors or broker.turn_status in ('failed','interrupted') or (broker.output_closed and broker.turn_status!='completed'):return 'error'
        if elapsed>=remaining:return 'budget' if broker.receipt and broker.turn_started else 'error'
        return ''


def model_finished(reason, completed, broker):
    if completed:return True
    if reason!='budget' or broker is None:return False
    with broker.io:
        r=broker.expiry if broker.expired else {**vars(broker),'errors':list(broker.errors)}
    return bool(r['receipt'] and r['turn_started'] and not r['errors'] and not r['client_eof']
                and r['turn_status'] not in ('failed','interrupted') and not (r['output_closed'] and r['turn_status']!='completed'))


def run(spec, workdir, name):
    task_name=spec['tasks'][0]
    side=Path(workdir)/task_name;app=side/'app';control=side/'control'
    private=Path(workdir).parent/'.app-ledger'/name
    directory(private);private.chmod(0o700)
    identity=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()
    ledger=private/'state.json';canonical=private/'result.json'
    state=json.loads(ledger.read_text()) if ledger.exists() else dict(identity=identity,spent=0,attempts=0,grades=0,model_done=False)
    if state['identity']!=identity:raise ValueError('existing app receipt belongs to another spec')
    directory(side)
    if app.is_symlink() or control.is_symlink():raise ValueError('trial workspace is a symlink')
    if canonical.exists():
        result=json.loads(canonical.read_text());write(side/'result.json',result)
        return {task_name:result}
    if state.get('inflight'):
        raise RuntimeError('interrupted app attempt requires budget reconciliation; no automatic extra budget')
    if guard.problems():return {task_name:{'status':'blocked','error':'host start gate'}}
    tooling,source,venv=assets();python=(venv/'bin/python').resolve()
    rust,node,registry=repo_env.tools()
    reads=[rust.parent,node.parent,registry,tooling/'node_modules',tooling/'.rust-deps',Path(sys.executable).resolve().parent.parent]
    if not state.get('input_sha256'):
        if app.exists():shutil.rmtree(app)
        task.stage(app)
        for dependency in ('node_modules','.rust-deps'):(app/dependency).symlink_to((tooling/dependency).resolve(),target_is_directory=True)
        shutil.copy2(tooling/'src-tauri/Cargo.lock',app/'src-tauri/Cargo.lock')
        state['input_sha256']=hashlib.sha256(''.join(task.FILES.values()).encode()+task.TASK.encode()).hexdigest()
        write(ledger,state)
    env=repo_env.environment(app,[rust,node])
    if not state['model_done'] and state['spent'] < 1200:
        remaining=1200-state['spent']
        directory(control)
        state.update(attempts=state['attempts']+1)
        (control/'app-worker.py').write_text((ROOT/'bench/app_worker.py').read_text())
        (control/'prompt.txt').write_text(PROMPT)
        receipt=private/f"attempt-{state['attempts']}"
        reason='';process=None;broker=None
        started=time.monotonic()
        state['inflight']=True;write(ledger,state)
        try:
            broker=app_broker.Broker(app,reads,env,receipt,budget=remaining)
            shim=control/'codex-bridge'
            shim.write_text('#!'+str(python)+'\n'+(ROOT/'bench/app_stdio.py').read_text()+'\nrelay('+repr(str(broker.path))+')\n');shim.chmod(0o755)
            args=[python,'-I','-B',control/'app-worker.py','native' if name=='native-codex-app' else 'omnigent',app,broker.path,shim,source,venv/'lib/python3.12/site-packages',control/'prompt.txt']
            with (receipt/'worker.log').open('w') as log:
                process=subprocess.Popen(repo_env.command(args,side,[*reads,source,venv,python.parent.parent],broker.path),
                                         cwd=side,env=repo_env.environment(side,[python.parent,rust,node]),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                sampled=0
                while process.poll() is None:
                    now=time.monotonic()
                    reason=turn_reason(broker,now-started,remaining)
                    if reason:break
                    if now-sampled>=5:
                        sampled=now
                        if guard.problems(heavy=False,active=True):reason='host';break
                    time.sleep(.25)
        finally:
            if not reason and broker is not None and broker.expired:reason='budget'
            completed=process is not None and process.poll()==0 and broker is not None and broker.turn_status=='completed' and not broker.errors
            finished=model_finished(reason,completed,broker)
            if broker is not None:broker.close();broker.timer.join()
            if process is not None:app_broker.stop(process)
            state.update(inflight=False,spent=min(1200,state['spent']+time.monotonic()-started),
                         termination=reason or 'completed',receipt=broker.receipt if broker else None)
            state['model_done']=finished
            write(ledger,state)
        if reason=='host':return {task_name:{'status':'blocked','error':'host pause gate'}}
    if not state['model_done']:
        return {task_name:{'status':'error','model_error':True,'error':'app client/model transport failed; protected receipt retained'}}
    if guard.problems(heavy=False):return {task_name:{'status':'blocked','error':'host grading start gate'}}
    if python_fingerprint(venv)!=spec['python_sha256']:raise RuntimeError('Python dependencies changed during the trial')
    if fingerprint(tooling)!=spec['tooling_sha256']:raise RuntimeError('shared dependencies changed during the trial')
    state['grades']+=1;write(ledger,state)
    try:scored=app_score.score(app,tooling,private/f"grade-{state['grades']}")
    except guard.HostBusy:return {task_name:{'status':'blocked','error':'host grading gate'}}
    result={**scored,'status':'complete','pass':scored['score']==1,'wall_seconds':round(state['spent'],3),
            'model_calls':state['attempts'],'cost_estimate_usd':None,'provider_spend_issued':False,
            'termination':state['termination'],'source_pin':spec['pins'][name],
            'input_sha256':state['input_sha256'],'tooling_sha256':spec['tooling_sha256'],'python_sha256':spec['python_sha256'],
            'model':app_broker.MODEL,'effort':app_broker.EFFORT,'permissions':'bench-device-safe',
            'service_tier_requested':'standard','service_tier_confirmed':False,
            'service_tier_note':'CLI reports standard unsupported for this model and may omit it' }
    write(canonical,result);write(side/'result.json',result)
    return {task_name:result}
