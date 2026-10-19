import re
import unittest
from test_dead_code_deploy import deploy
from run_dead_code_canary import canary_body, TARGET


class CanaryPromptTests(unittest.TestCase):
    def test_prompts_do_not_reference_source_pr_even_negatively(self):
        for body in (deploy.request_body(), canary_body()):
            self.assertIsNone(re.search(r'18417|Vasco|method reference|historical diff', body['prompt'], re.I))

    def test_independent_pinned_discovery_allows_draft(self):
        body = canary_body()
        self.assertEqual(body['repos'][0]['ref'], TARGET)
        self.assertIn(TARGET, body['prompt'])
        self.assertNotIn('inspect open PRs', body['prompt'])
        self.assertIn('open ONE draft PR', body['prompt'])
        self.assertIn('do not query other cleanup PRs', body['prompt'])
        self.assertFalse(body['enabled'])
        self.assertEqual(body['state'], 'INACTIVE')
        self.assertTrue(body['keep_alive'])
        self.assertIn('DEAD_CODE_PATCH.diff', body['prompt'])
