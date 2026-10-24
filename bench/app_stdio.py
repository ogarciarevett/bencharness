"""Copied into the candidate's workspace with a fixed socket path; no Codex binary or auth."""
import selectors
import socket
import sys


def relay(path):
    with socket.socket(socket.AF_UNIX) as conn:
        conn.connect(path)
        with selectors.DefaultSelector() as select:
            select.register(sys.stdin.buffer, selectors.EVENT_READ)
            select.register(conn, selectors.EVENT_READ)
            while True:
                for event, _ in select.select():
                    if event.fileobj is conn:
                        data = conn.recv(65536)
                        if not data:
                            return
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                    else:
                        data = sys.stdin.buffer.read1(65536)
                        if not data:
                            select.unregister(sys.stdin.buffer)
                            conn.shutdown(socket.SHUT_WR)
                            continue
                        conn.sendall(data)
