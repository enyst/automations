"""Concurrency and publication boundary tests for Field notes."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
import base64

SOURCE = Path(__file__).resolve().parents[1] / "sources/notebook-field-notes"
sys.path.insert(0, str(SOURCE))
from transport import API, FieldNotesError, GitState, Publisher

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
