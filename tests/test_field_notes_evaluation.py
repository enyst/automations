"""Offline regression checks for the separate evaluation harness."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from io import BytesIO

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location("field_notes_evaluation",ROOT/"scripts/evaluate_field_notes.py")
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
policy_spec=importlib.util.spec_from_file_location("evaluation_policy",ROOT/"sources/notebook-field-notes/core.py")
core=importlib.util.module_from_spec(policy_spec);policy_spec.loader.exec_module(core)

class Response:
    status=200
    def __init__(self,value):self.raw=json.dumps(value).encode()
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,limit):return self.raw

class Evaluation(unittest.TestCase):
    def fixture(self,root):
        (root/"requests").mkdir();(root/"responses").mkdir()
        source=(ROOT/"sources/notebook-field-notes/core.py").read_bytes()
        (root/"core.snapshot.py").write_bytes(source)
        issue={"number":42,"html_url":"https://github.com/OpenHands/software-agent-sdk/issues/42",
               "title":"Preserve context on resume","body":"Resuming loses conversation context. Preserve it.",
               "updated_at":"2026-09-19T10:00:00Z"}
        snapshot={"repository":"OpenHands/software-agent-sdk","issues":[issue]}
        e.save(root/"snapshot.json",snapshot)
        e.save(root/"rubric.json",{"core_sha256":hashlib.sha256(source).hexdigest(),
                                  "snapshot_sha256":hashlib.sha256((root/"snapshot.json").read_bytes()).hexdigest()})
        candidate={"repository":snapshot["repository"],"kind":"issue","number":42,"url":issue["html_url"],
                   "title":issue["title"],"body":issue["body"],"updated_at":issue["updated_at"],
                   "head_sha":"a"*40,"description_complete":True}
        e.save(root/"requests/42.json",core.classification_request(candidate))
        return {"model":core.MODEL,"answers":{key:{"type":"noul","noul":.9} for key in core.QUESTIONS},
                "usage":{"input_tokens":100,"output_tokens":104}}

    def test_response_and_request_retained_and_successful_resume_never_calls_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);reply=self.fixture(root)
            request_before=(root/"requests/42.json").read_bytes()
            with patch.object(e.subprocess,"run",return_value=subprocess.CompletedProcess([],0,"dummy-eval-secret\n","")),\
                 patch.object(e.urllib.request.OpenerDirector,"open",return_value=Response(reply)) as send:
                e.evaluate(root,1)
                self.assertEqual(send.call_count,1)
                self.assertEqual(send.call_args.args[0].full_url,"https://api.typesafe.ai/v1/systemone")
                e.evaluate(root,1)
                self.assertEqual(send.call_count,1)
            self.assertEqual((root/"requests/42.json").read_bytes(),request_before)
            self.assertEqual(json.loads((root/"responses/42/attempt-01.json").read_text()),reply)
            result=json.loads((root/"results/42.json").read_text())
            self.assertEqual(result["decision"]["decision"],"write")

    def test_http_error_response_is_saved_before_retry_and_attempt_numbers_survive_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);reply=self.fixture(root)
            failure=urllib.error.HTTPError("https://api.typesafe.ai/v1/systemone",503,"temporary",{},BytesIO(b'{"error":"temporary"}'))
            with patch.object(e.subprocess,"run",return_value=subprocess.CompletedProcess([],0,"dummy-eval-secret\n","")),\
                 patch.object(e.time,"sleep"),\
                 patch.object(e.urllib.request.OpenerDirector,"open",side_effect=[failure,Response(reply)]):
                e.evaluate(root,1)
            self.assertEqual(json.loads((root/"responses/42/attempt-01.json").read_text()),{"error":"temporary"})
            self.assertEqual(json.loads((root/"responses/42/attempt-02.meta.json").read_text())["http_status"],200)
            # Resume an error result without overwriting any earlier response.
            result=json.loads((root/"results/42.json").read_text());result["status"]="error";e.save(root/"results/42.json",result)
            with patch.object(e.subprocess,"run",return_value=subprocess.CompletedProcess([],0,"dummy-eval-secret\n","")),\
                 patch.object(e.urllib.request.OpenerDirector,"open",return_value=Response(reply)):
                e.evaluate(root,1)
            self.assertTrue((root/"responses/42/attempt-03.json").exists())

    def test_provider_cannot_echo_credential_into_saved_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);reply=self.fixture(root);reply["diagnostic"]="dummy-eval-secret"
            with patch.object(e.subprocess,"run",return_value=subprocess.CompletedProcess([],0,"dummy-eval-secret\n","")),\
                 patch.object(e.urllib.request.OpenerDirector,"open",return_value=Response(reply)):
                e.evaluate(root,1)
            for path in root.rglob("*"):
                if path.is_file():self.assertNotIn(b"dummy-eval-secret",path.read_bytes())
            self.assertTrue(json.loads((root/"responses/42/attempt-01.meta.json").read_text())["credential_redacted"])
