"""A headless Tauri 2 app-building task; graders are never staged for the worker."""
from pathlib import Path

TASK = """Build Work Queue in this multi-file Tauri 2 / Rust / React starter.
Use the offline tools already installed. No network, devices, new dependencies,
plugins, delegation or outside-workspace files. Do not change Cargo.toml,
package.json, tsconfig.json, build.py or the public check. Implement the Rust library,
React view, TypeScript reducer/bridge and the Tauri command wiring. Run check.py
and add your own tests. build.py compiles the Rust core directly with the staged
.rust-deps and TypeScript with the staged node_modules. Use it instead of Cargo:
the native device filename policy blocks Cargo's directory cleanup. Packaging,
launching windows and device builds are out of scope.

Rust replay(input: serde_json::Value) -> Value accepts {snapshot?, requests:[]}.
Return {state,replies}. Default state is {rev:0,jobs:[],events:[],seen:{}}.
Each job has id,title,priority,status,seq. ID matches [a-z0-9-]{1,32}; trimmed
Unicode title has 1..80 characters; priority is an integer 0..3; status is one
of queued/running/done/cancelled. seq is the revision at which it was added.

Mutations add/start/finish/cancel/retry/reprioritize require nonempty string key
and integer expectedRev. Check successful replay by key BEFORE revision conflict:
identical original JSON request returns its original reply without mutation;
a different request reusing a successful key returns {ok:false,error:"KEY_REUSED"}.
Then expectedRev must equal state.rev, otherwise CONFLICT. Invalid shape/fields
return BAD_REQUEST. Missing job returns NOT_FOUND. Errors never change state or
reserve a key. Each successful mutation increments rev once, appends
{rev,op,id} to events, and returns {ok:true,rev,job}. Record {request,reply} under
seen[key]. Reject unknown operations as BAD_REQUEST. Never panic on malformed JSON.

add: requires new id,title,priority; duplicate id returns EXISTS, even terminal.
start: queued -> running; only one job may run (BUSY); only the highest-priority
queued job may start (ORDER). Order is priority descending, then seq ascending.
finish: running -> done. cancel: queued/running -> cancelled. retry:
done/cancelled -> queued, retaining seq. reprioritize: queued only, valid priority.
Other status transitions return STATE. For start, check STATE then BUSY then ORDER.

Queries do not mutate or need key/expectedRev. list returns {ok:true,rev,jobs},
ordered as above; optional status filters to a valid status, invalid -> BAD_REQUEST.
events returns {ok:true,rev,events} with event.rev > after, first limit events;
after is integer >=0, limit integer 1..100. Unknown query fields may be ignored.

Snapshots must pass validate(state:&Value)->bool before requests run. Validate
all four containers, nonnegative integer rev, rev == events.len, event revisions
exactly 1..rev, event op a known mutation and valid id; validate all job fields,
unique job IDs/seq, 1<=seq<=rev and at most one running job. seen must be an object
whose values contain object request and object reply with ok:true and integer
1<=reply.rev<=rev. Invalid snapshots return {error:"INVALID_SNAPSHOT"} only.
An empty/default snapshot is valid. Valid snapshots round-trip without data loss.

TypeScript Job mirrors Rust. State is {rev,jobs,request,loading,error}, initial
{rev:0,jobs:[],request:0,loading:false,error:null}. reduce(state,action) must be
immutable. loading(request): ignore request<=state.request; otherwise retain
jobs/rev, set request/loading=true/error=null. snapshot(request,rev,jobs): accept
only matching request and rev>=current rev; sort jobs by queue order and set
loading=false/error=null. failure(request,error): only matching request sets
loading=false/error; retain jobs/rev. All stale actions return the original state.
loadJobs(invoke) calls invoke('dispatch',{request:{op:'list'}}), rejects a reply
with ok:false as Error(reply.error), otherwise returns its jobs and rev.

App({state}) renders an h1 'Work queue', ul with one li per job in queue order,
each li data-id=id and data-status=status, displaying title; React must escape
text. Set aria-busy on the ul from loading. While loading render role=status
text 'Loading'; when error is non-null render role=alert with the error text.
Do not mutate state while rendering. main.tsx must load the list with @tauri-apps/api/core invoke and render the result.
The bridge, reducer and view must compose
with the Rust list response. Keep desktop.rs registering the dispatch command
with tauri::generate_handler![dispatch], sharing Mutex<Value> state. Export
pub fn configure<R:tauri::Runtime>(builder:tauri::Builder<R>)->tauri::Builder<R>
from desktop.rs, adding managed default queue state and its handler to the supplied
builder. This permits headless MockRuntime IPC testing; do not create real windows. The command
must apply one request through replay, persist returned state and return that
one reply; a poisoned mutex becomes an error, never panic. Tauri config is v2.
"""

FILES = {
    'src-tauri/Cargo.toml': '''[package]
name="work-queue"
version="0.1.0"
edition="2021"
[dependencies]
serde_json="=1.0.154"
tauri={version="=2.12.4",optional=true,default-features=false,features=["test"]}
[features]
desktop=["dep:tauri"]
''',
    'src-tauri/src/lib.rs': '''#[cfg(feature="desktop")]
pub mod desktop;
pub mod store;
pub mod validation;
pub fn replay(_input: serde_json::Value) -> serde_json::Value {
    serde_json::json!({"state":{"rev":0,"jobs":[],"events":[],"seen":{}},"replies":[]})
}
''',
    'src-tauri/src/store.rs': '// Implement queue operations here.\n',
    'src-tauri/src/validation.rs': 'pub fn validate(_state: &serde_json::Value) -> bool { false }\n',
    'src-tauri/src/main.rs': '''use std::io::{self, Read};
fn main() {
    let mut input=String::new(); io::stdin().read_to_string(&mut input).unwrap();
    let value=serde_json::from_str(&input).unwrap();
    println!("{}",work_queue::replay(value));
}
''',
    'src-tauri/src/desktop.rs': '// Implement the Tauri 2 dispatch command and builder wiring here.\n',
    'src-tauri/tauri.conf.json': '{"$schema":"https://schema.tauri.app/config/2","productName":"Work Queue","version":"0.1.0","identifier":"dev.bench.workqueue","build":{"frontendDist":"../dist"},"app":{"windows":[{"title":"Work Queue"}]}}\n',
    'package.json': '{"private":true,"dependencies":{"react":"19.2.7","react-dom":"19.2.7","typescript":"5.9.3","@tauri-apps/api":"2.12.4","jsdom":"26.1.0","@types/react":"19.2.17","@types/react-dom":"19.2.3"}}\n',
    'tsconfig.json': '{"compilerOptions":{"target":"ES2022","module":"CommonJS","moduleResolution":"node","jsx":"react-jsx","strict":true,"esModuleInterop":true,"skipLibCheck":true,"outDir":"dist"},"include":["src"]}\n',
    'src/state.ts': '''export type Job={id:string,title:string,priority:number,status:string,seq:number};
export type State={rev:number,jobs:Job[],request:number,loading:boolean,error:string|null};
export type Action={type:"loading",request:number}|{type:"snapshot",request:number,rev:number,jobs:Job[]}|{type:"failure",request:number,error:string};
export const initial:State={rev:0,jobs:[],request:0,loading:false,error:null};
export function reduce(state:State,_action:Action):State{return state;}
''',
    'src/bridge.ts': '''import {Job} from "./state";
export type Invoke=(command:string,args:unknown)=>Promise<any>;
export async function loadJobs(_invoke:Invoke):Promise<{rev:number,jobs:Job[]}>{return {rev:0,jobs:[]};}
''',
    'index.html': '<html><body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>\n',
    '''src/main.tsx''': '''import React from "react";
import {createRoot} from "react-dom/client";
import {App} from "./App";
import {initial} from "./state";
// Connect the Tauri invoke bridge and reducer here.
createRoot(document.getElementById("root")!).render(<App state={initial}/>);
''',
    'src/App.tsx': '''import React from "react";
import {State} from "./state";
export function App({state:_state}:{state:State}){return <h1>Work queue</h1>;}
''',
    'build.py': '''import glob,pathlib,subprocess
p=pathlib.Path("src-tauri/src/bench_main.rs")
p.write_text('extern crate self as work_queue;\\ninclude!("lib.rs");\\ninclude!("main.rs");\\n')
serde=glob.glob(".rust-deps/libserde_json-*.rlib")[0]
subprocess.run(["rustc","--edition=2021","--crate-name","work_queue","-Clinker=/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang",str(p),"--extern","serde_json="+serde,"-L",".rust-deps","-o",".bench-core"],check=True)
subprocess.run(["node","node_modules/typescript/bin/tsc"],check=True)
''',
    'check.py': '''import json,subprocess,sys
subprocess.run([sys.executable,"build.py"],check=True)
request={"op":"add","key":"k","expectedRev":0,"id":"first","title":" First ","priority":2}
r=subprocess.run(["./.bench-core"],input=json.dumps({"requests":[request,{"op":"list"}]}),text=True,capture_output=True,check=True)
v=json.loads(r.stdout); assert v["replies"][1]["jobs"][0]["title"]=="First"
print("public Rust/TypeScript check passed")
''',
}


def stage(work):
    work = Path(work)
    for name, content in FILES.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (work / 'TASK.md').write_text(TASK)
