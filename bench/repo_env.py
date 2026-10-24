"""Offline leaf sandboxes for repository tasks, with no access to sealed grader source."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import selectors
import threading
import time

from bench import safety


def table(values):
    return '{' + ','.join(json.dumps(k) + '=' + json.dumps(v) for k, v in values.items()) + '}'


def settings(work, reads=()):
    work = Path(work).resolve()
    fs = {':root': 'deny', ':minimal': 'read', str(work): 'write',
          '/System/Library/OpenSSL': 'read'}
    fs.update({str(Path(p).resolve()): 'read' for p in reads})
    # These cannot be planted before app-server loads a thread or changed by model commands.
    fs.update({str(work / '.codex'): 'deny', str(work / '.agents'): 'deny'})
    return ['default_permissions="repo-task"', 'permissions.repo-task.filesystem=' + table(fs),
            'permissions.repo-task.network.enabled=false']


def environment(work, paths=()):
    """Allow list, never a copy of the host environment or candidate source's filtering."""
    work = Path(work).resolve()
    for name in ('_home', '_tmp', '_codex'):
        (work / name).mkdir(exist_ok=True)
    return {'HOME': str(work / '_home'), 'TMPDIR': str(work / '_tmp'),
            'CODEX_HOME': str(work / '_codex'),
            'PATH': ':'.join([*map(str, paths), str(Path(sys.executable).resolve().parent), '/usr/bin', '/bin']),
            'PYTHONDONTWRITEBYTECODE': '1', 'CARGO_NET_OFFLINE': 'true', 'CARGO_BUILD_JOBS': '2',
            'CARGO_TARGET_AARCH64_APPLE_DARWIN_LINKER': '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang',
            'CC': '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang',
            'AR': '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/ar',
            'CXX': '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang++',
            'CARGO_HOME': str(work / '_cargo'),
            'SDKROOT': '/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk'}


def command(argv, work, reads=(), socket=None, writable=True, allow_children=True):
    work = Path(work).resolve()
    readable = [work, *map(lambda p: Path(p).resolve(), reads),
                Path('/private/etc/ssl/openssl.cnf'), Path('/System/Library'), Path('/usr/lib'), Path('/usr/bin'), Path('/bin'),
                Path('/Applications/Xcode.app/Contents/Developer/Toolchains'),
                Path('/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs')]
    ancestors = sorted({str(parent) for path in readable for parent in path.parents})
    paths = ' '.join('(subpath ' + json.dumps(str(p)) + ')' for p in readable)
    metadata = ' '.join('(literal ' + json.dumps(p) + ')' for p in ancestors)
    profile = ('(version 1)(allow default)(deny network*)(deny file-write*)'
               '(deny file-read* (subpath "/Users") (subpath "/Volumes") (subpath "/private") (subpath "/Library"))'
               '(allow file-read* ' + paths + ' (literal "/dev/null") (literal "/dev/urandom")'
               ' (literal "/dev/random") (literal "/private/etc/localtime"))'
               '(allow file-read-metadata ' + metadata + ')'
               '(allow file-write* (subpath ' + json.dumps(str(work if writable else work / '_tmp')) + ') (literal "/dev/null"))'
               '(deny file-read* file-write* (subpath ' + json.dumps(str(work / '.codex')) + ')'
               ' (subpath ' + json.dumps(str(work / '.agents')) + '))' + safety.device_policy())
    if not allow_children:
        profile += '(deny process-fork)'
    if socket:
        profile += '(allow network-outbound (remote unix-socket (path-literal ' + json.dumps(str(Path(socket).resolve())) + ')))'
    return ['/usr/bin/sandbox-exec', '-p', profile, *map(str, argv)]


def tools():
    """Use installed, resolved executables; do not execute rustup or download a toolchain."""
    rust = Path.home() / '.rustup/toolchains/stable-aarch64-apple-darwin/bin'
    node = Path(shutil.which('node') or '/nonexistent').resolve()
    cargo = rust / 'cargo'
    if not cargo.is_file() or not node.is_file():
        raise RuntimeError('offline Rust/Node toolchains are unavailable')
    registry = (Path.home() / '.cargo/registry').resolve()
    return rust, node.parent, registry


def prepare_cargo(work, registry):
    home = Path(work) / '_cargo'
    home.mkdir(exist_ok=True)
    if not (home / 'registry').exists():
        (home / 'registry').symlink_to(Path(registry).resolve(), target_is_directory=True)


def execute(argv, work, reads=(), timeout=120, paths=(), input=None, memory_mb=None, writable=False, allow_children=False, monitor=None):
    """Bound submitted runtimes; allow_children opts into trusted compiler execution.

    Trusted tools must not daemonize: cleanup covers their original process group
    and descendants still observable by ancestry, not already detached sessions.
    Submitted runtimes cannot fork or spawn and cannot evade this ownership.
    """
    from bench.app_broker import stop
    if memory_mb is not None and allow_children:
        raise ValueError('resident-memory bounds require child process creation to be disabled')
    process = subprocess.Popen(command(argv, work, reads, writable=writable, allow_children=allow_children), env=environment(work, paths), cwd=work,
                               stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    if input is not None:
        def feed():
            try:
                process.stdin.write(input.encode())
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        threading.Thread(target=feed, daemon=True).start()
    output = {process.stdout: bytearray(), process.stderr: bytearray()}
    started, sampled, monitored, failure = time.monotonic(), 0, 0, ''
    group_closed = False
    try:
        with selectors.DefaultSelector() as selector:
            for stream in output: selector.register(stream, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                if process.poll() is not None and not group_closed:
                    try: os.killpg(process.pid, __import__('signal').SIGKILL)
                    except ProcessLookupError: pass
                    group_closed = True
                if monitor and now-monitored>=1:
                    monitored=now;monitor()
                if now-started >= timeout:
                    failure = 'execution time limit'; break
                if memory_mb and now-sampled >= .25:
                    sampled = now
                    rows = subprocess.run(['/bin/ps','-p',str(process.pid),'-o','rss='],capture_output=True,text=True).stdout.strip()
                    if rows and int(rows) > memory_mb*1024:
                        failure = 'execution memory limit'; break
                for event, _ in selector.select(timeout=.1):
                    data = os.read(event.fileobj.fileno(),65536)
                    if not data:
                        selector.unregister(event.fileobj); continue
                    output[event.fileobj].extend(data)
                    if len(output[event.fileobj]) > 1_000_000:
                        failure = 'execution output limit'; break
                if failure: break
        if failure: stop(process)
        else:
            try: process.wait(timeout=max(.01,timeout-(time.monotonic()-started)))
            except subprocess.TimeoutExpired: failure='execution time limit';stop(process)
        stdout, stderr = (bytes(output[p]).decode(errors='replace') for p in (process.stdout,process.stderr))
        return subprocess.CompletedProcess(argv,124 if failure else process.returncode,stdout,stderr+('\n'+failure if failure else ''))
    finally:
        stop(process)
        # Compilers may fork; their session group still exists after its root exits.
        if not group_closed:
            try: os.killpg(process.pid, __import__('signal').SIGKILL)
            except ProcessLookupError: pass
        for stream in output: stream.close()
