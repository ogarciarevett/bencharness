"""One-turn stdio model broker: candidate code receives neither auth nor RPC configuration authority."""
import json
import os
from pathlib import Path
import signal
import select
import socket
import subprocess
import tempfile
import threading
import time

from bench import repo_env, safety

from bench.app_policy import EFFORT, MODEL, Policy


def stop(process):
    """Children can create process groups; terminate the observed tree before its root."""
    if process is None:
        return
    def snapshot():
        rows = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid=,lstart='], text=True)
        return {int(p[0]): (int(p[1]), ' '.join(p[2:])) for p in map(str.split, rows.splitlines())}
    observed = snapshot()
    pids = [process.pid]
    for parent in pids:
        pids.extend(pid for pid, (ppid, _) in observed.items() if ppid == parent and pid not in pids)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        live = snapshot()
        for pid in reversed(pids):
            if pid not in observed or live.get(pid, (None, None))[1] != observed[pid][1]:
                continue
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # Root exit does not prove that its observed descendants exited.



def frames(buffer,chunk):
    parts=(buffer+chunk).split(b'\n');tail=parts.pop()
    lines=[part+b'\n' for part in parts]
    if not chunk and tail:lines.append(tail);tail=b''
    return lines,tail



class Broker:
    def __init__(self, work, reads, command_env, logdir, budget=1200):
        if not 0 < budget <= 1200:
            raise ValueError('invalid broker budget')
        self.work, self.logdir = Path(work).resolve(), Path(logdir)
        self.logdir.mkdir(parents=True, exist_ok=True)
        self.logdir.chmod(0o700)
        self.tmp = tempfile.TemporaryDirectory(prefix='app-seam-', dir='/tmp')
        self.path = Path(self.tmp.name) / 'model.sock'
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(self.path))
        self.listener.listen(1)
        self.proc = self.conn = None
        self.closed = False
        self.lock = threading.Lock()
        self.errors = []
        self.turn_status = None
        self.turn_started = False
        self.expired = self.client_eof = False
        self.expiry = None
        self.receipt = None
        self.io = threading.Condition()
        self.output_closed = False
        self.config = [*repo_env.settings(work, reads),
                       'shell_environment_policy.inherit="none"',
                       'shell_environment_policy.set=' + repo_env.table(command_env),
                       'model=' + json.dumps(MODEL), 'model_reasoning_effort="medium"',
                       'model_reasoning_summary="detailed"', 'features.memories=false']
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        self.timer = threading.Timer(budget, self.expire)
        self.timer.daemon = True
        self.timer.start()

    def _serve(self):
        try:
            conn, _ = self.listener.accept()
            with self.lock:
                if self.closed:
                    conn.close()
                    return
                self.conn = conn
                conn.setblocking(False)
                # The host process has only the subscription locator, never API-key/proxy env.
                env = {'HOME': str(Path.home()), 'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                       'CODEX_HOME': os.environ.get('CODEX_HOME', str(Path.home() / '.codex')),
                       'PYTHONDONTWRITEBYTECODE': '1'}
                self.stderr = (self.logdir / 'app-server.stderr').open('w')
                self.proc = subprocess.Popen([str(safety.WRAPPER), 'app-server', *safety.config_flags(self.config)],
                                             cwd=self.work, env=env, stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=self.stderr, start_new_session=True)
                os.set_blocking(self.proc.stdin.fileno(),False)
                os.set_blocking(self.proc.stdout.fileno(),False)
            policy = Policy(self.work)
            def output():
                try:
                    with (self.logdir / 'events.jsonl').open('w') as events:
                        fd=self.proc.stdout.fileno();buffer=b''
                        while not self.closed:
                            if not select.select([fd],[],[],.1)[0]:continue
                            with self.io:
                                if self.closed:break
                                try:
                                    chunk=os.read(fd,65536)
                                    lines,buffer=frames(buffer,chunk)
                                    outgoing=[]
                                    for line in lines:
                                        data=self.validate(policy.response,line)
                                        if data.get('method')=='turn/started':self.turn_started=True
                                        if data.get('method')=='turn/completed':self.turn_status=data.get('params',{}).get('turn',{}).get('status')
                                        if data.get('result',{}).get('activePermissionProfile'):self.receipt=data['result']
                                        encoded=json.dumps(data)+'\n';events.write(encoded);outgoing.append(encoded.encode())
                                    events.flush()
                                    if not chunk:self.output_closed=True
                                    self.io.notify_all()
                                except BlockingIOError:continue
                                except Exception as error:
                                    self.errors.append(type(error).__name__);raise
                            # Reads/EOF publish under io; a stalled send never holds that lock.
                            for encoded in outgoing:
                                if not self.send(conn.fileno(),encoded):break
                            if not chunk:break
                except (OSError, ValueError) as error:
                    if not self.closed:
                        self.errors.append(type(error).__name__)
                finally:
                    with self.io:
                        self.output_closed = True
                        self.io.notify_all()
                    try:
                        conn.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
            sender = threading.Thread(target=output, daemon=True)
            sender.start()
            with (self.logdir / 'requests.jsonl').open('w') as ledger:
                buffer=b''
                while not self.closed:
                    if not select.select([conn],[],[],.1)[0]:continue
                    with self.io:
                        if self.closed:break
                        try:
                            chunk=os.read(conn.fileno(),65536)
                            lines,buffer=frames(buffer,chunk);outgoing=[]
                            for line in lines:
                                data=self.validate(policy.request,line,1<<20)
                                encoded=json.dumps(data)+'\n';ledger.write(encoded);outgoing.append(encoded.encode())
                            if len(buffer)>=1<<20:self.validate(policy.request,buffer,1<<20)
                            ledger.flush()
                            if not chunk:self.client_eof=not self.closed
                            self.io.notify_all()
                        except BlockingIOError:continue
                        except Exception as error:
                            self.errors.append(type(error).__name__);raise
                    for encoded in outgoing:
                        if not self.send(self.proc.stdin.fileno(),encoded):break
                    if not chunk:break
                # EOF half-closes input; deliver outstanding results before stopping the server.
                with self.io:
                    self.io.wait_for(lambda: self.closed or self.output_closed or
                                     (not policy.pending and (not policy.turned or self.turn_status is not None)))
        except Exception as error:
            if not self.closed:
                self.errors.append(type(error).__name__)
        finally:
            stop(self.proc)

    def send(self,fd,data):
        remaining=memoryview(data)
        while remaining and not self.closed:
            if not select.select([],[fd],[],.1)[1]:continue
            with self.io:
                if self.closed:break
                try:
                    count=os.write(fd,remaining)
                    if not count:raise OSError('transport write made no progress')
                    remaining=remaining[count:]
                except BlockingIOError:continue
                except Exception as error:
                    self.errors.append(type(error).__name__);raise
        return not remaining

    def validate(self, operation, raw, limit=None):
        with self.io:
            try:
                if limit is not None and len(raw)>=limit:raise ValueError('RPC frame too large')
                return operation(json.loads(raw))
            except Exception as error:
                self.errors.append(type(error).__name__)
                raise

    def expire(self):
        with self.io:
            self.expiry = {key:getattr(self,key) for key in ('receipt','turn_started','turn_status','client_eof','output_closed')}
            self.expiry['errors'] = list(self.errors)
            self.expired = True
        self.close()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.timer.cancel()
            try:
                self.listener.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.listener.close()
            if self.conn:
                try:
                    self.conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.conn.close()
        with self.io:
            self.io.notify_all()
        stop(self.proc)
        self.thread.join(timeout=3)
        if hasattr(self, 'stderr'):
            self.stderr.close()
        self.tmp.cleanup()
