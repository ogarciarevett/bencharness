"""Trusted headless grader; no sealed checks are placed in a timed worker's filesystem grants."""
import json
from pathlib import Path
import shutil
import sys
from html.parser import HTMLParser

from bench import guard, repo_env
from suites.desktop import grade, task

FRONTEND = r'''
const fs=require('fs'), stringify=JSON.stringify.bind(JSON);
const backend=JSON.parse(fs.readFileSync(0,'utf8'));
const React=require('react'), {renderToStaticMarkup}=require('react-dom/server');
const {initial,reduce}=require('./dist/state'), {loadJobs}=require('./dist/bridge'), {App}=require('./dist/App');
const freeze=x=>{if(x&&typeof x==='object'){Object.values(x).forEach(freeze);Object.freeze(x)}return x};
(async()=>{
freeze(backend.jobs);
const initialCopy=stringify(initial), a=reduce(freeze(initial),{type:'loading',request:1});
const b=reduce(freeze(a),{type:'snapshot',request:1,rev:backend.rev,jobs:backend.jobs});
const before=stringify(b), c=reduce(freeze(b),{type:'loading',request:2});
const stale=reduce(freeze(c),{type:'snapshot',request:1,rev:999,jobs:[]});
const oldError=reduce(c,{type:'failure',request:1,error:'old'});
const regressed=reduce(c,{type:'snapshot',request:2,rev:backend.rev-1,jobs:[]});
const accepted=reduce(c,{type:'snapshot',request:2,rev:backend.rev,jobs:backend.jobs});
const failed=reduce(c,{type:'failure',request:2,error:'offline<&>'});
let calls=[], bridgeError='';
const bridge=await loadJobs(async(...args)=>{calls.push(args);return backend});
try{await loadJobs(async()=>({ok:false,error:'FAULT'}))}catch(e){bridgeError=e.message}
const {JSDOM}=require('jsdom'), Module=require('module');
const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost'});
global.window=dom.window;global.document=dom.window.document;
global.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
const originalLoad=Module._load, entryCalls=[];
Module._load=function(name,...rest){return name==='@tauri-apps/api/core'?{invoke:async(...args)=>{entryCalls.push(args);return backend}}:originalLoad.call(this,name,...rest)};
await React.act(async()=>{require('./dist/main');await new Promise(r=>setTimeout(r,50))});
Module._load=originalLoad;
const entryHtml=dom.window.document.body.innerHTML;
const html=renderToStaticMarkup(React.createElement(App,{state:accepted}));
const loadingHtml=renderToStaticMarkup(React.createElement(App,{state:c}));
const errorHtml=renderToStaticMarkup(React.createElement(App,{state:failed}));
process.stdout.write(stringify({initial:JSON.parse(initialCopy),loading:a,loaded:b,accepted,failed,bridge,calls,bridgeError,entryCalls,entryHtml,
 stale:stale===c,staleFailure:oldError===c,regressed:regressed===c,untouched:before===stringify(b),html,loadingHtml,errorHtml}));
})().catch(()=>process.exit(1));
'''

IPC = r'''
use serde_json::{json,Value};
#[test]
fn registered_dispatch_preserves_state_across_ipc_requests() {
    let app=work_queue::desktop::configure(tauri::test::mock_builder())
        .build(tauri::test::mock_context(tauri::test::noop_assets())).unwrap();
    let window=tauri::WebviewWindowBuilder::new(&app,"main",Default::default()).build().unwrap();
    let invoke=|request:Value| -> Value {
        tauri::test::get_ipc_response(&window,tauri::webview::InvokeRequest {
            cmd:"dispatch".into(),callback:tauri::ipc::CallbackFn(0),error:tauri::ipc::CallbackFn(1),
            url:"tauri://localhost".parse().unwrap(),
            body:tauri::ipc::InvokeBody::Json(json!({"request":request})),
            headers:Default::default(),invoke_key:tauri::test::INVOKE_KEY.to_string(),
        }).unwrap().deserialize::<Value>().unwrap()
    };
    let add=json!({"op":"add","key":"ipc","expectedRev":0,"id":"ipc","title":" IPC ","priority":2});
    let first=invoke(add.clone());assert_eq!(first["ok"],true);assert_eq!(first["job"]["title"],"IPC");
    let list=invoke(json!({"op":"list"}));assert_eq!(list["rev"],1);assert_eq!(list["jobs"][0]["id"],"ipc");
    assert_eq!(invoke(add),first);
}
'''


def copy_submission(app, destination):
    """Do not follow submitted symlinks or let a submission replace build/dependency metadata."""
    app, destination = Path(app), Path(destination)
    try:linked=any((app / name).is_symlink() for name in ('', 'src', 'src-tauri', 'src-tauri/src'))
    except OSError as error:raise ValueError('unreadable source parent') from error
    if linked:
        raise ValueError('source parent directories cannot be symlinks')
    for name, expected in task.FILES.items():
        if name in ('package.json','tsconfig.json','src-tauri/Cargo.toml','build.py','check.py'):
            path = app / name
            try:valid=path.is_file() and not path.is_symlink() and path.read_text()==expected
            except OSError:valid=False
            if not valid:
                raise ValueError('protected task metadata was changed')
    task.stage(destination)
    total = 0
    for name in ('src','src-tauri/src'):
        source = app / name
        if source.is_symlink():
            raise ValueError('source directory is a symlink')
        for path in source.rglob('*'):
            if path.is_symlink():
                raise ValueError('submitted symlinks are refused')
            if path.is_file():
                total += path.stat().st_size
                if total > 2_000_000:
                    raise ValueError('submission is too large')
                target = destination / path.relative_to(app)
                target.parent.mkdir(parents=True,exist_ok=True)
                try:data=path.read_bytes()
                except OSError as error:raise ValueError('unreadable submitted source') from error
                target.write_bytes(data)
    # The JSON wire driver is fixed; only application library/modules are submitted.
    (destination/'src-tauri/src/main.rs').write_text(task.FILES['src-tauri/src/main.rs'])


class DOM(HTMLParser):
    def __init__(self, html):
        super().__init__();self.nodes=[];self.stack=[];self.feed(html)
    def handle_starttag(self, tag, attrs):
        node={'tag':tag,'attrs':dict(attrs),'text':''};self.nodes.append(node);self.stack.append(node)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i]['tag']==tag:self.stack=self.stack[:i];break
    def handle_data(self, text):
        for node in self.stack:node['text']+=text
    def find(self, tag=None, **attrs):
        return [n for n in self.nodes if (tag is None or n['tag']==tag) and all(n['attrs'].get(k)==v for k,v in attrs.items())]


def frontend_ok(v, backend):
    jobs, rev = backend['jobs'], backend['rev']
    expected = dict(rev=rev,jobs=jobs,request=1,loading=False,error=None)
    dom,entry,loading,error=map(DOM,(v['html'],v['entryHtml'],v['loadingHtml'],v['errorHtml']))
    def job_list(view):
        nodes=view.find('li')
        return ([n['attrs'].get('data-id') for n in nodes]==[j['id'] for j in jobs]
                and all(n['attrs'].get('data-status')==j['status'] and j['title'] in n['text'] for n,j in zip(nodes,jobs)))
    return (v['initial']==dict(rev=0,jobs=[],request=0,loading=False,error=None)
            and v['loading']==dict(rev=0,jobs=[],request=1,loading=True,error=None)
            and v['loaded']==expected and v['accepted']=={**expected,'request':2}
            and all(v[k] for k in ('stale','staleFailure','regressed','untouched'))
            and v['bridge']==dict(rev=rev,jobs=jobs)
            and v['calls']==[['dispatch',{'request':{'op':'list'}}]] and v['bridgeError']=='FAULT'
            and v['entryCalls'] and all(call==['dispatch',{'request':{'op':'list'}}] for call in v['entryCalls'])
            and v['failed']=={**expected,'request':2,'error':'offline<&>'}
            and any(' '.join(n['text'].split())=='Work queue' for n in dom.find('h1'))
            and job_list(dom) and job_list(entry) and dom.find('ul',**{'aria-busy':'false'})
            and loading.find('ul',**{'aria-busy':'true'}) and any('Loading' in n['text'] for n in loading.find(role='status'))
            and any('offline<&>' in n['text'] for n in error.find(role='alert')))


def score(app, tooling, out):
    app, tooling, out = Path(app), Path(tooling), Path(out)
    work=out/'grade'
    if work.exists(): raise ValueError('grading workspace must be fresh')
    out.mkdir(parents=True,exist_ok=True)
    try:copy_submission(app,work)
    except (ValueError,FileNotFoundError,NotADirectoryError):
        checks={key:False for key in grade.checks(None,None)}
        checks['tauri_mock_ipc']=False
        return dict(score=0,checks=checks,invalid_submission=True)
    for name in ('node_modules','.rust-deps'):
        (work/name).symlink_to((tooling/name).resolve(),target_is_directory=True)
    shutil.copy2(tooling/'src-tauri/Cargo.lock',work/'src-tauri/Cargo.lock')
    rust,node,registry=repo_env.tools()
    repo_env.prepare_cargo(work,registry)
    reads=[rust.parent,node.parent,registry,(tooling/'node_modules').resolve(),(tooling/'.rust-deps').resolve(),Path(sys.executable).resolve().parent.parent]
    def monitor():
        if guard.problems(heavy=False,active=True):raise guard.HostBusy('host grading pause')
    def execute(args, input=None, timeout=20, memory_mb=None, **permissions):
        if guard.problems(heavy=False,active=not permissions.get('allow_children',False)):
            raise guard.HostBusy('host grading gate')
        return repo_env.execute(args,work,reads,paths=[rust,node],input=input,timeout=timeout,memory_mb=memory_mb,monitor=monitor,**permissions)
    build=execute([sys.executable,'build.py'],timeout=90,writable=True,allow_children=True)
    (out/'build.log').write_text(build.stdout+build.stderr)
    def backend(data):
        result=execute([work/'.bench-core'],json.dumps(data),timeout=5,memory_mb=256)
        if result.returncode: raise ValueError('submitted backend failed')
        return json.loads(result.stdout)
    (work/'frontend.cjs').write_text(FRONTEND)
    def frontend(data):
        result=execute([node/'node','frontend.cjs'],json.dumps(data),timeout=10,memory_mb=768)
        return result.returncode==0 and frontend_ok(json.loads(result.stdout),data)
    result=grade.score(backend,frontend)
    result['build_exit_code']=build.returncode
    # Use Tauri's MockRuntime: this creates no native window and touches no device.
    cached=tooling/'src-tauri/target'
    if cached.is_dir():
        shutil.copytree(cached,work/'src-tauri/target')
        target=work/'src-tauri/target/debug'
        for pattern in ('.fingerprint/work-queue-*','deps/*work_queue*','incremental/work_queue*'):
            for path in target.glob(pattern):
                shutil.rmtree(path) if path.is_dir() else path.unlink()
    (work/'src-tauri/tests').mkdir(exist_ok=True)
    (work/'src-tauri/tests/hidden_ipc.rs').write_text(IPC)
    compiled=execute([rust/'cargo','test','--offline','--locked','--features','desktop','--manifest-path','src-tauri/Cargo.toml','--test','hidden_ipc','--no-run','--message-format=json'],timeout=180,writable=True,allow_children=True)
    candidates=[v.get('executable') for v in map(json.loads,compiled.stdout.splitlines())
                if v.get('reason')=='compiler-artifact' and v.get('target',{}).get('name')=='hidden_ipc' and v.get('executable')]
    ipc=None
    if compiled.returncode==0 and len(candidates)==1 and Path(candidates[0]).resolve().is_relative_to(work.resolve()):
        ipc=execute([candidates[0]],timeout=20,memory_mb=768)
    (out/'ipc.log').write_text(compiled.stdout+compiled.stderr+(ipc.stdout+ipc.stderr if ipc else ''))
    result['ipc_exit_code']=ipc.returncode if ipc else compiled.returncode or 125
    result['checks']['tauri_mock_ipc']=ipc is not None and ipc.returncode==0
    result['score']=sum(result['checks'].values())/len(result['checks'])
    return result
