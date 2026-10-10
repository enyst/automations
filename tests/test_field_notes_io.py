"""Concurrency and publication boundary tests for Field notes."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
import base64

SOURCE = Path(__file__).resolve().parents[1] / "cloud-automations/automation-623cc664-07c2-425e-bc99-8da3d43c4206/tarball"
sys.path.insert(0, str(SOURCE))
from transport import API, FieldNotesError, GitState, Publisher, json_bytes

def encoded(value, sha="state-sha"):
    return {"sha":sha,"encoding":"base64","content":base64.b64encode(json.dumps(value).encode()).decode()}

class Boundaries(unittest.TestCase):
    def test_refuses_cross_host_and_control_paths(self):
        api=API("https://api.github.com","dummy")
        for path in ["//evil.test/x","/x\nInjected: yes","https://evil.test","/x\\bad"]:
            with self.assertRaises(FieldNotesError): api.request(path)

    def test_private_source_refused(self):
        gh=Mock()
        gh.request.return_value={"full_name":"enyst/enyst.github.io","private":True,"default_branch":"main"}
        with self.assertRaises(FieldNotesError): Publisher(gh).verify()

    def test_state_repo_accepts_public_or_private(self):
        for private in (True,False):
            with self.subTest(private=private):
                gh=Mock()
                gh.request.return_value={"full_name":"enyst/automations","private":private}
                GitState(gh,"run",now=100).verify()
                gh.request.assert_called_once_with("/repos/enyst/automations")

    def test_state_repo_rejects_wrong_identity(self):
        for private in (True,False):
            for name in ("other/automations","enyst/other",None):
                with self.subTest(private=private,name=name):
                    gh=Mock()
                    gh.request.return_value={"full_name":name,"private":private}
                    with self.assertRaisesRegex(FieldNotesError,"^wrong_state_repository$"):
                        GitState(gh,"run",now=100).verify()

    def test_state_repo_rejects_unknown_visibility(self):
        rows=[{"full_name":"enyst/automations"}]
        rows += [{"full_name":"enyst/automations","private":value}
                 for value in (None,0,1,"false","true",[],{})]
        for row in rows:
            with self.subTest(row=row):
                gh=Mock()
                gh.request.return_value=row
                with self.assertRaisesRegex(FieldNotesError,"^unknown_state_repository_visibility$"):
                    GitState(gh,"run",now=100).verify()

    def test_state_repo_rejects_malformed_metadata(self):
        for row in (None,[],"enyst/automations",0):
            with self.subTest(row=row):
                gh=Mock()
                gh.request.return_value=row
                with self.assertRaisesRegex(FieldNotesError,"^wrong_state_repository$"):
                    GitState(gh,"run",now=100).verify()

    def test_active_lease_is_not_stolen(self):
        gh=Mock()
        gh.request.return_value=encoded({"version":1,"lease":{"owner":"other","expires":500},"seen":{},"day":"","attempts":0})
        state=GitState(gh,"run",now=100)
        self.assertFalse(state.claim())
        self.assertTrue(all(c.kwargs.get("method","GET")=="GET" for c in gh.request.call_args_list))

    def test_claim_is_compare_and_swap(self):
        gh=Mock()
        gh.request.side_effect=[encoded({"version":1,"lease":None,"seen":{},"day":"","attempts":0}),
                                {"content":{"sha":"new-sha"}}]
        state=GitState(gh,"run",now=100)
        self.assertTrue(state.claim())
        payload=gh.request.call_args_list[-1].kwargs["data"]
        self.assertEqual(payload["sha"],"state-sha")
        self.assertEqual(payload["branch"],"notebook-field-notes-state")
        self.assertEqual(state.sha,"new-sha")

    def test_note_status_and_comment_opportunity_share_one_atomic_receipt(self):
        gh=Mock()
        gh.request.return_value={"content":{"sha":"next-state"}}
        state=GitState(gh,"run",now=100)
        state.sha="previous-state"
        state.value={"version":1,"lease":{"owner":"run"},"seen":{},"day":"","attempts":0,
                     "info_comments":{"day":"today","attempts":1,"items":{"prior-pr":{"status":"pending"}}}}
        state.remember("subject","f"*64,"published",policy_version=3,needs_info=True)
        gh.request.assert_called_once()
        payload=gh.request.call_args.kwargs["data"]
        self.assertEqual(payload["sha"],"previous-state")
        saved=json.loads(base64.b64decode(payload["content"]))
        self.assertEqual(saved["seen"]["subject"]["status"],"published")
        self.assertIs(saved["seen"]["subject"]["needs_info"],True)
        self.assertEqual(saved["info_comments"],state.value["info_comments"])
        state.remember("subject","f"*64,"published",policy_version=3,needs_info=False)
        saved=json.loads(base64.b64decode(gh.request.call_args.kwargs["data"]["content"]))
        self.assertIs(saved["seen"]["subject"]["needs_info"],False)
        self.assertEqual(saved["info_comments"]["items"]["prior-pr"]["status"],"pending")

    def test_invalid_comment_flag_never_mutates_or_saves_state(self):
        for invalid in [None, 0, 1, "true", [], {}]:
            with self.subTest(value=invalid):
                gh=Mock()
                state=GitState(gh,"run",now=100)
                state.value={"version":1,"lease":{"owner":"run"},"seen":{}}
                with self.assertRaisesRegex(FieldNotesError,"invalid_info_comment_flag"):
                    state.remember("subject","f"*64,"published",needs_info=invalid)
                self.assertFalse(state.value["seen"])
                gh.request.assert_not_called()

    def test_ready_cache_saves_only_validated_scores_and_clears_them_on_attempt(self):
        gh=Mock()
        gh.request.return_value={"content":{"sha":"next-state"}}
        state=GitState(gh,"run",now=100)
        state.sha="previous-state"
        state.value={"version":1,"lease":{"owner":"run"},"seen":{},"day":"","attempts":0}
        classifier={"model":"jev-1.13.0","probabilities":{"design":.8,"agent_behavior":.1,"memory":.1,
                    "substance":.8,"design_context":.1}}
        state.remember("subject","f"*64,"ready",policy_version=3,needs_info=True,classifier=classifier)
        saved=json.loads(base64.b64decode(gh.request.call_args.kwargs["data"]["content"]))
        self.assertEqual(saved["seen"]["subject"]["classifier"],classifier)
        classifier["probabilities"]["design"]=.2
        self.assertEqual(state.value["seen"]["subject"]["classifier"]["probabilities"]["design"],.8)
        state.remember("subject","f"*64,"writing",policy_version=3,needs_info=True)
        saved=json.loads(base64.b64decode(gh.request.call_args.kwargs["data"]["content"]))
        self.assertNotIn("classifier",saved["seen"]["subject"])

    def test_ready_cache_rejects_extra_text_legacy_scores_and_invalid_selection_before_save(self):
        valid={"model":"jev-1.13.0","probabilities":{"design":.8,"agent_behavior":.1,"memory":.1,
               "substance":.8,"design_context":.1}}
        invalid=[None, {**valid,"source_body":"must never enter state"},
                 {**valid,"probabilities":{**valid["probabilities"],"cross_repo":.9}},
                 {**valid,"probabilities":{**valid["probabilities"],"design":True}},
                 {**valid,"probabilities":{**valid["probabilities"],"design":.1}},
                 {**valid,"probabilities":{k:v for k,v in valid["probabilities"].items() if k!="design_context"}}]
        for classifier in invalid:
            with self.subTest(classifier=classifier):
                gh=Mock()
                state=GitState(gh,"run",now=100)
                state.value={"version":1,"lease":{"owner":"run"},"seen":{}}
                with self.assertRaisesRegex(FieldNotesError,"invalid_cached_classifier"):
                    state.remember("subject","f"*64,"ready",classifier=classifier)
                self.assertFalse(state.value["seen"])
                gh.request.assert_not_called()

    def test_score_cache_is_byte_bounded_before_five_hundred_entries(self):
        for direct_save in [False, True]:
            with self.subTest(direct_save=direct_save):
                gh=Mock()
                gh.request.return_value={"content":{"sha":"next-state"}}
                state=GitState(gh,"run",now=1000)
                ledger={"day":"today","attempts":1,"items":{"prior-pr":{"status":"pending","actor_id":1}}}
                state.value={"version":1,"lease":{"owner":"run","expires":9999},"seen":{},
                             "day":"today","attempts":2,"info_comments":ledger}
                classifier={"model":"jev-1.13.0","probabilities":{"design":.8,"agent_behavior":.1,"memory":.1,
                            "substance":.8,"design_context":.1}}
                while len(json_bytes(state.value)) < 230000:
                    n=len(state.value["seen"])
                    state.value["seen"][f"openhands-software-agent-sdk-pr-{n+1}"]={
                        "fingerprint":"f"*64,"listing_fingerprint":"d"*64,"status":"ready","at":n,
                        "source_updated_at":"2026-09-19T12:00:00Z","policy_version":3,
                        "needs_info":True,"classifier":classifier}
                count=len(state.value["seen"])
                self.assertLess(count,500)
                if direct_save:
                    # Comment reconciliation and lease writes also need room.
                    state.save()
                else:
                    state.remember("current","f"*64,"ready",policy_version=3,classifier=classifier)
                    self.assertIn("current",state.value["seen"])
                self.assertLessEqual(len(json_bytes(state.value)),220000)
                self.assertLess(len(state.value["seen"]),count)
                self.assertNotIn("openhands-software-agent-sdk-pr-1",state.value["seen"])
                self.assertEqual(state.value["info_comments"],ledger)
                self.assertEqual(state.value["lease"],{"owner":"run","expires":9999})
                self.assertEqual(state.value["attempts"],2)
                gh.request.assert_called_once()

    def test_non_evictable_state_overflow_still_fails_closed(self):
        gh=Mock()
        state=GitState(gh,"run",now=100)
        ledger={"oversized":"x"*240001}
        state.value={"version":1,"lease":{"owner":"run"},"seen":{},"info_comments":ledger}
        with self.assertRaisesRegex(FieldNotesError,"state_budget_exceeded"):
            state.remember("current","f"*64,"published",needs_info=True)
        self.assertIn("current",state.value["seen"])
        self.assertEqual(state.value["info_comments"],ledger)
        gh.request.assert_not_called()

    def test_conflicting_claim_returns_busy(self):
        gh=Mock()
        gh.request.side_effect=[encoded({"version":1,"lease":None,"seen":{},"day":"","attempts":0}),
                                FieldNotesError("http_409",status=409)]
        self.assertFalse(GitState(gh,"run",now=100).claim())

    def test_existing_note_is_never_replaced(self):
        gh=Mock()
        note={"id":"field-note-openhands-automation-pr-9"}
        manifest={"schema_version":1,"notes":[{"id":note["id"],"path":"field-notes/"+note["id"]+".json","sha256":"a"*64}]}
        gh.request.side_effect=[{"object":{"sha":"b"*40}},encoded(manifest)]
        result=Publisher(gh).publish(note,"<html></html>")
        self.assertEqual(result["status"],"existing")
        self.assertEqual(gh.request.call_count,2)

    def test_new_note_uses_one_atomic_nonforcing_commit(self):
        gh=Mock()
        gh.request.side_effect=[
            {"object":{"sha":"b"*40}}, FieldNotesError("http_404",status=404),
            {"tree":{"sha":"c"*40}}, {"tree":[],"truncated":False}, {"sha":"tree"}, {"sha":"commit"},
            {"object":{"sha":"commit"}}
        ]
        note={"id":"field-note-openhands-automation-pr-9","title":"A design note"}
        result=Publisher(gh).publish(note,"<html></html>")
        self.assertEqual(result["status"],"published")
        tree=gh.request.call_args_list[4].kwargs["data"]["tree"]
        self.assertEqual({x["path"] for x in tree},{
            "field-notes/field-note-openhands-automation-pr-9.json",
            "field-notes/field-note-openhands-automation-pr-9.html",
            "field-notes/manifest.json","field-notes/index.html"})
        patch=gh.request.call_args_list[-1].kwargs["data"]
        self.assertFalse(patch["force"])


    def test_orphaned_note_file_is_reserved_not_overwritten(self):
        gh=Mock()
        note={"id":"field-note-openhands-automation-pr-9","title":"Title"}
        gh.request.side_effect=[
            {"object":{"sha":"b"*40}},FieldNotesError("http_404",status=404),
            {"tree":{"sha":"c"*40}},
            {"tree":[{"path":"field-notes/"+note["id"]+".html"}],"truncated":False}
        ]
        self.assertEqual(Publisher(gh).publish(note,"html")["status"],"reserved")
        self.assertEqual(gh.request.call_count,4)

    def test_lease_loss_prevents_publication(self):
        gh=Mock()
        state=GitState(gh,"run",now=100)
        state.value={"lease":{"owner":"run","expires":9999999999}}
        state.sha="old"
        gh.request.return_value=encoded({"lease":{"owner":"other","expires":9999999999}},"changed")
        with self.assertRaises(FieldNotesError):state.verify_lease()

if __name__=="__main__": unittest.main()
