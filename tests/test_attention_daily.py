import datetime as dt
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).parents[1] / 'cloud-automations/automation-fe2c8185-1b7f-41bf-a687-143e350408b6/tarball'


class DailyTests(unittest.TestCase):
    def load(self, day=9, dry=False):
        weekly = types.SimpleNamespace(make_scorer=Mock(return_value=Mock()),
            GITHUB_TOKEN='synthetic', INGEST_TOKEN='synthetic', DRY_RUN=dry,
            VALIDATE_ONLY=False, gh_get=Mock(side_effect=[{'login':'enyst'}, {'permissions':{'push':True}}]),
            post_notes=Mock(), main=Mock(), close_cloud_workspace=Mock())
        verification = types.SimpleNamespace(BASE='/repos/OpenHands/OpenHands', BOT='all-hands-bot',run=Mock(return_value=[]))
        spec = importlib.util.spec_from_file_location('attention_daily', SOURCE/'daily.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'run':weekly, 'verification':verification}):spec.loader.exec_module(module)
        clock = Mock(wraps=dt)
        clock.datetime.now.return_value=dt.datetime(2026,10,day,7,tzinfo=dt.timezone.utc)
        module.dt=clock
        return module,weekly,verification

    def test_friday_no_llm_completion_or_weekly_scan(self):
        m,w,v=self.load()
        m.entrypoint()
        w.main.assert_not_called()
        w.make_scorer.return_value.assert_not_called()
        w.close_cloud_workspace.assert_called_once_with()
        self.assertFalse(v.run.call_args.args[-1])

    def test_monday_reuses_scorer(self):
        m,w,v=self.load(day=12)
        m.entrypoint()
        w.make_scorer.assert_called_once_with()
        w.main.assert_called_once_with(score_fit=w.make_scorer.return_value)

    def test_dry_run(self):
        m,w,v=self.load(dry=True)
        m.entrypoint()
        self.assertTrue(v.run.call_args.args[-1])
        w.gh_get.assert_not_called()

    def test_wrong_credential_fails_before_writes_and_closes_workspace(self):
        m,w,v=self.load()
        w.gh_get.side_effect=[{'login':'other'}, {'permissions':{'push':True}}]
        with self.assertRaises(RuntimeError):m.entrypoint()
        v.run.assert_not_called()
        self.assertEqual(w.close_cloud_workspace.call_args.args[0],RuntimeError)

    def test_failure_always_closes_workspace(self):
        m,w,v=self.load()
        v.run.side_effect=RuntimeError('test failure')
        with self.assertRaises(RuntimeError):m.entrypoint()
        self.assertEqual(w.close_cloud_workspace.call_args.args[0],RuntimeError)

    def test_missing_kv_refuses(self):
        m,_,_=self.load()
        with patch.dict(m.os.environ,{},clear=True), self.assertRaises(RuntimeError):m.claim('x')

if __name__=='__main__':unittest.main()
