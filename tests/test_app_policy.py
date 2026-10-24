import unittest
from bench.app_policy import EFFORT, MODEL, Policy


class BrokerPolicy(unittest.TestCase):
    def setUp(self):
        self.policy = Policy('/tmp/work')

    def start(self):
        value = self.policy.request({'id': 1, 'method': 'thread/start',
                                     'params': {'model': MODEL, 'cwd': self.policy.work, 'sandbox':'read-only'}})
        self.policy.response({'id': 1, 'result': {'thread': {'id': 'thread-a'}, 'model':MODEL, 'modelProvider':'openai', 'cwd':self.policy.work, 'approvalPolicy':'never', 'activePermissionProfile':{'id':'bench-device-safe'}, 'reasoningEffort':EFFORT, 'sandbox':{'networkAccess':False}}})
        return value

    def test_legacy_sandbox_is_mapped_to_the_fixed_named_policy(self):
        p = self.start()['params']
        self.assertNotIn('sandbox', p)
        self.assertEqual(p['config'], {'default_permissions': 'bench-device-safe'})
        self.assertEqual(p['approvalPolicy'], 'never')
        self.assertTrue(p['ephemeral'])

    def test_rpc_configuration_or_permissions_cannot_escape(self):
        for change in [{'sandbox':'danger-full-access'}, {'config':{}}, {'dynamicTools':[]},
                       {'model':'other'}, {'cwd':'/tmp'}, {'modelProvider':'other'}]:
            with self.subTest(change=change):
                p = Policy('/tmp/work')
                params = {'model':MODEL,'cwd':p.work, **change}
                with self.assertRaises(ValueError):
                    p.request({'id':1,'method':'thread/start','params':params})

    def test_only_one_fixed_model_text_turn_and_own_thread(self):
        self.start()
        with self.assertRaises(ValueError):
            self.policy.request({'id':2,'method':'turn/start','params':{'threadId':'other','input':[]}})
        value = self.policy.request({'id':3,'method':'turn/start','params':{'threadId':'thread-a','input':[{'type':'text','text':'task'}]}})
        self.assertEqual(value['params']['effort'], EFFORT)
        with self.assertRaises(ValueError):
            self.policy.request({'id':4,'method':'turn/start','params':{'threadId':'thread-a','input':[]}})

    def test_nontrial_methods_and_tool_injection_are_refused(self):
        for method in ['account/read','thread/resume','command/exec','mcpServer/oauth/login','config/value/write']:
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.policy.request({'id':1,'method':method})
        self.start()
        with self.assertRaises(ValueError):
            self.policy.request({'id':2,'method':'turn/start','params':{'threadId':'thread-a','input':[], 'sandboxPolicy':{'type':'dangerFullAccess'}}})

    def test_server_confirmation_must_preserve_the_fixed_permissions(self):
        self.policy.request({'id':1,'method':'thread/start','params':{'model':MODEL,'cwd':self.policy.work}})
        with self.assertRaises(ValueError):
            self.policy.response({'id':1,'result':{'thread':{'id':'unsafe'}}})

