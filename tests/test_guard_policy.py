import unittest
from unittest.mock import patch
from bench import guard


class StickySwap(unittest.TestCase):
    def check(self, load, free, swap):
        def sample(argv, **kwargs):
            from types import SimpleNamespace
            value = f'System-wide memory free percentage: {free}%\n' if argv[0]=='memory_pressure' else f'total = 12000M used = {swap}M free = 0M'
            return SimpleNamespace(stdout=value)
        with patch.object(guard.subprocess,'run',side_effect=sample):
            return guard.problems(heavy=False,load=load)

    def test_sticky_swap_with_healthy_memory_is_admitted(self):
        self.assertEqual(self.check(5,53,8500),[])
        self.assertEqual(self.check(19.9,46,10239),[])

    def test_all_boundaries_fail_closed(self):
        for load,free,swap in [(20,53,7000),(5,45,8500),(5,53,10240),(5,29,7000),(31,60,6000),(5,60,11265)]:
            with self.subTest(load=load,free=free,swap=swap):
                self.assertTrue(self.check(load,free,swap))

    def test_low_swap_branch_and_hard_memory_floor(self):
        self.assertEqual(self.check(5,30,8191),[])
        self.assertTrue(self.check(5,30,8192))

    def test_running_trial_uses_hard_pause_thresholds(self):
        from types import SimpleNamespace
        def sample(argv,**kwargs):
            return SimpleNamespace(stdout='System-wide memory free percentage: 40%\n' if argv[0]=='memory_pressure' else 'total = 12000M used = 10500M free = 0M')
        with patch.object(guard.subprocess,'run',side_effect=sample):
            self.assertTrue(guard.problems(heavy=False,load=25))
            self.assertEqual(guard.problems(heavy=False,load=25,active=True),[])
            self.assertTrue(guard.problems(heavy=False,load=31,active=True))
