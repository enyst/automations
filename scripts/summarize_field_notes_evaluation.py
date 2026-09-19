#!/usr/bin/env python3
"""Validate the frozen Field notes evaluation and summarize agent-review agreement.

Offline only: no credentials, provider calls, production modules or input edits.
Use --check to validate and compute metrics without writing reports.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import re
import sys
import tempfile

KEYS = ("design", "agent_behavior", "memory", "cross_repo", "substance", "design_context")
THRESHOLDS = {key: (0.70 if key in {"substance", "design_context"} else 0.65) for key in KEYS}
ACTION_ROUTE = {"investigate": "write", "skip": "skip", "uncertain": "defer",
                "insufficient_description": "needs_info"}
ROUTES = ("write", "skip", "defer", "needs_info", "error")
PRIMARY_FILES = {"group-1.json": (1, 89), "group-2.json": (2, 77),
                 "group-2-tail.json": (2, 20), "group-3.json": (3, 84)}
EXPECTED_COUNT = 270
ROOT_SAMPLE_SHA256 = "223189c87bb4269f36fda7fb098fd74653e2846cfa3f2d4c4567349ba1530184"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def keyed(rows, label):
    require(isinstance(rows, list), f"{label}: expected records list")
    result = {}
    for row in rows:
        number = row.get("issue_number")
        require(type(number) is int and number > 0 and number not in result,
                f"{label}: invalid or duplicate issue number")
        result[number] = row
    return result


def fraction(numerator, denominator):
    return numerator / denominator if denominator else None


def agreement(pairs, choices):
    matrix = {left: {right: 0 for right in choices} for left in choices}
    for left, right in pairs:
        require(left in choices and right in choices, "Unexpected comparison label")
        matrix[left][right] += 1
    equal = sum(left == right for left, right in pairs)
    return {"compared": len(pairs), "agree": equal, "agreement": fraction(equal, len(pairs)),
            "confusion": matrix}


def route(result):
    return result["decision"]["decision"] if result["status"] == "ok" else "error"


def binary(result, key):
    return "yes" if result["classification"]["probabilities"][key] >= THRESHOLDS[key] else "no"


def category_agreement(reviews, results):
    summary = {}
    for key in KEYS:
        pairs, unclear, unavailable = [], 0, 0
        for number, review in reviews.items():
            label = review["labels"][key]
            if label == "unclear":
                unclear += 1
            elif results[number]["status"] != "ok":
                unavailable += 1
            else:
                pairs.append((label, binary(results[number], key)))
        summary[key] = {**agreement(pairs, ("yes", "no")), "threshold": THRESHOLDS[key],
                        "reviewer_labels": dict(Counter(r["labels"][key] for r in reviews.values())),
                        "model_labels": dict(Counter(binary(results[n], key) if results[n]["status"] == "ok"
                                                     else "unavailable" for n in reviews)),
                        "reviewer_unclear_excluded": unclear, "model_unavailable_excluded": unavailable}
    return summary


class Evaluation:
    def __init__(self, directory):
        self.root = directory.resolve()
        self.raw_usage = {}
        self.attempts = []
        self.requests = {}
        self.inputs = {}
        self.input_files = {}
        self.review_files = {}

    def load(self, relative):
        path = self.root / relative
        require(path.resolve().is_relative_to(self.root), "Artifact path escapes evaluation directory")
        return read_json(path)

    def usage(self, relative, raw):
        require(isinstance(raw, dict), f"{relative}: response must be an object")
        usage = raw.get("usage")
        if usage is None:
            return
        require(isinstance(usage, dict), f"{relative}: invalid usage")
        for key in ("input_tokens", "output_tokens"):
            require(type(usage.get(key)) is int and usage[key] >= 0, f"{relative}: invalid {key}")
        self.raw_usage[relative] = {key: usage[key] for key in ("input_tokens", "output_tokens")}

    def validated_model_response(self, relative):
        raw = self.load(relative)
        classification = self.core.validate_classification(raw)
        require(raw.get("usage") is not None, f"{relative}: successful response usage missing")
        self.usage(relative, raw)
        return classification

    def sources(self):
        self.rubric = self.load("rubric.json")
        for filename, field in (("snapshot.json", "snapshot_sha256"), ("core.snapshot.py", "core_sha256")):
            actual = hashlib.sha256((self.root / filename).read_bytes()).hexdigest()
            require(actual == self.rubric[field], f"{filename}: frozen SHA mismatch")
        # Execute only the hash-checked, pure policy snapshot, never the live runner.
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("frozen_field_notes_policy", self.root / "core.snapshot.py")
        self.core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.core)
        require(self.rubric["questions"] == self.core.QUESTIONS and
                self.rubric["policy_version"] == self.core.POLICY_VERSION and
                set(self.rubric["questions"]) == set(KEYS), "Rubric/policy mismatch")
        snapshot = self.load("snapshot.json")
        self.snapshot = snapshot
        require(snapshot["repository"] == "OpenHands/software-agent-sdk", "Unexpected source repository")
        require(len(snapshot["issues"]) == EXPECTED_COUNT, "Snapshot is not the complete 270-issue evaluation")
        numbers = set()
        for issue in snapshot["issues"]:
            number = issue["number"]
            require(type(number) is int and number > 0 and number not in numbers, "Duplicate snapshot issue")
            numbers.add(number)
            require(issue["state"] == "open" and "pull_request" not in issue, "Snapshot contains non-open issue")
            expected = self.core.classification_request({
                "repository": snapshot["repository"], "kind": "issue", "number": number,
                "url": issue["html_url"], "title": issue["title"], "body": issue.get("body") or "",
                "updated_at": issue["updated_at"], "head_sha": snapshot["source_head"],
                "description_complete": True, "description_truncated": False,
            })
            request = self.load(f"requests/{number}.json")
            require(request == expected, f"#{number}: request differs from frozen source/policy")
            self.requests[number] = request
        expected_names = {f"{number}.json" for number in numbers}
        require({path.name for path in (self.root / "requests").glob("*.json")} == expected_names,
                "Request file coverage mismatch")
        require({path.name for path in (self.root / "results").glob("*.json")} == expected_names,
                "Primary result file coverage mismatch")
        require({path.name for path in (self.root / "responses").iterdir() if path.is_dir()} ==
                {str(number) for number in numbers}, "Primary response directory coverage mismatch")

        groups = self.rubric["review_groups"]
        require({group["group"] for group in groups} == {1, 2, 3}, "Unexpected input review groups")
        for group in groups:
            relative = f"inputs/group-{group['group']}.json"
            entries = keyed(self.load(relative), relative)
            require(len(entries) == group["count"], f"{relative}: count mismatch")
            for number, entry in entries.items():
                require(number not in self.inputs, "Duplicate primary input assignment")
                self.validate_input(entry)
                self.inputs[number] = entry
                self.input_files[number] = relative
        require(set(self.inputs) == numbers and len({r["input_sha256"] for r in self.inputs.values()}) == EXPECTED_COUNT,
                "Primary input/hash coverage mismatch")

    def validate_input(self, entry):
        number = entry["issue_number"]
        require(number in self.requests, "Unknown input issue")
        request = self.requests[number]
        state = request["state"]
        require(entry["input_sha256"] == digest(request), f"#{number}: input SHA mismatch")
        require({key: entry[key] for key in ("url", "title", "body")} == state["subject"] and
                entry["coverage"] == state["coverage"], f"#{number}: review/model input mismatch")
        require(set(entry["coverage"]) == {"retrieval_complete", "truncated"} and
                all(type(value) is bool for value in entry["coverage"].values()), f"#{number}: invalid coverage")

    def primary_results(self):
        aggregate = self.load("results.json")
        require(aggregate["model"] == self.core.MODEL and aggregate["policy_version"] == self.core.POLICY_VERSION and
                aggregate["count"] == EXPECTED_COUNT, "Aggregate primary metadata mismatch")
        self.results = keyed(aggregate["results"], "results.json")
        require(set(self.results) == set(self.requests), "Aggregate primary result coverage mismatch")
        for number, result in self.results.items():
            require(result == self.load(f"results/{number}.json"), f"#{number}: aggregate/per-issue results differ")
            require(result["input_sha256"] == digest(self.requests[number]) and
                    result["url"] == self.inputs[number]["url"], f"#{number}: result identity mismatch")
            require(result["status"] in {"ok", "error"}, f"#{number}: invalid result status")
            metadata = sorted((self.root / "responses" / str(number)).glob("attempt-*.meta.json"))
            require(metadata, f"#{number}: no primary response metadata")
            for path in metadata:
                relative = str(path.relative_to(self.root))
                meta = self.load(relative)
                require(meta["issue_number"] == number and meta["input_sha256"] == result["input_sha256"],
                        f"#{number}: attempt identity mismatch")
                duration = meta["duration_seconds"]
                require(type(duration) in {int, float} and math.isfinite(duration) and duration >= 0,
                        f"#{number}: invalid attempt duration")
                require(meta["http_status"] is None or type(meta["http_status"]) is int,
                        f"#{number}: invalid HTTP status")
                self.attempts.append(meta)
                response_path = relative.removesuffix(".meta.json") + ".json"
                if (self.root / response_path).exists():
                    self.usage(response_path, self.load(response_path))
            response_file = result.get("response_file")
            if response_file is not None:
                require(isinstance(response_file, str) and
                        re.fullmatch(rf"responses/{number}/attempt-[0-9]+\.(json|txt)", response_file),
                        f"#{number}: invalid response path")
                meta_file = response_file.rsplit(".", 1)[0] + ".meta.json"
                require(self.load(meta_file)["http_status"] == result["http_status"], f"#{number}: HTTP status mismatch")
            if result["status"] == "ok":
                require(result["http_status"] == 200 and response_file is not None and response_file.endswith(".json"),
                        f"#{number}: successful result without successful JSON response")
                classified = self.validated_model_response(response_file)
                require(classified == result["classification"], f"#{number}: raw/normalized probabilities differ")
                coverage = self.requests[number]["state"]["coverage"]
                require(result["coverage"] == coverage, f"#{number}: result coverage mismatch")
                complete = coverage["retrieval_complete"] and not coverage["truncated"]
                require(result["decision"] == self.core.classify_decision(classified, context_complete=complete),
                        f"#{number}: saved route differs from frozen policy")
            else:
                require(isinstance(result.get("error"), str), f"#{number}: failed result without diagnostic")

    def reviews(self, relative):
        document = self.load(relative)
        require(document["protocol_version"] == 1 and isinstance(document["reviewer"], str) and document["reviewer"],
                f"{relative}: invalid review metadata")
        records = keyed(document["records"], relative)
        for number, record in records.items():
            require(number in self.inputs and record["input_sha256"] == self.inputs[number]["input_sha256"],
                    f"{relative}: review input identity mismatch")
            labels = record["labels"]
            require(isinstance(labels, dict) and set(labels) == set(KEYS) and
                    all(label in {"yes", "no", "unclear"} for label in labels.values()), f"#{number}: invalid labels")
            require(record["suggested_action"] in ACTION_ROUTE, f"#{number}: invalid action")
            require(isinstance(record["reason"], str) and 0 < len(record["reason"]) <= 500, f"#{number}: invalid reason")
            require(isinstance(record["flags"], list) and all(isinstance(flag, str) for flag in record["flags"]),
                    f"#{number}: invalid flags")
            require(isinstance(record["evidence"], list) and record["evidence"], f"#{number}: evidence missing")
            source = self.inputs[number]
            for evidence in record["evidence"]:
                quote = evidence["text"]
                require(isinstance(quote, str) and 0 < len(quote) <= 160 and
                        (quote in source["title"] or quote in source["body"]), f"#{number}: non-exact or unbounded quote")
                require(isinstance(evidence["supports"], list) and evidence["supports"] and
                        set(evidence["supports"]) <= set(KEYS), f"#{number}: invalid evidence labels")
        return document["reviewer"], records

    def reviewers(self):
        self.primary = {}
        self.reviewers_by_issue = {}
        counts = {}
        for filename, (group, expected) in PRIMARY_FILES.items():
            relative = "reviews/" + filename
            reviewer, records = self.reviews(relative)
            require(len(records) == expected, f"{relative}: incomplete review, expected {expected}")
            counts[relative] = {"reviewer": reviewer, "count": len(records)}
            for number, record in records.items():
                require(number not in self.primary and self.input_files[number] == f"inputs/group-{group}.json",
                        f"{relative}: duplicate or out-of-group issue")
                self.primary[number] = record
                self.reviewers_by_issue[number] = reviewer
                self.review_files[number] = relative
        require(set(self.primary) == set(self.requests), "Primary reviews do not cover all 270 issues")
        self.review_counts = counts
        require(hashlib.sha256((self.root / "inputs/root-blind-sample.json").read_bytes()).hexdigest() ==
                ROOT_SAMPLE_SHA256, "Frozen root double-review sample SHA mismatch")
        root_inputs = keyed(self.load("inputs/root-blind-sample.json"), "root sample inputs")
        require(len(root_inputs) == 18, "Root double-review sample must contain 18 issues")
        for entry in root_inputs.values():
            self.validate_input(entry)
        self.root_reviewer, self.root_reviews = self.reviews("reviews/root-blind-sample.json")
        require(set(root_inputs) == set(self.root_reviews), "Root review/input sample coverage mismatch")

    def stability(self):
        plan = self.load("stability/plan.json")
        numbers = plan["issue_numbers"]
        require(len(numbers) == len(set(numbers)) == 10 and plan["repeats_per_case"] == 2,
                "Unexpected stability plan")
        records = keyed(self.load("stability/results.json"), "stability results")
        require(set(records) == set(numbers), "Incomplete stability results")
        rows = []
        for number in numbers:
            require(number in self.results and self.results[number]["status"] == "ok", "Invalid stability subject")
            samples = records[number]["samples"]
            require(len(samples) == 3 and samples[0]["primary"] is True and
                    all(sample["primary"] is False for sample in samples[1:]), "Invalid stability sample ordering")
            for index, sample in enumerate(samples):
                expected_file = (self.results[number]["response_file"] if index == 0
                                 else f"stability/{number}-repeat-{index}.json")
                require(sample["response_file"] == expected_file, "Stability response path mismatch")
                classified = self.validated_model_response(expected_file)
                require(sample["probabilities"] == classified["probabilities"], "Stability raw/normalized mismatch")
                coverage = self.inputs[number]["coverage"]
                actual = self.core.classify_decision(classified,
                    context_complete=coverage["retrieval_complete"] and not coverage["truncated"])
                require(sample["decision"] == actual["decision"], "Stability route mismatch")
            require(samples[0]["probabilities"] == self.results[number]["classification"]["probabilities"] and
                    samples[0]["decision"] == route(self.results[number]), "Stability primary mismatch")
            baseline = samples[0]
            decisions = [sample["decision"] for sample in samples]
            rows.append({
                "issue_number": number, "input_sha256": self.inputs[number]["input_sha256"],
                "request_file": f"requests/{number}.json", "response_files": [sample["response_file"] for sample in samples],
                "routes": decisions,
                "repeat_route_flips_from_primary": sum(value != decisions[0] for value in decisions[1:]),
                "adjacent_route_transitions": sum(left != right for left, right in zip(decisions, decisions[1:])),
                "category_repeat_flips_from_primary": {
                    key: sum((sample["probabilities"][key] >= THRESHOLDS[key]) !=
                             (baseline["probabilities"][key] >= THRESHOLDS[key]) for sample in samples[1:])
                    for key in KEYS},
                "probability_ranges": {key: [min(s["probabilities"][key] for s in samples),
                                             max(s["probabilities"][key] for s in samples)] for key in KEYS},
            })
        self.stability_summary = {
            "selection_method": plan["method"], "plan_file": "stability/plan.json",
            "results_file": "stability/results.json", "cases": len(rows), "repeats": 2 * len(rows),
            "cases_with_route_flip": sum(row["repeat_route_flips_from_primary"] > 0 for row in rows),
            "repeat_route_flips_from_primary": sum(row["repeat_route_flips_from_primary"] for row in rows),
            "adjacent_route_transitions": sum(row["adjacent_route_transitions"] for row in rows),
            "category_repeat_flips_from_primary": {
                key: sum(row["category_repeat_flips_from_primary"][key] for row in rows) for key in KEYS},
            "case_results": rows,
        }

    def summarize(self):
        action_pairs = [(ACTION_ROUTE[r["suggested_action"]], route(self.results[n])) for n, r in self.primary.items()]
        complete = {n: r for n, r in self.primary.items()
                    if self.inputs[n]["coverage"]["retrieval_complete"] and not self.inputs[n]["coverage"]["truncated"]}
        root_assigned = {}
        for key in KEYS:
            pairs = [(r["labels"][key], self.primary[n]["labels"][key]) for n, r in self.root_reviews.items()]
            determinate = [(left, right) for left, right in pairs if "unclear" not in (left, right)]
            root_assigned[key] = {**agreement(determinate, ("yes", "no")),
                                  "root_labels": dict(Counter(left for left, right in pairs)),
                                  "assigned_labels": dict(Counter(right for left, right in pairs)),
                                  "either_unclear_excluded": len(pairs) - len(determinate)}
        root_pairs = [(ACTION_ROUTE[r["suggested_action"]], ACTION_ROUTE[self.primary[n]["suggested_action"]])
                      for n, r in self.root_reviews.items()]
        root_jev = [(ACTION_ROUTE[r["suggested_action"]], route(self.results[n])) for n, r in self.root_reviews.items()]
        primary_usage = {path: usage for path, usage in self.raw_usage.items() if path.startswith("responses/")}
        repeat_usage = {path: usage for path, usage in self.raw_usage.items() if path.startswith("stability/")}
        sum_usage = lambda rows: {key: sum(value[key] for value in rows.values()) for key in ("input_tokens", "output_tokens")}
        counts = lambda values: dict(sorted(Counter(values).items()))
        return {
            "protocol_version": 1, "model": self.core.MODEL, "policy_version": self.core.POLICY_VERSION,
            "interpretation": [
                "Reviewers are independent agent judgments, not ground truth; agreement is not accuracy.",
                "Primary reviewer actions map to model routes; uncertainty maps to defer.",
                "The policy forces defer for incomplete inputs independently of author description sufficiency.",
                "Unclear category judgments are excluded from binary agreement and counted explicitly.",
                "Stability cases were selected near the context threshold, not randomly; do not generalize their flip rate.",
                "Thresholded judgments do not establish probability calibration.",
            ],
            "artifacts": {"snapshot": "snapshot.json", "rubric": "rubric.json", "core": "core.snapshot.py",
                          "primary_results": "results.json", "comparison": "comparison.csv",
                          "root_overlap": "root-overlap.csv", "review_files": self.review_counts,
                          "root_review": "reviews/root-blind-sample.json"},
            "source": {"repository": self.snapshot["repository"], "head": self.snapshot["source_head"],
                       "scope": self.snapshot["scope"], "started_at": self.snapshot["started_at"],
                       "completed_at": self.snapshot["completed_at"], "snapshot_sha256": self.rubric["snapshot_sha256"],
                       "core_sha256": self.rubric["core_sha256"], "root_sample_sha256": ROOT_SAMPLE_SHA256},
            "counts": {"issues": len(self.requests), "primary_reviews": len(self.primary),
                       "root_double_reviews": len(self.root_reviews), "primary_attempts": len(self.attempts),
                       "primary_status": counts(r["status"] for r in self.results.values()),
                       "primary_http_status": counts(str(a["http_status"]) for a in self.attempts),
                       "primary_routes": counts(route(r) for r in self.results.values()),
                       "primary_route_reasons": counts(r.get("decision", {}).get("reason", "error") for r in self.results.values()),
                       "reviewer_actions": counts(r["suggested_action"] for r in self.primary.values())},
            "coverage": {"complete": len(complete),
                         "truncated_issues": sorted(n for n, x in self.inputs.items() if x["coverage"]["truncated"]),
                         "retrieval_incomplete_issues": sorted(n for n, x in self.inputs.items() if not x["coverage"]["retrieval_complete"])},
            "usage": {"primary_attempts": sum_usage(primary_usage), "primary_usage_records": len(primary_usage),
                      "repeat_responses": sum_usage(repeat_usage), "repeat_usage_records": len(repeat_usage),
                      "total": sum_usage(self.raw_usage),
                      "primary_duration_seconds": round(sum(a["duration_seconds"] for a in self.attempts), 3)},
            "agreement": {"action_to_route": ACTION_ROUTE, "confusion_orientation": "rows=reviewer, columns=Jev",
                          "all_input_routes": agreement(action_pairs, ROUTES),
                          "complete_input_routes": agreement(
                              [(ACTION_ROUTE[r["suggested_action"]], route(self.results[n])) for n, r in complete.items()], ROUTES),
                          "categories": category_agreement(self.primary, self.results)},
            "root_overlap": {"count": len(self.root_reviews), "confusion_orientation": "rows=root, columns=comparator",
                             "root_routes": counts(pair[0] for pair in root_jev),
                             "assigned_routes": counts(pair[1] for pair in root_pairs),
                             "jev_routes": counts(pair[1] for pair in root_jev),
                             "routes_vs_assigned": agreement(root_pairs, ROUTES),
                             "routes_vs_jev": agreement(root_jev, ROUTES),
                             "categories_vs_assigned": root_assigned,
                             "categories_vs_jev": category_agreement(self.root_reviews, self.results)},
            "stability": self.stability_summary,
        }

    def comparison_rows(self):
        rows = []
        for number in sorted(self.primary):
            review, result, source = self.primary[number], self.results[number], self.inputs[number]
            row = {"issue_number": number, "url": source["url"], "title": source["title"],
                   "input_sha256": source["input_sha256"], **source["coverage"],
                   "reviewer": self.reviewers_by_issue[number], "review_action": review["suggested_action"],
                   "review_route": ACTION_ROUTE[review["suggested_action"]], "jev_status": result["status"],
                   "jev_route": route(result), "jev_reason": result.get("decision", {}).get("reason", result.get("error", "")),
                   "route_agrees": ACTION_ROUTE[review["suggested_action"]] == route(result)}
            for key in KEYS:
                row.update({f"review_{key}": review["labels"][key],
                            f"jev_{key}_probability": result.get("classification", {}).get("probabilities", {}).get(key, ""),
                            f"jev_{key}_label": binary(result, key) if result["status"] == "ok" else ""})
            row.update({"review_reason": review["reason"], "review_flags": json.dumps(review["flags"]),
                        "review_evidence": json.dumps(review["evidence"], ensure_ascii=False),
                        "input_file": self.input_files[number], "request_file": f"requests/{number}.json",
                        "review_file": self.review_files[number], "result_file": f"results/{number}.json",
                        "response_file": result.get("response_file") or ""})
            rows.append(row)
        return rows

    def root_rows(self):
        rows = []
        for number in sorted(self.root_reviews):
            root, assigned, model = self.root_reviews[number], self.primary[number], self.results[number]
            row = {"issue_number": number, "url": self.inputs[number]["url"], "input_sha256": root["input_sha256"],
                   "root_action": root["suggested_action"], "assigned_action": assigned["suggested_action"],
                   "root_route": ACTION_ROUTE[root["suggested_action"]],
                   "assigned_route": ACTION_ROUTE[assigned["suggested_action"]], "jev_route": route(model),
                   "root_assigned_route_agrees": root["suggested_action"] == assigned["suggested_action"],
                   "root_jev_route_agrees": ACTION_ROUTE[root["suggested_action"]] == route(model)}
            for key in KEYS:
                row.update({f"root_{key}": root["labels"][key], f"assigned_{key}": assigned["labels"][key],
                            f"jev_{key}_label": binary(model, key) if model["status"] == "ok" else ""})
            row.update({"root_reason": root["reason"], "assigned_reason": assigned["reason"],
                        "root_review_file": "reviews/root-blind-sample.json", "assigned_review_file": self.review_files[number],
                        "request_file": f"requests/{number}.json", "response_file": model.get("response_file") or ""})
            rows.append(row)
        return rows


def csv_text(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def run(directory, check=False):
    evaluation = Evaluation(directory)
    evaluation.sources()
    evaluation.primary_results()
    evaluation.reviewers()
    evaluation.stability()
    summary = evaluation.summarize()
    # Complete all validation and serialization before creating any report.
    outputs = {"summary.json": json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
               "comparison.csv": csv_text(evaluation.comparison_rows()),
               "root-overlap.csv": csv_text(evaluation.root_rows())}
    if not check:
        with tempfile.TemporaryDirectory(prefix=".evaluation-report-", dir=evaluation.root) as staging:
            for name, content in outputs.items():
                (Path(staging) / name).write_text(content, encoding="utf-8")
            for name in outputs:
                (Path(staging) / name).replace(evaluation.root / name)
    print(json.dumps({"status": "validated" if check else "written", "issues": len(evaluation.requests),
                      "primary_reviews": len(evaluation.primary), "root_reviews": len(evaluation.root_reviews),
                      "reports": list(outputs) if not check else []}))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--check", action="store_true", help="Validate and compute without writing output files")
    args = parser.parse_args()
    try:
        run(args.directory, args.check)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f"Evaluation incomplete or invalid: {error}\n")
