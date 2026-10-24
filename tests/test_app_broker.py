import tempfile
import unittest


class BrokerTransport(unittest.TestCase):
    def test_deadline_closes_an_unconnected_broker_without_model_startup(self):
        from pathlib import Path
        from bench.app_broker import Broker
        with tempfile.TemporaryDirectory() as tmp:
            b = Broker(tmp, [], {}, Path(tmp) / 'logs', budget=0.05)
            b.timer.join(timeout=3)
            self.assertTrue(b.closed)
            self.assertFalse(b.thread.is_alive())
            self.assertIsNone(b.proc)
            self.assertFalse(b.path.exists())

    def test_cleanup_kills_a_child_that_ignores_term_after_root_exits(self):
        import os, signal, subprocess, sys
        from bench.app_broker import stop
        child_code = 'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print("ready",flush=True);time.sleep(60)'
        root_code = 'import subprocess,sys,time;p=subprocess.Popen([sys.executable,"-c",'+repr(child_code)+'],stdout=subprocess.PIPE,text=True);p.stdout.readline();print(p.pid,flush=True);time.sleep(60)'
        root = subprocess.Popen([sys.executable,'-c',root_code],stdout=subprocess.PIPE,text=True,start_new_session=True)
        child = int(root.stdout.readline())
        try:
            stop(root)
            state = subprocess.run(['/bin/ps','-p',str(child),'-o','stat='],capture_output=True,text=True).stdout.strip()
            self.assertTrue(not state or state.startswith('Z'), state)
        finally:
            for pid in (child, root.pid):
                try: os.kill(pid, signal.SIGKILL)
                except ProcessLookupError: pass
            root.wait()
            root.stdout.close()

    def test_stdio_eof_drains_the_remaining_server_response(self):
        import socket, subprocess, sys, threading
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix='relay-',dir='/tmp') as tmp:
            path = str(Path(tmp)/'s')
            server = socket.socket(socket.AF_UNIX);server.bind(path);server.listen(1)
            def reply():
                with server.accept()[0] as conn:
                    while conn.recv(1024): pass
                    try: conn.sendall(b'response after EOF\n')
                    except BrokenPipeError: pass
            thread = threading.Thread(target=reply);thread.start()
            code = 'from bench.app_stdio import relay;relay('+repr(path)+')'
            result = subprocess.run([sys.executable,'-c',code],input=b'request',capture_output=True,timeout=5)
            thread.join(timeout=2);server.close()
            self.assertEqual(result.stdout,b'response after EOF\n')

    @unittest.skipUnless(__import__('sys').platform == 'darwin', 'requires installed Codex')
    def test_broker_eof_drains_initialization_without_an_inference_turn(self):
        import json, socket
        from pathlib import Path
        from bench import repo_env, safety
        from bench.app_broker import Broker
        if not safety.CODEX.is_file(): self.skipTest('Codex unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            b = Broker(tmp, [], repo_env.environment(tmp), Path(tmp)/'logs', budget=15)
            try:
                with socket.socket(socket.AF_UNIX) as conn:
                    conn.settimeout(10);conn.connect(str(b.path))
                    conn.sendall(json.dumps({'id':1,'method':'initialize','params':{'clientInfo':{'name':'eof-test','version':'1'}}}).encode()+b'\n')
                    conn.shutdown(socket.SHUT_WR)
                    data = b''.join(iter(lambda: conn.recv(65536),b''))
                    self.assertTrue(any(v.get('id')==1 and 'result' in v for v in map(json.loads,data.splitlines())))
            finally: b.close()

    def test_pending_second_request_survives_an_eof_signal_from_the_first(self):
        import json, socket, subprocess, sys, threading, types
        from pathlib import Path
        from unittest.mock import patch
        from bench import app_broker
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)/'reply-b'
            fake = ('import sys,json,time,pathlib\n'
                    'for line in sys.stdin:\n'
                    ' v=json.loads(line)\n'
                    ' if v["id"]==2:\n'
                    '  while not pathlib.Path('+repr(str(release))+').exists():time.sleep(.01)\n'
                    ' sys.stdout.write(json.dumps({"id":v["id"],"result":{}})+(chr(10) if v["id"]==1 else ""));sys.stdout.flush()\n'
                    ' if v["id"]==2:break\n')
            proxy = types.SimpleNamespace(Popen=lambda *a,**kw: subprocess.Popen([sys.executable,'-u','-c',fake],**{k:v for k,v in kw.items() if k!='cwd'}),
                                          PIPE=subprocess.PIPE,check_output=subprocess.check_output,TimeoutExpired=subprocess.TimeoutExpired)
            with patch.object(app_broker,'subprocess',proxy):
                b = app_broker.Broker(tmp,[],{},Path(tmp)/'logs',budget=5)
                # Force the former stale-event interleaving; the synchronized version has no event.
                if hasattr(b,'drained'):
                    paused, admitted = threading.Event(), threading.Event()
                    old_set, old_clear = b.drained.set, b.drained.clear
                    def delayed_signal():
                        if not paused.is_set(): paused.set();admitted.wait(3)
                        old_set()
                    def admit():
                        old_clear()
                        if paused.is_set(): admitted.set()
                    b.drained.set, b.drained.clear = delayed_signal, admit
                try:
                    with socket.socket(socket.AF_UNIX) as conn:
                        conn.settimeout(2);conn.connect(str(b.path))
                        f = conn.makefile('rwb',buffering=0)
                        def send(id): f.write(json.dumps({'id':id,'method':'initialize','params':{}}).encode()+b'\n')
                        send(1);self.assertEqual(json.loads(f.readline())['id'],1)
                        send(2);conn.shutdown(socket.SHUT_WR)
                        conn.settimeout(.2)
                        with self.assertRaises(socket.timeout): conn.recv(1)
                        release.touch();conn.settimeout(2)
                        self.assertEqual(json.loads(f.readline())['id'],2)
                        f.close()
                finally: b.close()

    def test_deadline_closes_a_client_that_stops_reading(self):
        import json,socket,sys
        from pathlib import Path
        from unittest.mock import patch
        from bench import app_broker
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=root/'fake-server'
            fake.write_text('#!'+sys.executable+'\nimport json,sys,time\nfor line in sys.stdin:\n print(json.dumps({"method":"notice","params":{"text":"x"*1000000}}),flush=True)\n time.sleep(60)\n')
            fake.chmod(0o755)
            with patch.object(app_broker.safety,'WRAPPER',fake):
                b=app_broker.Broker(root,[],{},root/'logs',budget=1)
                conn=socket.socket(socket.AF_UNIX)
                try:
                    conn.connect(str(b.path))
                    conn.sendall(json.dumps({'id':1,'method':'initialize','params':{}}).encode()+b'\n')
                    b.timer.join(timeout=3)
                    self.assertGreater((root/'logs/events.jsonl').stat().st_size,900000)
                    self.assertTrue(b.closed,'deadline blocked behind the unread socket')
                finally:
                    conn.close();b.close();b.timer.join(timeout=3)

    def test_validation_error_is_published_before_expiry_snapshot(self):
        self._assert_error_snapshot(None)

    def test_oversized_frame_error_is_published_before_expiry_snapshot(self):
        self._assert_error_snapshot(b'x'*(1<<20))

    def _assert_error_snapshot(self,payload):
        import json,socket,sys,threading
        from pathlib import Path
        from unittest.mock import patch
        from bench import app_broker
        entered,release,snapped=threading.Event(),threading.Event(),threading.Event()
        class Errors(list):
            def append(self,error):
                entered.set();release.wait(3);super().append(error)
        class Observed(threading.Condition):
            def __exit__(self,*args):
                result=super().__exit__(*args)
                if threading.current_thread().name=='expiry-probe':snapped.set()
                return result
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=root/'fake-server'
            fake.write_text('#!'+sys.executable+'\nimport time;time.sleep(60)\n');fake.chmod(0o755)
            with patch.object(app_broker.safety,'WRAPPER',fake), patch.object(app_broker.Policy,'request',side_effect=ValueError('invalid')):
                b=app_broker.Broker(root,[],{},root/'logs',budget=5)
                b.io=Observed();b.errors=Errors();expiry=None
                conn=socket.socket(socket.AF_UNIX)
                try:
                    conn.connect(str(b.path))
                    conn.sendall(payload if payload is not None else json.dumps({'id':1,'method':'initialize','params':{}}).encode()+b'\n')
                    self.assertTrue(entered.wait(1))
                    expiry=threading.Thread(target=b.expire,name='expiry-probe');expiry.start()
                    snapped.wait(.2);release.set();expiry.join(timeout=3)
                    self.assertIn('ValueError',b.expiry['errors'])
                finally:
                    release.set();conn.close();b.close()
                    if expiry is not None:expiry.join(timeout=3)
                    b.timer.join(timeout=3)

    def test_server_eof_is_published_before_expiry_snapshot(self):
        import os,socket,sys,threading
        from pathlib import Path
        from unittest.mock import patch
        from bench import app_broker
        entered,release,snapped=threading.Event(),threading.Event(),threading.Event()
        def eof():entered.set();release.wait(3)
        class Stream:
            def __init__(self,stream):self.stream=stream
            def fileno(self):return self.stream.fileno()
            def close(self):self.stream.close()
            def __iter__(self):return self
            def __next__(self):
                line=self.stream.readline()
                if not line:eof();raise StopIteration
                return line
        class Observed(threading.Condition):
            def __exit__(self,*args):
                result=super().__exit__(*args)
                if threading.current_thread().name=='eof-expiry':snapped.set()
                return result
        popen,read=app_broker.subprocess.Popen,os.read
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=root/'fake-server';fake.write_text('#!'+sys.executable+'\npass\n');fake.chmod(0o755)
            fd=[None]
            def start(args,**kwargs):
                process=popen(args,**kwargs)
                if args[0]==str(fake):process.stdout=Stream(process.stdout);fd[0]=process.stdout.fileno()
                return process
            def observed_read(handle,size):
                data=read(handle,size)
                if handle==fd[0] and not data:eof()
                return data
            with patch.object(app_broker.safety,'WRAPPER',fake),patch.object(app_broker.subprocess,'Popen',side_effect=start),patch.object(app_broker.os,'read',side_effect=observed_read):
                b=app_broker.Broker(root,[],{},root/'logs',budget=5);b.io=Observed();expiry=None
                conn=socket.socket(socket.AF_UNIX)
                try:
                    conn.connect(str(b.path));self.assertTrue(entered.wait(1))
                    expiry=threading.Thread(target=b.expire,name='eof-expiry');expiry.start()
                    snapped.wait(.2);release.set();expiry.join(timeout=3)
                    self.assertTrue(b.expiry['output_closed'])
                finally:
                    release.set();conn.close();b.close()
                    if expiry is not None:expiry.join(timeout=3)
                    b.timer.join(timeout=3)
                    if b.proc is not None:b.proc.stdin.close();b.proc.stdout.close()

    def test_client_eof_is_published_before_expiry_snapshot(self):
        import os,socket,sys,threading
        from pathlib import Path
        from unittest.mock import patch
        from bench import app_broker
        entered,release,snapped=threading.Event(),threading.Event(),threading.Event()
        def eof():entered.set();release.wait(3)
        class Input:
            def __init__(self,stream):self.stream=stream
            def __enter__(self):return self
            def __exit__(self,*args):self.stream.close()
            def readline(self,*args):
                line=self.stream.readline(*args)
                if not line:eof()
                return line
        class Observed(threading.Condition):
            def __exit__(self,*args):
                result=super().__exit__(*args)
                if threading.current_thread().name=='input-expiry':snapped.set()
                return result
        makefile,read=socket.socket.makefile,os.read
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=root/'fake-server';fake.write_text('#!'+sys.executable+'\nimport time;time.sleep(60)\n');fake.chmod(0o755)
            holder={}
            def observed_file(sock,*args,**kwargs):
                stream=makefile(sock,*args,**kwargs)
                return Input(stream) if args and args[0]=='rb' else stream
            def observed_read(handle,size):
                data=read(handle,size)
                b=holder.get('broker')
                if b is not None and b.conn is not None and handle==b.conn.fileno() and not data:eof()
                return data
            with patch.object(app_broker.safety,'WRAPPER',fake),patch.object(socket.socket,'makefile',new=observed_file),patch.object(app_broker.os,'read',side_effect=observed_read):
                b=app_broker.Broker(root,[],{},root/'logs',budget=5);b.io=Observed();holder['broker']=b;expiry=None
                conn=socket.socket(socket.AF_UNIX)
                try:
                    conn.connect(str(b.path));conn.shutdown(socket.SHUT_WR);self.assertTrue(entered.wait(1))
                    expiry=threading.Thread(target=b.expire,name='input-expiry');expiry.start()
                    snapped.wait(.2);release.set();expiry.join(timeout=3)
                    self.assertTrue(b.expiry['client_eof'])
                finally:
                    release.set();conn.close();b.close()
                    if expiry is not None:expiry.join(timeout=3)
                    b.timer.join(timeout=3)
