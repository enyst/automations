"""OpenHands Automation entrypoint. No scheduling loop or GitHub webhook server."""

import json
import logging
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from controller import Auditor, AuditStopped, event_target, validate_retired_prs
from github import GitHub
from runtime import cloud_secret, completion, github_transport, state_store
from source import SourceRepo


def run(config):
    mode = config["mode"]
    if mode == "manual-trial":
        from trial import run as run_trial
        return run_trial(config)
    retired = validate_retired_prs(config.get("retired_prs", ()))
    if mode != "bootstrap" and retired:
        repo, number, _, _ = event_target(json.loads(os.environ["AUTOMATION_EVENT_PAYLOAD"]))
        if (repo, number) in retired:
            return {"status": "skipped", "reason": "configured_retired_pr"}
    state = state_store()
    if mode == "bootstrap":
        state.bootstrap()
        return {"status": "bootstrapped"}
    if mode not in {"dry-run", "live"}:
        raise AuditStopped("invalid_runtime_mode")
    state.get()  # Fail before fetching credentials or spending model tokens.
    if os.environ.get("AUTOMATION_MODEL") != "astra-review-auditor":
        raise AuditStopped("astra_profile_must_be_selected")
    from reviewer import create_llm, run_stage

    reader = GitHub(github_transport(cloud_secret(config["reader_secret"])))
    publisher = GitHub(github_transport(cloud_secret(config["publisher_secret"])))
    if (
        not config["publisher_login"]
        or publisher.identity()["login"].lower() != config["publisher_login"].lower()
    ):
        raise AuditStopped("publisher_identity_not_verified")
    llm = create_llm(cloud_secret(config["openai_secret"]))
    skill_dir = Path(__file__).parent / "review-skill"
    skill = "\n\n".join(
        (skill_dir / name).read_text() for name in ("SKILL.md", "risk-evaluation.md")
    )
    with TemporaryDirectory(prefix="review-source-") as directory:
        auditor = Auditor(
            github=reader,
            publisher=publisher,
            state=state,
            source_factory=lambda repo: SourceRepo(repo, Path(directory) / "objects"),
            run_stage=run_stage,
            llm=llm,
            skill_text=skill,
            run_id=os.environ["AUTOMATION_RUN_ID"],
            publish=mode == "live",
            settle_seconds=(1, 2),
            retired_prs=config.get("retired_prs", ()),
        )
        return auditor.handle(json.loads(os.environ["AUTOMATION_EVENT_PAYLOAD"]))


def main():
    # Provider exceptions can contain request content. Keep all runtime output to
    # our explicitly selected status fields, never arbitrary exception strings.
    logging.disable(logging.CRITICAL)
    status, error, exit_code, local_trial = "COMPLETED", None, 0, False
    try:
        config = json.loads((Path(__file__).parent / "config.json").read_text())
        local_trial = config.get("mode") == "manual-trial"
        result = run(config)
        if result.get("status") == "trial_incomplete":
            status, error, exit_code = "FAILED", "trial_incomplete", 1
        print(
            json.dumps(
                {k: v for k, v in result.items() if k in {"status", "reason", "action", "published"}}
            )
        )
    except BaseException as exc:
        status, exit_code = "FAILED", 1
        error = str(exc) if isinstance(exc, AuditStopped) else type(exc).__name__
        print(json.dumps({"status": status, "error_code": error}))
    try:
        options = {"allow_loopback_http": True} if local_trial else {}
        completion(status, error=error, **options)
    except Exception:
        print(json.dumps({"status": "callback_failed"}))
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
