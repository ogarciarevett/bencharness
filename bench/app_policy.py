"""Pure admission policy for the fixed subscription-only repository-task RPCs."""
import json
from pathlib import Path

MODEL, EFFORT = 'gpt-6.1-sol', 'medium'


class Policy:
    def __init__(self, work):
        self.work = str(Path(work).resolve())
        self.thread = None
        self.pending = {}
        self.started = self.turned = False

    def request(self, message):
        method, p = message.get('method'), message.get('params', {})
        allowed = {
            'initialize': {'clientInfo', 'capabilities'}, 'initialized': set(),
            'thread/start': {'model', 'cwd', 'approvalPolicy', 'sandbox', 'developerInstructions'},
            'thread/settings/update': {'threadId', 'effort', 'summary'},
            'turn/start': {'threadId', 'input', 'effort', 'summary'},
            'turn/interrupt': {'threadId', 'turnId'},
        }
        if method not in allowed or not isinstance(p, dict) or set(p) - allowed[method]:
            raise ValueError('RPC method or fields are not admitted')
        if method == 'thread/start':
            if self.started or p.get('model') != MODEL or p.get('cwd') != self.work:
                raise ValueError('thread/model/workspace mismatch')
            if p.get('sandbox', 'read-only') != 'read-only' or p.get('approvalPolicy', 'never') != 'never' or p.get('developerInstructions'):
                raise ValueError('candidate attempted to change permissions or instructions')
            self.started = True
            # Map the executor's legacy read-only request to the shared named task profile.
            # Never forward its sandbox field: it would replace the device policy.
            p = {'model': MODEL, 'cwd': self.work, 'approvalPolicy': 'never', 'ephemeral': True,
                 'serviceTier': 'standard', 'config': {'default_permissions': 'bench-device-safe'}}
        elif method.startswith(('turn/', 'thread/settings')):
            if not self.thread or p.get('threadId') != self.thread:
                raise ValueError('thread identity mismatch')
            if method != 'turn/interrupt':
                if p.get('effort', EFFORT) != EFFORT or p.get('summary', 'detailed') != 'detailed':
                    raise ValueError('effort/summary mismatch')
                p = {**p, 'effort': EFFORT, 'summary': 'detailed'}
            if method == 'turn/start':
                if self.turned or not isinstance(p.get('input'), list):
                    raise ValueError('one turn per side is admitted')
                for item in p['input']:
                    if set(item) - {'type', 'text'} or item.get('type') != 'text' or not isinstance(item.get('text'), str) or len(item['text']) > 65536:
                        raise ValueError('only bounded text input is admitted')
                self.turned = True
        if 'id' in message:
            if not isinstance(message['id'], int) or message['id'] in self.pending:
                raise ValueError('invalid/reused request id')
            self.pending[message['id']] = method
        return {**message, 'params': p}

    def response(self, message):
        method = self.pending.pop(message.get('id'), None)
        if method == 'thread/start' and 'result' in message:
            r = message['result']
            if (r.get('model') != MODEL or r.get('modelProvider') != 'openai'
                    or r.get('cwd') != self.work or r.get('approvalPolicy') != 'never'
                    or r.get('activePermissionProfile', {}).get('id') != 'bench-device-safe'
                    or r.get('reasoningEffort') != EFFORT or r.get('sandbox', {}).get('networkAccess') is not False):
                raise ValueError('app-server did not confirm the fixed model/permissions')
            self.thread = r.get('thread', {}).get('id')
        return message

