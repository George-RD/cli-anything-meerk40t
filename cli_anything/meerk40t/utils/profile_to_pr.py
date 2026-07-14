"""Fail-closed issue -> profile PR automation.

Extracted from ``.github/workflows/profile-to-pr.yml`` so the JSON extraction,
the optimistic-concurrency freshness checks, and the publish flow
(branch/commit/push/PR) live in a single tested source of truth. The workflow
calls :func:`cli_main`; unit tests drive :func:`run_flow` with injected
``gh``/``git`` runners so stale revisions and every subprocess failure can be
asserted to abort the job with a nonzero exit.

Ordering contract (fail-closed):
  1. Parse + validate the issue body in memory (no workspace mutation).
  2. Collision probe (remote + local branch) -- read-only.
  3. Refresh the canonical base with ``git fetch``.
  4. Boundary 1 -- re-fetch the live issue and compare state/label/body hash
     and re-validate. Abort *before* ``git checkout`` on any drift. (Placed
     after the base fetch so an edit arriving during a slow network fetch is
     still caught.)
  5. Checkout a fresh branch and only now write the profile, then commit.
  6. Boundary 2 -- re-fetch + re-verify. Abort *before* push on any drift.
  7. Push + open PR (no auto-merge).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

from .submit import validate_submission

PROFILE_DIR = "profiles/community"

# ```lang\n...\n```  -- capture (language, content) pairs.
_FENCE_RE = re.compile(r"```([a-zA-Z0-9_-]*)[ \t]*\n(.*?)```", re.DOTALL)


class ProfileToPrError(Exception):
    """Base class for aborting the flow with a nonzero exit."""


class ValidationError(ProfileToPrError):
    """Issue body could not be parsed/validated into a profile."""


class FreshnessError(ProfileToPrError):
    """Live issue diverged from the validated snapshot."""


def extract_profile_json(body: str):
    """Return the profile JSON text, preferring a ```json fence.

    Falls back to the first code block when no ``json`` fence exists. Returns
    ``None`` when the body contains no code block at all.
    """
    json_block = None
    first_block = None
    for lang, content in _FENCE_RE.findall(body):
        if first_block is None:
            first_block = content.strip()
        if lang.lower() == "json":
            json_block = content.strip()
            break
    return json_block if json_block is not None else first_block


def compute_body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def check_freshness(snapshot_hash: str, live: dict, validate) -> None:
    """Raise FreshnessError if the live issue diverged from the snapshot."""
    if str(live.get("state", "")).lower() != "open":
        raise FreshnessError("issue is no longer open")
    labels = {lbl.get("name") for lbl in live.get("labels", [])}
    if "community-profile" not in labels:
        raise FreshnessError("community-profile label removed")
    body = live.get("body", "")
    chosen = extract_profile_json(body)
    if chosen is None:
        raise FreshnessError("issue body has no code block")
    try:
        prof = json.loads(chosen)
    except Exception as exc:  # noqa: BLE001
        raise FreshnessError(f"live profile JSON unparseable: {exc}") from exc
    errs = validate(prof)
    if errs:
        raise FreshnessError("live profile no longer validates: " + "; ".join(errs))
    if compute_body_hash(body) != snapshot_hash:
        raise FreshnessError("issue body drifted since validation")


def _branch_name(name: str) -> str:
    return f"profile/{name}"


def _expect_rc(run, args, allowed):
    """Run a probe and treat any exit code outside ``allowed`` as a failure.

    ``git show-ref --verify --quiet`` and ``git diff --cached --quiet`` use the
    exit code as a *signal* (0/1), so they must run with ``check=False``. But a
    different code means the command itself broke (e.g. rc 128) and must still
    fail the job closed rather than be silently treated as "no branch"/"no
    diff".
    """
    rc = run(args, check=False)
    if rc not in allowed:
        raise subprocess.CalledProcessError(rc, args)
    return rc


def run_flow(
    *,
    issue_number,
    load_issue_body,
    fetch_issue,
    post_comment,
    run,
    write_profile,
    validate=validate_submission,
    base_branch: str = "main",
):
    """Execute the full flow; return a process exit code (0 = published)."""
    try:
        # 1. Parse + validate in memory (no workspace mutation).
        body = load_issue_body()
        chosen = extract_profile_json(body)
        if chosen is None:
            post_comment(issue_number, _no_block_message())
            return 1
        try:
            prof = json.loads(chosen)
        except Exception as exc:  # noqa: BLE001
            post_comment(issue_number, f"The JSON in the code block is not valid: {exc}")
            return 1
        errs = validate(prof)
        if errs:
            post_comment(issue_number, "Profile validation failed:\n- " + "\n- ".join(errs))
            return 1

        name = prof["name"]
        snapshot_hash = compute_body_hash(body)
        branch = _branch_name(name)

        # 2. Collision probe (read-only).
        remote = run(["git", "ls-remote", "--heads", "origin", branch], capture=True)
        if f"refs/heads/{branch}" in remote:
            print(f"Remote branch {branch} already exists; aborting.")
            return 1
        # `git show-ref --verify --quiet` exits 0 when the branch EXISTS, 1 when
        # absent; any other code means the probe itself broke.
        show_rc = _expect_rc(
            run, ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], (0, 1)
        )
        if show_rc == 0:
            print(f"Local branch {branch} already exists; aborting.")
            return 1

        # 3. Refresh the canonical base.
        run(["git", "fetch", "--no-tags", "origin", base_branch])

        # 4. Boundary 1 -- immediately before branch creation (after fetch).
        live = fetch_issue(issue_number)
        try:
            check_freshness(snapshot_hash, live, validate)
        except FreshnessError as exc:
            print(f"Aborting before branch creation: {exc}")
            return 1

        # 5. Fresh branch, only now write the profile, then commit.
        run(["git", "checkout", "-b", branch, f"origin/{base_branch}"])
        path = write_profile(name, json.dumps(prof, indent=2))
        run(["git", "add", path])
        # `git diff --cached --quiet` exits 0 when nothing changed, 1 when there
        # are staged changes; any other code means the probe itself broke.
        diff_rc = _expect_rc(run, ["git", "diff", "--cached", "--quiet"], (0, 1))
        if diff_rc == 0:
            print("Profile already present and unchanged; aborting.")
            return 1
        run([
            "git", "-c", "user.name=github-actions[bot]",
            "-c", "user.email=github-actions[bot]@users.noreply.github.com",
            "commit", "-m", f"Add community profile {name}",
        ])

        # 6. Boundary 2 -- before push.
        live = fetch_issue(issue_number)
        try:
            check_freshness(snapshot_hash, live, validate)
        except FreshnessError as exc:
            print(f"Aborting before push: {exc}")
            return 1

        # 7. Publish (no auto-merge).
        run(["git", "push", "-u", "origin", branch])
        run([
            "gh", "pr", "create",
            "--head", branch,
            "--base", base_branch,
            "--title", f"Community machine profile: {name}",
            "--body", _pr_body(name, issue_number),
        ])
        return 0
    except subprocess.CalledProcessError as exc:
        cmd = exc.cmd if isinstance(exc.cmd, list) else str(exc.cmd).split()
        print(f"Command failed (exit {exc.returncode}): {' '.join(cmd)}")
        return 1


def _no_block_message() -> str:
    return (
        "No code block was found in the issue body. Please paste the complete "
        "profile JSON inside a ```json ... ``` block."
    )


def _pr_body(name: str, issue_number) -> str:
    return (
        f"Adds `profiles/community/{name}.json` from issue #{issue_number}.\n\n"
        "Submitted values should come from `device setup --save-profile` (live "
        "readback), the device state machine, and the firmware banner, and not "
        "be guessed.\n\n"
        "This PR was opened by automation; it is intentionally not merged "
        "automatically."
    )


def _real_run(args, check=True, capture=False):
    if capture:
        result = subprocess.run(args, check=check, capture_output=True, text=True)
        return result.stdout
    result = subprocess.run(args, check=check)
    return result.returncode


def _real_fetch_issue(num):
    out = subprocess.run(
        ["gh", "issue", "view", str(num), "--json", "body,labels,state"],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def _real_post_comment(num, text):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(text)
        tmp = fh.name
    try:
        subprocess.run(["gh", "issue", "comment", str(num), "--body-file", tmp], check=True)
    finally:
        os.unlink(tmp)


def _real_write_profile(name, text):
    path = os.path.join(PROFILE_DIR, f"{name}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def cli_main() -> None:
    issue_number = int(os.environ["ISSUE_NUMBER"])
    code = run_flow(
        issue_number=issue_number,
        load_issue_body=lambda: os.environ["ISSUE_BODY"],
        fetch_issue=_real_fetch_issue,
        post_comment=_real_post_comment,
        run=_real_run,
        write_profile=_real_write_profile,
        validate=validate_submission,
    )
    sys.exit(code)


if __name__ == "__main__":
    cli_main()
