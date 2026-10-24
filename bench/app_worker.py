"""Standalone, credential-free clients copied into an offline worker control directory."""
import asyncio
import json
from pathlib import Path
import socket
import sys

MODEL = 'gpt-6.1-sol'


def native(work, address, prompt):
    with socket.socket(socket.AF_UNIX) as conn:
        conn.connect(address)
        with conn.makefile('rwb', buffering=0) as stream:
            inbox = []
            def request(id, method, params):
                stream.write(json.dumps(dict(id=id, method=method, params=params)).encode() + b'\n')
                while True:
                    line = stream.readline(1 << 20)
                    if not line:
                        raise RuntimeError('model transport closed')
                    data = json.loads(line)
                    if data.get('id') == id:
                        if 'error' in data:
                            raise RuntimeError('model RPC refused')
                        return data['result']
                    inbox.append(data)
            request(1, 'initialize', {'clientInfo': {'name':'bench-native','version':'1'},
                                     'capabilities': {'experimentalApi': True}})
            thread = request(2, 'thread/start', {'model':MODEL,'cwd':work,'sandbox':'read-only','approvalPolicy':'never'})['thread']['id']
            request(3, 'turn/start', {'threadId':thread,'input':[{'type':'text','text':prompt}]})
            while True:
                data = inbox.pop(0) if inbox else json.loads(stream.readline(1 << 20))
                if data.get('method') == 'turn/completed':
                    return data['params']['turn']['status'] == 'completed'


async def omnigent(work, shim, source, site, prompt):
    sys.path[:0] = [source, site]
    from omnigent.inner.codex_executor import CodexExecutor
    from omnigent.inner.executor import ExecutorConfig, ExecutorError, TurnComplete
    executor = CodexExecutor(cwd=work, codex_path=shim, model=MODEL, skills_filter='none',
                             enable_web_search=False, bundle_dir=None, os_env=None)
    done, failed = False, False
    try:
        async for event in executor.run_turn([{'role':'user','content':prompt}], [], '',
                                            ExecutorConfig(model=MODEL,extra={'reasoning_effort':'medium'})):
            if isinstance(event, ExecutorError):
                failed = True
                print(json.dumps({'executor_error':event.message}), flush=True)
            done = done or isinstance(event, TurnComplete)
        return done and not failed
    finally:
        await executor.close()


if __name__ == '__main__':
    mode, work, address, shim, source, site, prompt_file = sys.argv[1:]
    prompt = Path(prompt_file).read_text()
    ok = native(work,address,prompt) if mode == 'native' else asyncio.run(omnigent(work,shim,source,site,prompt))
    print(json.dumps({'completed':ok}), flush=True)
    raise SystemExit(0 if ok else 1)
