"""Shared offline-command and device policy for every bencharness child."""
import json
import os
from pathlib import Path
import subprocess
import re
import tempfile
import sys

ROOT = Path(__file__).resolve().parent.parent
CODEX = Path('/opt/homebrew/bin/codex').resolve()
WRAPPER = ROOT / 'schedule/codex-safe'
# The extension allow list is intentionally empty. Do not inherit plugins, hooks, apps or agents.
SETTINGS = ['features.plugins=false', 'features.apps=false', 'features.hooks=false',
            'features.multi_agent=false', 'features.multi_agent_v2=false',
            'features.browser_use=false', 'features.computer_use=false',
            'features.browser_use_external=false', 'features.in_app_local_automation=false',
            'features.image_generation=false', 'features.sleep_tool=false',
            'features.current_time_reminder=false',
            'features.remote_plugin=false', 'features.skill_mcp_dependency_install=false',
            'features.skip_host_skill_discovery=true',
            'model_provider="openai"', 'forced_login_method="chatgpt"',
            'web_search="disabled"', 'approval_policy="never"', 'service_tier="standard"']


def device_policy():
    # Applies to the host Codex process too, where plugin servers would otherwise escape the
    # command sandbox. Block file reads as well as exec, including interpreted MCP packages.
    names = '(devicectl|xcrun|xcodebuild|simctl|instruments|ideviceinstaller|xcodebuildmcp)'
    pattern = f'(^|/){names}(/|\\.|-|$)'
    return (f'(deny process-exec (regex #"{pattern}"))'
            f'(deny file-read* (regex #"{pattern}"))'
            '(deny iokit-open)'
            '(deny mach-lookup (global-name-regex #".*(mobiledevice|usbmux|lockdown|bonjour).*"))'
            '(deny file-read* file-write* (regex #"^/dev/(cu\\..*|tty\\..*|usb.*|bus/usb.*)$")'
            ' (literal "/var/run/usbmuxd") (literal "/private/var/run/usbmuxd"))'
            '(deny network-outbound (remote unix-socket (path-literal "/var/run/usbmuxd"))'
            ' (remote unix-socket (path-literal "/private/var/run/usbmuxd")))'
            '(deny network* (local udp "*:*"))'
            '(deny network-outbound (remote ip "localhost:*"))')


def device_command(argv):
    if sys.platform != 'darwin':
        raise RuntimeError('bencharness execution requires the macOS device sandbox')
    return ['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)' + device_policy(), *map(str, argv)]


def config_flags(settings):
    return [part for setting in settings for part in ('-c', setting)]


def configure_round2(round2):
    # Freeze the original filesystem grants before replacing CODEX with our entry point.
    original = round2.config_args()
    round2.CODEX = WRAPPER
    round2.config_args = lambda: [*original, '--ignore-rules', *config_flags(SETTINGS)]


def isolated_flags(argv, cwd):
    """Inventory is configuration-only: no server is started. Disable each inherited server.

    An empty TOML map merges with existing maps; mcp_servers={} does NOT clear them.
    """
    configs = []
    for i, value in enumerate(argv):
        if value in ('-c', '--config'):
            configs += ['-c', argv[i + 1]]
    flags = [*configs, *config_flags(SETTINGS)]
    command = [str(CODEX), 'mcp', 'list', '--json', *flags]
    listed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=30)
    if listed.returncode:
        raise RuntimeError('MCP inventory failed; refusing Codex startup')
    servers = json.loads(listed.stdout)
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+', s['name']) for s in servers):
        raise RuntimeError('unsupported MCP server name; refusing Codex startup')
    disabled = config_flags([f'mcp_servers.{s["name"]}.enabled=false' for s in servers])
    checked = subprocess.run([*command, *disabled], cwd=cwd,
                             capture_output=True, text=True, timeout=30)
    if checked.returncode or any(s['enabled'] for s in json.loads(checked.stdout)):
        raise RuntimeError('MCP allow-list check failed; refusing Codex startup')
    print('bench safety: enabled MCP servers: [] (empty allow list); plugins/apps/hooks off', file=sys.stderr)
    return [*config_flags(SETTINGS), *disabled]


def command_policy(argv):
    """Extend the caller's permissions inside Codex's single Seatbelt layer.

    macOS rejects a second sandbox_init after a restrictive policy. Keep the host
    configuration isolated and put device read denials in the command sandbox.
    The native closed-by-default policy also denies USB IOKit and all networking.
    """
    parent = ':read-only'
    clean = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ('-s', '--sandbox'):
            mode = argv[i + 1]
            if mode != 'read-only':
                raise RuntimeError('only read-only explicit sandbox mode is supported')
            i += 2
            continue
        if arg in ('-c', '--config') and argv[i + 1].startswith('default_permissions='):
            parent = argv[i + 1].split('=', 1)[1].strip('"')
        clean.append(arg)
        i += 1
    names = ('devicectl', 'xcrun', 'xcodebuild', 'simctl', 'instruments',
             'ideviceinstaller', 'xcodebuildmcp')
    denied = {f'/**/{name}': 'deny' for name in names}
    denied.update({'/**/xcodebuildmcp*/**': 'deny', '/**/xcodebuildmcp*': 'deny',
                   '/dev/cu.*': 'deny', '/dev/tty.*': 'deny', '/dev/usb*': 'deny',
                   '/dev/bus/usb': 'deny', '/private/var/run/usbmuxd': 'deny',
                   '/private/var/run/mDNSResponder': 'deny'})
    table = '{' + ','.join(json.dumps(k) + '="deny"' for k in denied) + '}'
    settings = ['default_permissions="bench-device-safe"',
                'permissions.bench-device-safe.extends=' + json.dumps(parent),
                'permissions.bench-device-safe.filesystem=' + table,
                'permissions.bench-device-safe.network.enabled=false',
                'permissions.bench-device-safe.network.dangerously_allow_all_unix_sockets=false']
    return clean, config_flags(settings)


def launch(argv):
    if not argv or argv[0] not in ('exec', 'app-server', '--probe'):
        raise SystemExit('codex-safe accepts exec, app-server (stdio), or --probe only')
    cwd = Path(argv[argv.index('-C') + 1]).resolve() if '-C' in argv else Path.cwd()
    flags = isolated_flags(argv, cwd)
    if argv[0] == '--probe':
        return
    # Use the same config sources as the checked inventory. User config is absent in our
    # temporary CODEX_HOME; an exec-only ignore flag would make those sources differ.
    argv = [arg for arg in argv if arg not in ('--ignore-user-config', '--ignore-rules')]
    argv, policy = command_policy(argv)
    if argv[0] == 'app-server' and any(arg.startswith('--listen') for arg in argv):
        raise RuntimeError('app-server transport is fixed to stdio')
    rules = ['--ignore-rules'] if argv[0] == 'exec' else []
    cmd = [str(CODEX), *argv, *rules, *flags, *policy]
    return subprocess.call(cmd, cwd=cwd)


def main():
    if sys.platform != 'darwin':
        raise SystemExit('bencharness execution requires the macOS device sandbox')
    auth_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve()
    with tempfile.TemporaryDirectory(prefix='bench-codex-') as tmp:
        # Reuse the subscription without copying or printing credentials or inheriting config,
        # plugins, hooks, caches or MCP registrations from the owner's Codex home.
        auth = auth_home / 'auth.json'
        if auth.exists():
            (Path(tmp) / 'auth.json').symlink_to(auth)
        os.environ['CODEX_HOME'] = tmp
        if sys.argv[1:2] == ['app-server']:
            os.environ['HOME'] = tmp
        raise SystemExit(launch(sys.argv[1:]))
