"""Description-only evidence collection; no source files, diffs or comments."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
SOURCE=Path(__file__).parents[1]/"sources/notebook-field-notes"
sys.path.insert(0,str(SOURCE))
import descriptions as d
from transport import FieldNotesError
REPO="OpenHands/software-agent-sdk"
SHA="a"*40
DATE="2026-09-19T10:00:00Z"
PR={"number":7,"html_url":f"https://github.com/{REPO}/pull/7","title":"Recover tool observations",
    "body":"## Why\nAgent context loses tool observations.\n## Summary\nRetain observations during compaction.\n## Issue Number\n#9",
    "updated_at":DATE,"head":{"sha":SHA},"base":{"sha":"b"*40},"draft":False,"state":"open"}
ISSUE={"number":9,"html_url":f"https://github.com/{REPO}/issues/9","title":"Lost tool observations",
       "body":"Compaction drops tool observations. The next step cannot use them.","updated_at":DATE}
def gh(*, graph=None, issue=None):
    client=Mock()
    def request(path,**kw):
        if path==f"/repos/{REPO}/pulls/7":return copy.deepcopy(PR)
        if path=="/graphql":return {"data":{"repository":{"pullRequest":{
            "body":PR["body"],"updatedAt":DATE,"headRefOid":SHA,"closingIssuesReferences":
            graph or {"nodes":[],"pageInfo":{"hasNextPage":False},"totalCount":0}}}}}
        if path==f"/repos/{REPO}/issues/9":
            if isinstance(issue,Exception):raise issue
            return copy.deepcopy(issue or ISSUE)
        raise AssertionError(path)
    client.request.side_effect=request
    return client

class DescriptionCollection(unittest.TestCase):
    def test_all_authored_issue_references_are_collected(self):
        refs,complete=d.issue_references("Fixes #9\n## Issue Number\nOpenHands/automation#33\n## Notes\nSee #999\n```\nCloses #555\n```\n<!-- Closes #444 -->",REPO)
        self.assertEqual(refs,{(REPO,9),(REPO,999),("OpenHands/automation",33)})
        self.assertTrue(complete)

    def test_nonclosing_design_references_include_the_issue_description(self):
        descriptions = ["See #9 for the design and motivation.", "Related issue: #9",
                        "Follow-up to #9; the intended behavior is documented there."]
        for body in descriptions:
            with self.subTest(body=body), patch.dict(PR, body=body):
                refs, complete = d.issue_references(body, REPO)
                self.assertEqual(refs, {(REPO, 9)})
                self.assertTrue(complete)
                value = d.gather(gh(), REPO, "pr", 7, [{"repository": REPO, "sha": SHA}])
                self.assertEqual(value["linked_issues"][0]["body"], ISSUE["body"])
                self.assertTrue(value["description_complete"])

    def test_quoted_code_and_comments_are_not_authored_issue_references(self):
        body = ("Read #9 and OpenHands/automation#33.\n"
                "Inline example `#101 OpenHands/automation#102`.\n"
                "```python\nSee #103\n```\n~~~\nRelated issue: #104\n~~~\n"
                "<!-- Related #105 -->\n> Quoted request: #106\n")
        refs, complete = d.issue_references(body, REPO)
        self.assertEqual(refs, {(REPO, 9), ("OpenHands/automation", 33)})
        self.assertTrue(complete)

    def test_refs_outside_public_allowlist_are_never_fetchable(self):
        for body in ["Fixes evil/private#3", "Related issue: evil/private#3",
                     "See https://github.com/evil/private/issues/3 for design."]:
            with self.subTest(body=body):
                refs,complete=d.issue_references(body,REPO)
                self.assertEqual(refs,set())
                self.assertFalse(complete)

    def test_graphql_manual_links_and_description_section_are_combined(self):
        linked={"nodes":[{"number":9,"url":ISSUE["html_url"],"repository":{"nameWithOwner":REPO,"isPrivate":False}}],
                "totalCount":1,"pageInfo":{"hasNextPage":False}}
        client=gh(graph=linked)
        value=d.gather(client,REPO,"pr",7,[{"repository":REPO,"sha":SHA}])
        self.assertEqual([i["url"] for i in value["linked_issues"]],[ISSUE["html_url"]])
        self.assertTrue(value["description_complete"])
        self.assertEqual(value["files"],[])
        self.assertEqual(value["body"],PR["body"])
        paths=[call.args[0] for call in client.request.call_args_list]
        self.assertFalse(any("/files" in p or "/compare/" in p or "/comments" in p for p in paths))

    def test_unavailable_issue_is_incomplete_not_missing_author_information(self):
        value=d.gather(gh(issue=FieldNotesError("http_404",status=404)),REPO,"pr",7,[{"repository":REPO,"sha":SHA}])
        self.assertFalse(value["description_complete"])
        self.assertEqual(value["linked_issues"],[])

    def test_auth_and_rate_limit_failures_are_not_silently_accepted(self):
        for status in (401,403,429):
            with self.subTest(status=status),self.assertRaises(FieldNotesError):
                d.gather(gh(issue=FieldNotesError("http_"+str(status),status=status)),REPO,"pr",7,[{"repository":REPO,"sha":SHA}])

    def test_issue_endpoint_cannot_smuggle_a_pull_request_as_issue(self):
        value=d.gather(gh(issue={**ISSUE,"pull_request":{}}),REPO,"pr",7,[{"repository":REPO,"sha":SHA}])
        self.assertFalse(value["description_complete"])

    def test_linked_issue_change_invalidates_current_check_even_if_pr_unchanged(self):
        client=gh()
        value=d.gather(client,REPO,"pr",7,[{"repository":REPO,"sha":SHA}])
        newer=gh(issue={**ISSUE,"body":ISSUE["body"]+" New design details."})
        self.assertFalse(d.still_current(newer,value))
        self.assertTrue(d.still_current(client,value))

    def test_issue_description_only_for_standalone_issue(self):
        client=Mock()
        client.request.return_value=copy.deepcopy(ISSUE)
        value=d.gather(client,REPO,"issue",9,[{"repository":REPO,"sha":SHA}])
        self.assertEqual(value["linked_issues"],[])
        self.assertTrue(value["description_complete"])
        self.assertEqual(value["body"],ISSUE["body"])
        self.assertTrue(all(c.args[0]==f"/repos/{REPO}/issues/9" for c in client.request.call_args_list))

    def test_private_graphql_link_is_incomplete_without_fetching_it(self):
        graph={"nodes":[{"number":22,"url":"https://github.com/elsewhere/private/issues/22",
                        "repository":{"nameWithOwner":"elsewhere/private","isPrivate":True}}],
               "totalCount":1,"pageInfo":{"hasNextPage":False}}
        client=gh(graph=graph)
        value=d.gather(client,REPO,"pr",7,[{"repository":REPO,"sha":SHA}])
        self.assertFalse(value["description_complete"])
        self.assertFalse(any("elsewhere" in c.args[0] for c in client.request.call_args_list))

    def test_paginated_graphql_links_mark_incomplete(self):
        graph={"nodes":[],"totalCount":21,"pageInfo":{"hasNextPage":True}}
        value=d.gather(gh(graph=graph),REPO,"pr",7,[{"repository":REPO,"sha":SHA}])
        self.assertFalse(value["description_complete"])

    def test_more_than_four_issue_references_are_bounded_and_incomplete(self):
        client=gh()
        real=client.request.side_effect
        def request(path,**kw):
            if "/issues/" in path:
                number=int(path.rsplit("/",1)[1])
                return {**ISSUE,"number":number,"html_url":f"https://github.com/{REPO}/issues/{number}"}
            return real(path,**kw)
        client.request.side_effect=request
        with patch.object(d,"_links",return_value=({(REPO,n) for n in range(1,7)},True)):
            value=d.gather(client,REPO,"pr",7,[{"repository":REPO,"sha":SHA}])
        self.assertEqual(len(value["linked_issues"]),4)
        self.assertFalse(value["description_complete"])

    def test_raw_pr_description_survives_normalization_for_comment_freshness(self):
        with patch.dict(PR,body=PR["body"]+"\n"):
            value=d.gather(gh(),REPO,"pr",7,[{"repository":REPO,"sha":SHA}])
            self.assertEqual(value["pr_description"],PR["body"])
