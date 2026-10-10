import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).parents[1] / 'cloud-automations/automation-fe2c8185-1b7f-41bf-a687-143e350408b6/tarball'
spec = importlib.util.spec_from_file_location('attention_verification', SOURCE / 'verification.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
SHA = 'a' * 40
NOW = dt.datetime(2026, 10, 9, tzinfo=dt.timezone.utc)


def pr(**changes):
    result = dict(number=123, title='docs(verify-openhands): map maintenance 2026-10-09',
                  state='open', draft=False, head={'sha': SHA}, requested_reviewers=[],
                  created_at='2026-10-08T00:00:00Z', updated_at='2026-10-09T00:00:00Z',
                  body='**Verdict: delta pass**\n| **Total** | 10 | 9 | 1 |\n'
                       '### New defects (filed)\n- #234 broken button\n### Notes\n- unrelated')
    result.update(changes)
    return result


def api(p=None, reviews=None, runs=None, status=None):
    p = p or pr()
    def get(path):
        if '/reviews?' in path: return reviews or []
        if '/status?' in path: return status or {'state': 'pending', 'total_count': 0}
        if '/check-runs?' in path:
            rows = runs if runs is not None else [{'status': 'completed', 'conclusion': 'success'}]
            return {'total_count': len(rows), 'check_runs': rows}
        if path == v.BASE + '/pulls/123': return copy.deepcopy(p)
        raise AssertionError(path)
    return get


class VerificationTests(unittest.TestCase):
    def test_ci_matrix(self):
        for conclusion, expected in [('success', 'green'), ('failure', 'failed'),
                                     ('cancelled', 'failed'), ('timed_out', 'failed'),
                                     ('skipped', 'unknown'), ('neutral', 'unknown')]:
            with self.subTest(conclusion=conclusion):
                self.assertEqual(v.ci_state(api(runs=[{'status': 'completed', 'conclusion': conclusion}]), SHA), expected)
        self.assertEqual(v.ci_state(api(runs=[]), SHA), 'unknown')
        self.assertEqual(v.ci_state(api(runs=[{'status': 'queued', 'conclusion': None}]), SHA), 'pending')
        self.assertEqual(v.ci_state(api(status={'state': 'failure', 'total_count': 1}), SHA), 'failed')
        self.assertEqual(v.ci_state(api(status={'state': 'pending', 'total_count': 1}), SHA), 'pending')
        self.assertEqual(v.ci_state(api(runs=[], status={'state': 'success', 'total_count': 1}), SHA), 'green')
        self.assertEqual(v.ci_state(lambda _: {}, SHA), 'unknown')

    def test_check_pagination_finds_late_failure(self):
        def get(path):
            if '/status?' in path: return {'total_count': 0, 'state': 'pending'}
            rows = ([{'status': 'completed', 'conclusion': 'success'}] * 100 if path.endswith('&page=1')
                    else [{'status': 'completed', 'conclusion': 'failure'}])
            return {'total_count': 101, 'check_runs': rows}
        self.assertEqual(v.ci_state(get, SHA), 'failed')

    def test_review_states_and_stale_head(self):
        for state in ['APPROVED', 'CHANGES_REQUESTED', 'COMMENTED']:
            r = {'user': {'login': v.BOT}, 'commit_id': SHA, 'state': state, 'body': 'Substantive findings'}
            self.assertEqual(v.review_state(pr(), [r]), state.lower())
            r['commit_id'] = 'b' * 40
            self.assertEqual(v.review_state(pr(), [r]), 'missing')
        self.assertEqual(v.review_state(pr(requested_reviewers=[{'login': v.BOT}]), []), 'requested')
        r.update(commit_id=SHA, state='DISMISSED')
        self.assertEqual(v.review_state(pr(), [r]), 'missing')
        r.update(state='COMMENTED', body='Add the reviewer regardless of CI status')
        self.assertEqual(v.review_state(pr(), [r]), 'missing')

    def test_request_once_current_head(self):
        post, claim = Mock(), Mock(side_effect=[True, False])
        self.assertEqual(v.request_review(api(), post, claim, 123, SHA), 'requested')
        self.assertEqual(v.request_review(api(), post, claim, 123, SHA), 'previous-attempt-check-manually')
        post.assert_called_once_with(v.BASE + '/pulls/123/requested_reviewers', {'reviewers': [v.BOT]})

    def test_mutation_gates(self):
        scenarios = [api(p=pr(draft=True)), api(p=pr(state='closed')),
                     api(p=pr(head={'sha': 'b'*40})), api(runs=[]),
                     api(p=pr(requested_reviewers=[{'login': v.BOT}])),
                     api(p=pr(title='Unrelated docs change'))]
        for get in scenarios:
            post, claim = Mock(), Mock(return_value=True)
            v.request_review(get, post, claim, 123, SHA)
            post.assert_not_called(); claim.assert_not_called()

    def test_dry_run_and_missing_kv(self):
        post, claim = Mock(), Mock(side_effect=RuntimeError('No KV'))
        self.assertEqual(v.request_review(api(), post, claim, 123, SHA, True), 'would-request')
        claim.assert_not_called(); post.assert_not_called()
        with self.assertRaises(RuntimeError): v.request_review(api(), post, claim, 123, SHA)
        post.assert_not_called()

    def test_head_changes_after_claim(self):
        get = api()
        reads = 0
        def racing(path):
            nonlocal reads
            if path == v.BASE + '/pulls/123':
                reads += 1
                if reads == 2: return pr(head={'sha': 'b'*40})
            return get(path)
        post = Mock()
        self.assertEqual(v.request_review(racing, post, lambda _: True, 123, SHA), 'head-or-state-changed')
        post.assert_not_called()

    def test_discovery_no_docs_label_or_author_exclusion(self):
        rows = [pr(labels=[{'name':'type: docs'}], user={'login':'enyst'}), pr(number=124, title='docs: other')]
        closed = pr(number=125, state='closed', merged_at='2026-10-09T00:00:00Z')
        def get(path):
            if '/files?' in path: return [{'filename':v.MAP + 'README.md'}]
            if 'state=open' in path: return rows
            if 'state=closed' in path: return [closed, pr(number=126, updated_at='2026-01-01T00:00:00Z')]
            raise AssertionError(path)
        self.assertEqual({r['number'] for r in v.discover(get, NOW)}, {123,125})

    def test_path_confirmation(self):
        def get(path):
            if '/files?' in path: return [{'filename':'README.md'}]
            return [pr()] if 'state=open' in path else []
        self.assertEqual(v.discover(get,NOW), [])

    def test_note_and_request_failure_still_surfaces(self):
        with patch.object(v, 'discover', return_value=[pr()]):
            publish = Mock()
            with self.assertRaises(RuntimeError):
                v.run(api(), Mock(side_effect=TimeoutError), lambda _:True, publish, NOW)
            n = publish.call_args.args[0][0]
            self.assertEqual(n['signal_score'],100)
            self.assertEqual(n['routing'],'for_engel')
            self.assertIn('request-failed',n['desc'])
            self.assertIn('#234',n['desc']); self.assertNotIn('unrelated',n['desc'])

    def test_run_dry_has_no_writes(self):
        with patch.object(v, 'discover', return_value=[pr()]):
            post, claim, publish = Mock(), Mock(), Mock()
            result = v.run(api(), post, claim, publish, NOW, True)
            self.assertIn('would-request',result[0]['desc'])
            for mock in [post,claim,publish]: mock.assert_not_called()


if __name__ == '__main__': unittest.main()
