import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1] / 'local-automations/jev-lint/tarball'
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('jev_file_check', HERE / 'main.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
focused_spec = importlib.util.spec_from_file_location("jev_lint_focused", HERE / "focused.py")
focused = importlib.util.module_from_spec(focused_spec)
with patch.dict(sys.modules, {"main": check}):
    focused_spec.loader.exec_module(focused)


class FileCheckTest(unittest.TestCase):
    def setUp(self):
        self.files = json.loads((HERE / 'fixtures/case-1.json').read_text())['files']
        self.request = check.build_request(self.files)
        self.response = {'model': check.MODEL, 'answers': {}}
        for key, q in self.request['questions'].items():
            if q['type'] == 'noul':
                value = {'type': 'noul', 'noul': 0.99}
            else:
                chosen = next(k for k in q['criteria'] if k != 'none')
                value = {'type': 'choice', 'choice': chosen, 'confidence': 1,
                         'probabilities': {k: int(k == chosen) for k in q['criteria']}}
            self.response['answers'][key] = value

    def test_typed_contract(self):
        self.assertEqual(check.validate(self.response, self.request), self.response)

    def test_reject_invalid_probability(self):
        for v in [True, -1, 2, float('nan')]:
            self.response['answers']['PY_SUPPRESS']['noul'] = v
            with self.assertRaises(check.CheckError):
                check.validate(self.response, self.request)

    def test_missing_or_unknown_answers_fail_closed(self):
        del self.response['answers']['PY_SUPPRESS']
        with self.assertRaises(check.CheckError):
            check.validate(self.response, self.request)

    def test_error_includes_alternative_and_source_identity(self):
        errors = check.feedback(self.files, self.response)
        self.assertEqual([e['code'] for e in errors], ['PY_SUPPRESS', 'BOOL_SCOPE'])
        self.assertIn('AFTER', errors[0]['alternative'])
        self.assertIn('deprecation', errors[1]['alternative'])
        self.assertEqual(len(errors[0]['source_sha256']), 64)

    def test_uncertain_signals_never_emit_error(self):
        self.response['answers']['PY_SUPPRESS']['noul'] = 0.5
        self.response['answers']['BOOL_SCOPE_location']['probabilities'] = {'none': 0.2}
        self.assertEqual(check.feedback(self.files, self.response), [])

    def test_no_location_never_emit_error(self):
        for rule in check.RULES:
            self.response['answers'][rule + '_location']['choice'] = 'none'
        self.assertEqual(check.feedback(self.files, self.response), [])

    def test_no_answer_key_or_case_identity_in_state(self):
        self.assertEqual(set(self.request['state']), {'files'})
        self.assertNotIn('original', json.dumps(self.request))

    def test_dry_run_never_reads_credential_or_calls_provider(self):
        with patch.object(check, 'credential', side_effect=AssertionError), patch.object(check, 'evaluate', side_effect=AssertionError):
            self.assertEqual(len(check.run(dry_run=True)), 6)

    def test_clean_source_has_no_invented_location(self):
        r = check.build_request({'x.py': 'answer = 42\n'})
        self.assertNotIn('PY_SUPPRESS_location', r['questions'])
        self.assertNotIn('BOOL_SCOPE_location', r['questions'])

    def test_unknown_choice_fails(self):
        self.response['answers']['BOOL_SCOPE_location']['choice'] = 'hallucination:123'
        with self.assertRaises(check.CheckError):
            check.validate(self.response, self.request)


if __name__ == '__main__':
    unittest.main()

class FocusedTest(unittest.TestCase):
    def test_policies_and_locations(self):
        old = json.loads((HERE / 'fixtures/case-1.json').read_text())['files']
        items = focused.inputs(old)
        self.assertEqual(len(items), 4)
        self.assertEqual({i['rule'] for i in items}, {'PY_SUPPRESS', 'BOOL_SCOPE'})
        request = focused.build(items)
        self.assertEqual(len(request['questions']), 8)
        self.assertNotIn('original', json.dumps(request))

    def test_final_is_not_type_alias(self):
        items = focused.inputs({'x.py': 'MODE = "strict"\nMode = str\n'}, final_only=True)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['rule'], 'CONST_FINAL')

    def test_focused_error_gate(self):
        items = [{'id': '0', 'rule': 'CONST_FINAL', 'location': 'x.py:1', 'code': 'KEY = "x"'}]
        for p, n in [(0.94, 0), (0.95, 1)]:
            response = {'answers': {'0': {'noul': 0.91}, '0_choice': {'choice': 'correct', 'probabilities': {'correct': p, 'accept': 1-p}}}}
            self.assertEqual(len(focused.result(items, response)), n)
        response['answers']['0']['noul'] = 0.1
        self.assertEqual(focused.result(items, response), [])

map_spec = importlib.util.spec_from_file_location('jev_feature_map', HERE / 'feature_map.py')
feature_map = importlib.util.module_from_spec(map_spec)
with patch.dict(sys.modules, {'main': check, 'focused': focused}):
    map_spec.loader.exec_module(feature_map)


class FeatureMapTest(unittest.TestCase):
    def test_changed_new_lines_not_removed_or_context(self):
        diff = '--- a/map.md\n+++ b/map.md\n@@ -3,2 +3,3 @@\n-old\n+new\n+extra\n context\n'
        self.assertEqual(feature_map.changed_lines(diff), {'map.md': {3, 4}})

    def test_prose_keeps_locations_and_excludes_code_blocks(self):
        source = '# Map\n\n- Current behavior.\n  Continuation.\n- Older checkout history.\n\n```sh\necho old checkout\n```\n'
        paragraphs = list(feature_map.prose(source))
        self.assertIn((3, 4, '- Current behavior.\n  Continuation.'), paragraphs)
        self.assertIn((5, 5, '- Older checkout history.'), paragraphs)
        self.assertNotIn('echo old checkout', str(paragraphs))

    def test_reuses_gate_and_emits_concrete_correction(self):
        items = [{'id': '0', 'rule': feature_map.RULE, 'location': 'map.md:7',
                  'code': 'A checkout without the fix saves null.'}]
        response = {'answers': {'0': {'noul': .99}, '0_choice': {'choice': 'correct', 'probabilities': {'correct': .99, 'accept': .01}}}}
        errors = focused.result(items, response)
        self.assertEqual(errors[0]['code'], 'MAP_CURRENT_BEHAVIOR')
        self.assertIn('current expected behavior', errors[0]['alternative'])
        response['answers']['0_choice']['probabilities']['correct'] = .79
        self.assertEqual(focused.result(items, response), [])

    def test_map_gate_boundary_and_contradiction(self):
        items = [{'id': '0', 'rule': feature_map.RULE, 'location': 'map.md:7', 'code': 'Historical behavior.'}]
        for probability, noul, expected in [(.79, .9, 0), (.80, .9, 1), (.85, .8, 1), (.99, .5, 0)]:
            response = {'answers': {'0': {'noul': noul}, '0_choice': {'choice': 'correct', 'probabilities': {'correct': probability, 'accept': 1-probability}}}}
            self.assertEqual(len(focused.result(items, response)), expected)

    def test_request_has_no_case_labels_or_revision_answer_key(self):
        items = [{'id': '0', 'rule': feature_map.RULE, 'location': 'map.md:7', 'code': '- It saves the shown value.'}]
        request = focused.build(items)
        self.assertEqual(request['state'], {'items': [{'id': '0', 'code': '- It saves the shown value.'}]})

pr_spec = importlib.util.spec_from_file_location('jev_pr_lint', HERE / 'pr_lint.py')
pr_lint = importlib.util.module_from_spec(pr_spec)
with patch.dict(sys.modules, {'main': check, 'focused': focused, 'feature_map': feature_map}):
    pr_spec.loader.exec_module(pr_lint)


class PrAuthorGateTest(unittest.TestCase):
    def test_other_author_skips_before_files_secrets_and_classifier(self):
        from unittest.mock import Mock
        gh = Mock()
        gh.get.return_value = {'user': {'login': 'someone-else'}}
        def forbidden(*args):
            raise AssertionError('Should not classify or get credential')
        result = pr_lint.run(gh, 'OpenHands/OpenHands', 1, forbidden, forbidden)
        self.assertEqual(result['reason'], 'author_not_enyst')
        gh.get.assert_called_once_with('/repos/OpenHands/OpenHands/pulls/1')

    def test_missing_author_and_draft_fail_closed(self):
        from unittest.mock import Mock
        for pr in [{}, {'user': {'login': 'enyst'}, 'state': 'open', 'draft': True}]:
            gh = Mock(); gh.get.return_value = pr
            self.assertEqual(pr_lint.run(gh, 'OpenHands/OpenHands', 1)['status'], 'skipped')
            self.assertEqual(gh.get.call_count, 1)

    def test_event_selects_target_not_author_authorization(self):
        event = {'repository': {'full_name': 'OpenHands/OpenHands'}, 'pull_request': {'number': 7, 'user': {'login': 'enyst'}}}
        self.assertEqual(pr_lint.target(None, None, event), ('OpenHands/OpenHands', 7))
        self.assertIsNone(pr_lint.target(None, None))
        with self.assertRaises(check.CheckError):
            pr_lint.target('https://example.com', 7)

    def test_enyst_without_relevant_changes_needs_no_classifier(self):
        from unittest.mock import Mock
        gh = Mock()
        pr = {'user': {'login': 'enyst'}, 'state': 'open', 'draft': False, 'head': {'sha': 'a'*40}, 'changed_files': 1, 'base': {'repo': {'private': False}}}
        gh.get.side_effect = [pr, [{'filename': 'README.md', 'status': 'modified'}], pr, pr]
        def forbidden(*args):
            raise AssertionError('No paid call needed')
        result = pr_lint.run(gh, 'OpenHands/OpenHands', 1, forbidden, forbidden)
        self.assertEqual(result['candidates'], 0)

    def test_changed_head_aborts_before_classifier(self):
        from unittest.mock import Mock
        gh = Mock()
        pr = {'user': {'login': 'enyst'}, 'state': 'open', 'draft': False, 'head': {'sha': 'a'*40}, 'changed_files': 0, 'base': {'repo': {'private': False}}}
        gh.get.side_effect = [pr, [], dict(pr, head={'sha': 'b'*40})]
        with self.assertRaises(check.CheckError):
            pr_lint.run(gh, 'OpenHands/OpenHands', 1)

publish_spec = importlib.util.spec_from_file_location('jev_publish', HERE / 'publish.py')
publisher = importlib.util.module_from_spec(publish_spec)
with patch.dict(sys.modules, {'main': check, 'feature_map': feature_map}):
    publish_spec.loader.exec_module(publisher)


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.error = {'code': feature_map.RULE, 'location': 'map.md:3', 'source_sha256': 'c'*64,
                      'message': feature_map.POLICY['rule'], 'alternative': feature_map.POLICY['alternative'], 'signal_probability': .85}
        self.report = {'repository': 'OpenHands/OpenHands', 'pr': 7, 'head': 'a'*40, 'errors': [self.error]}
        self.pr = {'user': {'login': 'enyst'}, 'state': 'open', 'draft': False,
                   'head': {'sha': 'a'*40}, 'changed_files': 1, 'base': {'repo': {'private': False}}}

    def client(self, comments=None, line=3):
        from unittest.mock import Mock
        gh = Mock()
        def get(path):
            if path == '/user':
                return {'login': 'smolpaws'}
            if '/files?' in path:
                return [{'filename': 'map.md', 'patch': f'@@ -{line} +{line} @@\n-old\n+new'}]
            if '/comments?' in path:
                return comments or []
            return self.pr
        gh.get.side_effect = get
        gh.post.return_value = {'html_url': 'https://github.com/example/comment'}
        return gh

    def publish(self, gh):
        with patch.dict(sys.modules, {'pr_lint': pr_lint}):
            return publisher.publish(gh, self.report)

    def test_exact_first_line_and_inline_commit(self):
        gh = self.client(); result = self.publish(gh)
        path, body = gh.post.call_args.args
        self.assertEqual(path, '/repos/OpenHands/OpenHands/pulls/7/comments')
        self.assertTrue(body['body'].startswith('Jev-lint warning 🐾\n'))
        self.assertEqual(body['body'].splitlines()[0], 'Jev-lint warning 🐾')
        self.assertEqual((body['commit_id'], body['line'], body['side']), ('a'*40, 3, 'RIGHT'))
        self.assertEqual(result['posted'][0]['kind'], 'inline')

    def test_duplicate_smolpaws_warning_is_skipped(self):
        gh = self.client([{'user': {'login': 'smolpaws'}, 'body': publisher.marker(self.error)}])
        self.assertEqual(self.publish(gh)['duplicate_warnings_skipped'], 1)
        gh.post.assert_not_called()

    def test_other_author_cannot_suppress_warning_by_copying_marker(self):
        gh = self.client([{'user': {'login': 'someone'}, 'body': publisher.marker(self.error)}])
        self.publish(gh); gh.post.assert_called_once()

    def test_non_diff_line_uses_pinned_source_link_not_invalid_inline(self):
        gh = self.client(line=4); result = self.publish(gh)
        path, body = gh.post.call_args.args
        self.assertEqual(path, '/repos/OpenHands/OpenHands/issues/7/comments')
        self.assertIn('/blob/'+'a'*40+'/map.md#L3', body['body'])
        self.assertEqual(result['posted'][0]['kind'], 'source_link_comment')

    def test_wrong_identity_or_changed_target_never_posts(self):
        gh = self.client(); gh.get.side_effect = lambda path: {'login': 'enyst'}
        with self.assertRaises(check.CheckError): self.publish(gh)
        gh.post.assert_not_called()
        gh = self.client(); self.pr['head']['sha'] = 'b'*40
        with self.assertRaises(check.CheckError): self.publish(gh)
        gh.post.assert_not_called()

    def test_no_retry_after_ambiguous_post_failure(self):
        gh = self.client(); gh.post.side_effect = check.CheckError('github_network_failure')
        with self.assertRaises(check.CheckError): self.publish(gh)
        gh.post.assert_called_once()
