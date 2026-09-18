"""Read-only GitHub selection and complete scoring context.

REST/GraphQL callables are injected so collection can be tested without GitHub,
credentials, an SDK, or an LLM. GitHub text remains data throughout this module.
"""

import datetime as dt
from html.parser import HTMLParser
import re
import urllib.parse


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def mentions(text, login):
    # Match a login, not an email address or @enyst-other/@enyst/example.
    return bool(re.search(r"(?<![\w@])@" + re.escape(login) + r"(?![\w/-])", text or "", re.I))


def rendered_mention(body_html, login):
    class Mentions(HTMLParser):
        found = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "a" and "user-mention" in (attrs.get("class") or "").split():
                url = urllib.parse.urlsplit(attrs.get("href") or "")
                if (url.netloc in ("", "github.com")
                        and url.path.lower().rstrip("/") == f"/{login.lower()}"):
                    self.found = True

    parser = Mentions()
    parser.feed(body_html)
    return parser.found


class GitHubContext:
    def __init__(self, rest, graphql, progress=None):
        self._rest = rest
        self._graphql = graphql
        self.api_calls = {"rest": 0, "graphql": 0}
        self.content_cache = {}
        self.progress = progress

    def count(self, kind):
        self.api_calls[kind] += 1
        if self.progress and sum(self.api_calls.values()) % 100 == 0:
            self.progress(dict(self.api_calls))

    def rest(self, path):
        self.count("rest")
        return self._rest(path)

    def graphql(self, query, variables):
        self.count("graphql")
        return self._graphql(query, variables)

    def pages(self, path, **params):
        page = 1
        per_page = params.pop("per_page", 100)
        while True:
            query = urllib.parse.urlencode({**params, "per_page": per_page, "page": page})
            batch = self.rest(f"{path}?{query}")
            if not isinstance(batch, list):
                raise RuntimeError(f"GitHub did not return a collection for {path}")
            yield from batch
            if len(batch) < per_page:
                return
            page += 1

    def connection(self, query, variables, field):
        cursor = None
        while True:
            data = self.graphql(query, {**variables, "cursor": cursor})
            pr = (data.get("repository") or {}).get("pullRequest")
            if not pr or not isinstance(pr.get(field), dict):
                raise RuntimeError(f"GitHub omitted pull request {field}")
            connection = pr[field]
            yield from (connection.get("nodes") or [])
            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage"):
                return
            next_cursor = page.get("endCursor")
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError(f"GitHub returned an invalid {field} pagination cursor")
            cursor = next_cursor

    @staticmethod
    def variables(repo, number):
        owner, name = repo.split("/", 1)
        return {"owner": owner, "name": name, "number": int(number)}

    def content_node(self, content):
        node_id = content.get("node_id")
        if not node_id:
            raise RuntimeError("GitHub omitted the content node ID")
        if node_id not in self.content_cache:
            data = self.graphql("""query($id:ID!){node(id:$id){
              ... on PullRequest{bodyHTML}
              ... on IssueComment{bodyHTML}
              ... on PullRequestReview{bodyHTML}
              ... on PullRequestReviewComment{bodyHTML publishedAt}
            }}""", {"id": node_id})
            node = data.get("node")
            if not isinstance(node, dict) or "bodyHTML" not in node:
                raise RuntimeError("GitHub omitted rendered mention content")
            self.content_cache[node_id] = node
        return self.content_cache[node_id]

    def content_mentions(self, content, login):
        if not mentions(content.get("body"), login):
            return False
        body_html = content.get("body_html")
        if not isinstance(body_html, str):
            body_html = self.content_node(content)["bodyHTML"]
        # GitHub's rendered user-mention anchors exclude code, escaped text,
        # email addresses, ordinary profile links, and similarly named logins.
        return rendered_mention(body_html, login)

    def closing_issues(self, repo, number):
        # No text-link heuristics: GitHub owns this relationship, including
        # manually linked issues. Do not excludeUserLinked or userLinkedOnly.
        query = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
          repository(owner:$owner,name:$name){pullRequest(number:$number){
            closingIssuesReferences(first:100,after:$cursor){
              nodes{id number title body url repository{nameWithOwner}}
              pageInfo{hasNextPage endCursor}
            }
          }}
        }"""
        unique = {}
        for issue in self.connection(query, self.variables(repo, number), "closingIssuesReferences"):
            if not issue or not issue.get("url"):
                raise RuntimeError("GitHub omitted a closing-linked issue")
            unique[issue["url"]] = {
                "url": issue["url"], "title": issue.get("title") or "",
                "body": issue.get("body") or "",
            }
        return sorted(unique.values(), key=lambda issue: issue["url"])

    def recent_mention_event(self, repo, pr, login, cutoff, now):
        # GitHub records the mentioned recipient as actor. A live SDK PR4700
        # canary confirms these events include discussion-comment mentions.
        # They also avoid dating an old @mention by unrelated updated_at.
        created = timestamp(pr.get("created_at"))
        if created and cutoff <= created <= now and self.content_mentions(pr, login):
            return True
        query = """query($owner:String!,$name:String!,$number:Int!,$cursor:String,$since:DateTime!){
          repository(owner:$owner,name:$name){pullRequest(number:$number){
            timelineItems(first:100,after:$cursor,since:$since,itemTypes:[MENTIONED_EVENT]){
              nodes{... on MentionedEvent{createdAt actor{login}}}
              pageInfo{hasNextPage endCursor}
            }
          }}
        }"""
        variables = {**self.variables(repo, pr["number"]), "since": cutoff.isoformat()}
        for event in self.connection(query, variables, "timelineItems"):
            occurred = timestamp(event.get("createdAt"))
            if ((event.get("actor") or {}).get("login", "").lower() == login.lower()
                    and occurred and cutoff <= occurred <= now):
                return True
        return False

    def inline_publication(self, comment):
        # created_at can be the time a draft was written, rather than published.
        node = self.content_node(comment)
        if "publishedAt" not in node:
            raise RuntimeError("GitHub omitted review-comment publication data")
        return timestamp(node["publishedAt"])

    def candidates(self, repo, login, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        cutoff = now - dt.timedelta(days=7)
        base = f"/repos/{repo}"
        chosen = {}
        # Exactly the newest 50 OPEN PRs by creation, not by activity.
        for pr in self.pages(f"{base}/pulls", state="open", sort="created", direction="desc", per_page=50):
            chosen[pr["number"]] = pr
            if len(chosen) == 50:
                break

        recent = {}
        # Candidate discovery only: updated_at is never evidence of a mention.
        for pr in self.pages(f"{base}/pulls", state="all", sort="updated", direction="desc"):
            if timestamp(pr.get("updated_at")) < cutoff:
                break
            recent[pr["number"]] = pr

        ordinary_issues = set()

        def remember(number):
            if number in chosen:
                return chosen[number]
            if number not in recent and number not in ordinary_issues:
                pr = self.rest(f"{base}/pulls/{number}")
                # Repository issue comments also include ordinary issues.
                if pr is None:
                    ordinary_issues.add(number)
                else:
                    recent[number] = pr
            return recent.get(number)

        def add(number):
            pr = remember(number)
            if pr is not None:
                chosen[number] = pr

        # Both repository-wide endpoints paginate without a result cap. Their
        # `since` filter is on updated time; inspect publication time separately.
        for endpoint, ref_key in [("issues/comments", "issue_url"), ("pulls/comments", "pull_request_url")]:
            for comment in self.pages(f"{base}/{endpoint}", since=cutoff.isoformat()):
                number = int(comment[ref_key].rstrip("/").rsplit("/", 1)[1])
                # Old comments edited recently still need timeline checks, even
                # if PR.updated_at did not change or the mention was removed.
                # This discovers candidates; it never establishes mention age.
                if remember(number) is None:
                    continue
                if not self.content_mentions(comment, login):
                    continue
                published = (self.inline_publication(comment) if endpoint == "pulls/comments"
                             else timestamp(comment.get("created_at")))
                if published and cutoff <= published <= now:
                    add(number)

        for number, pr in recent.items():
            if number in chosen:
                continue
            found = self.recent_mention_event(repo, pr, login, cutoff, now)
            if not found:
                for review in self.pages(f"{base}/pulls/{number}/reviews"):
                    submitted = timestamp(review.get("submitted_at"))
                    if submitted and cutoff <= submitted <= now:
                        found = self.content_mentions(review, login)
                        if not found:
                            # Includes inline comments drafted earlier but only
                            # published with this review during the window.
                            found = any(self.content_mentions(comment, login) for comment in self.pages(
                                f"{base}/pulls/{number}/reviews/{review['id']}/comments"))
                        if found:
                            break
            if found:
                add(number)
        return list(chosen.values())
