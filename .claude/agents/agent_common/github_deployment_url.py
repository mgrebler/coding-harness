#!/usr/bin/env python3
"""Wait for a commit's GitHub Deployment to succeed and print its URL.

A ready-made `validation.deploy_command` for projects whose preview deploys
are recorded as GitHub Deployments — Vercel's GitHub integration does this,
as does any GitHub Actions job that declares `environment: {name, url}`.
Deploys triggered by the push or by the PR have already started by the time
ch_6_validate_auto.py runs this, so it only waits; it never triggers one.

    python .claude/agents/agent_common/github_deployment_url.py --environment Preview

Reads VALIDATION_SHA / VALIDATION_BRANCH (set by ch_6_validate_auto.py) unless
--sha/--branch are given. Prints only the URL on stdout; progress goes to
stderr. Exit codes: 0 deployed, 1 failed or timed out, 3 superseded (the
deployment failed or was cancelled because the branch has moved past this
commit — re-run on the new head rather than treating it as a deploy failure).

Stdlib only, like pipeline_status.py: a deploy_command runs as its own
process, possibly under a python3 without the harness's dependencies."""

import argparse
import json
import os
import subprocess
import sys
import time

SUPERSEDED_EXIT_CODE = 3
_REFUSED_ENVIRONMENT = "production"
_FAILED_STATES = ("failure", "error", "inactive")


def _log(msg: str) -> None:
    print(f"[github-deployment-url] {msg}", file=sys.stderr, flush=True)


def _gh_api(path: str) -> list:
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def matching_deployments(deployments: list, environment: str) -> list:
    """Deployments whose environment contains environment (case-insensitive),
    newest first. Anything naming production is never a candidate: validation
    drives a real browser that may write data, so it must not land on prod."""
    wanted = environment.lower()
    matches = [
        d
        for d in deployments
        if wanted in d.get("environment", "").lower()
        and _REFUSED_ENVIRONMENT not in d.get("environment", "").lower()
    ]
    return sorted(matches, key=lambda d: d.get("created_at", ""), reverse=True)


def deployment_url(statuses: list) -> str | None:
    """The URL from the newest success status, if any. A later `inactive`
    doesn't disqualify it: GitHub marks a deployment inactive as soon as a
    newer one lands in the same environment (e.g. another branch's preview),
    but the earlier preview URL still serves this commit."""
    for status in statuses:
        if status.get("state") == "success":
            return status.get("environment_url") or status.get("target_url") or None
    return None


def remote_head(branch: str) -> str | None:
    result = subprocess.run(
        ["git", "ls-remote", "origin", f"refs/heads/{branch}"], capture_output=True, text=True
    )
    fields = result.stdout.split()
    return fields[0] if result.returncode == 0 and fields else None


def wait(sha: str, branch: str, environment: str, timeout_s: float, interval_s: float) -> int:
    deadline = time.monotonic() + timeout_s
    seen_any = False
    while True:
        deployments = matching_deployments(
            _gh_api(f"repos/{{owner}}/{{repo}}/deployments?sha={sha}&per_page=100"), environment
        )
        for deployment in deployments:
            seen_any = True
            statuses = _gh_api(
                f"repos/{{owner}}/{{repo}}/deployments/{deployment['id']}/statuses?per_page=100"
            )
            if url := deployment_url(statuses):
                print(url)
                return 0
            latest = statuses[0]["state"] if statuses else "pending"
            if latest in _FAILED_STATES:
                head = remote_head(branch)
                if head and head != sha:
                    _log(f"Deployment of {sha[:12]} {latest}: superseded by {head[:12]}.")
                    return SUPERSEDED_EXIT_CODE
                _log(f"Deployment {deployment['id']} ({deployment['environment']}) {latest}.")
                return 1
            _log(f"Deployment {deployment['id']} ({deployment['environment']}) is {latest}...")
        if not deployments:
            _log(f"No '{environment}' deployment for {sha[:12]} yet...")
        if time.monotonic() >= deadline:
            break
        time.sleep(interval_s)
    if seen_any:
        _log(f"Timed out waiting for the deployment of {sha[:12]} to finish.")
    else:
        _log(
            f"Timed out: no '{environment}' deployment was ever created for {sha[:12]}. If "
            "the deploy is PR-triggered, check the workflow doesn't skip draft PRs "
            "(`if: ... draft == false`) — the validation stage opens its PR as a draft."
        )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--environment", default="Preview", help="substring to match")
    parser.add_argument("--sha", default=os.environ.get("VALIDATION_SHA"))
    parser.add_argument("--branch", default=os.environ.get("VALIDATION_BRANCH"))
    parser.add_argument("--timeout-minutes", type=float, default=20)
    parser.add_argument("--interval-seconds", type=float, default=15)
    args = parser.parse_args(argv)
    if not args.sha or not args.branch:
        parser.error("--sha/--branch (or VALIDATION_SHA/VALIDATION_BRANCH) are required")
    return wait(
        args.sha, args.branch, args.environment, args.timeout_minutes * 60, args.interval_seconds
    )


if __name__ == "__main__":
    sys.exit(main())
