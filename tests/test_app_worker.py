import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from bench.app_worker import native


class NativeClient(unittest.TestCase):
    def test_completion_before_turn_start_response_is_retained(self):
        with tempfile.TemporaryDirectory(prefix='native-',dir='/tmp') as tmp:
            path=str(Path(tmp)/'s');server=socket.socket(socket.AF_UNIX);server.bind(path);server.listen(1)
            def reply():
                with server.accept()[0] as conn, conn.makefile('rwb',buffering=0) as f:
                    for i in (1,2,3):
                        request=json.loads(f.readline());self.assertEqual(request['id'],i)
                        if i==3:f.write(json.dumps({'method':'turn/completed','params':{'turn':{'status':'completed'}}}).encode()+b'\n')
                        result={'thread':{'id':'t'}} if i==2 else {}
                        f.write(json.dumps({'id':i,'result':result}).encode()+b'\n')
            thread=threading.Thread(target=reply);thread.start()
            self.assertTrue(native(tmp,path,'task'));thread.join(timeout=3);server.close()
