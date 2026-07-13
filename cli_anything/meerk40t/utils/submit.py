"""Community machine-profile submission.

This module turns a loaded machine profile into a community submission.
It validates the profile against the community schema, builds a pre-filled
GitHub new-issue URL (used as the fallback when the ``gh`` CLI is not
available or the operator has not passed ``--yes``), and optionally drives
the ``gh`` CLI to fork, branch, and open a pull request.

The schema here is the single source of truth for the submission contract.
The ``.github/workflows/profile-to-pr.yml`` workflow validates the same
keys in the issue body so a profile accepted by the CLI is also accepted
by the automation.

No secrets or tokens are handled client-side: the only network action is
the ``gh`` CLI, which uses the operator's existing authenticated session.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from cli_anything.meerk40t.utils import profiles

# Upstream repository that owns the community profile collection.
REPO = "George-RD/cli-anything-meerk40t"
ISSUE_URL_BASE = f"https://github.com/{REPO}/issues/new"
COMMUNITY_DIR = "profiles/community"

# Canonical community submission schema. Every key is required and must
# match the listed Python type (booleans and ints are kept distinct).
REQUIRED_KEYS = (
    "name",
    "device",
    "baud",
    "bedwidth",
    "bedheight",
    "has_endstops",
    "notes",
    "provenance",
)
_TYPE_OF = {
    "name": str,
    "device": str,
    "baud": int,
    "bedwidth": str,
    "bedheight": str,
    "has_endstops": bool,
    "notes": str,
    "provenance": dict,
}

# Safe filename pattern: matches ``profiles.load_profile`` name rules so a
# profile name can be used directly as a JSON filename.
_NAME_RE = profiles.PROFILE_NAME_RE


def validate_submission(profile: Any) -> list[str]:
    """Return a list of human-readable validation errors (empty means valid).

    The profile is checked for the required keys, correct value types, and a
    safe ``name`` that can be used as a filename. Booleans and ints are kept
    distinct (``True`` is not accepted where ``int`` is expected).
    """
    errors: list[str] = []
    if not isinstance(profile, dict):
        return ["profile must be a JSON object"]

    for key in REQUIRED_KEYS:
        if key not in profile:
            errors.append(f"missing required key: {key}")

    for key, expected in _TYPE_OF.items():
        if key not in profile or profile[key] is None:
            continue
        value = profile[key]
        # isinstance(True, int) is True in Python; keep them apart.
        if expected is int and isinstance(value, bool):
            errors.append(f"key {key} must be an int (got bool)")
            continue
        if expected is bool and isinstance(value, int) and not isinstance(value, bool):
            errors.append(f"key {key} must be a bool (got int)")
            continue
        if not isinstance(value, expected):
            errors.append(f"key {key} must be {expected.__name__}")

    name = profile.get("name")
    if name is not None and (not isinstance(name, str) or not _NAME_RE.match(name)):
        errors.append(f"key name must match {_NAME_RE.pattern}")

    provenance = profile.get("provenance")
    if isinstance(provenance, dict) and not provenance:
        errors.append("key provenance must not be empty")

    return errors


def _issue_body(profile: dict) -> str:
    """Render the provenance-aware body used in the issue and PR."""
    prov = profile.get("provenance") or {}
    firmware = prov.get("firmware", "unknown")
    verified = prov.get("verified", False)
    lines = [
        "Community machine profile submission.",
        "",
        f"Device: {profile.get('device')}",
        f"Firmware: {firmware}",
        f"Verified readback: {verified}",
        "",
        "Profile JSON:",
        "",
        "```json",
        json.dumps(profile, indent=2, sort_keys=True),
        "```",
        "",
        (
            "Submitted via `cli-anything-meerk40t profile submit`. "
            "Values should come from `device setup --save-profile` (live "
            "$$ readback), the device state machine, and the firmware banner."
        ),
    ]
    return "\n".join(lines)


def build_issue_url(profile: dict) -> str:
    """Build a URL-encoded GitHub new-issue URL pre-filled with the profile.

    Opens ``https://github.com/<REPO>/issues/new`` with ``title``, ``body``,
    and ``labels=community-profile`` query parameters. The query is encoded
    so spaces and symbols are safe in a browser address bar.
    """
    name = profile.get("name", "unknown")
    title = f"Community machine profile: {name}"
    params = {
        "title": title,
        "body": _issue_body(profile),
        "labels": "community-profile",
    }
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"{ISSUE_URL_BASE}?{query}"


def build_confirm_command(name: str) -> str:
    """Return the exact command an operator runs to actually submit."""
    return f"cli-anything-meerk40t profile submit {name} --yes"


def gh_installed() -> bool:
    """True when the ``gh`` CLI is on PATH (authenticated use is separate)."""
    return shutil.which("gh") is not None


def submit_profile(
    name: str,
    config_home: str | None = None,
    yes: bool = False,
) -> dict:
    """Plan (or, with ``yes``, perform) a community profile submission.

    Without ``yes`` this is side-effect free: it loads and validates the
    named profile, then returns the plan (the target file path, the profile
    JSON, a pre-filled issue URL, and the confirm command). Nothing is sent.

    With ``yes`` and the ``gh`` CLI available it attempts a fork/branch/PR;
    otherwise it falls back to the issue-URL plan with no side effects.
    """
    profile = profiles.load_profile(name, config_home=config_home)
    if profile is None:
        return {
            "ok": False,
            "error": f"unknown profile: {name!r}",
            "name": name,
            "known": profiles.available_names(config_home),
        }

    errors = validate_submission(profile)
    if errors:
        return {
            "ok": False,
            "error": "profile failed submission validation",
            "name": name,
            "validation_errors": errors,
        }

    community_file = f"{COMMUNITY_DIR}/{name}.json"
    issue_url = build_issue_url(profile)
    plan: dict = {
        "ok": True,
        "name": name,
        "community_file": community_file,
        "profile": profile,
        "issue_url": issue_url,
        "submitted": False,
    }
    if gh_installed():
        plan["confirm_command"] = build_confirm_command(name)

    if not yes:
        return plan

    if not gh_installed():
        # No authenticated CLI: leave the operator to open the issue URL.
        plan["method"] = "issue-url"
        plan["note"] = (
            "gh CLI not available; open issue_url in a browser to submit."
        )
        return plan

    result = _submit_via_gh(name, profile, community_file)
    plan.update(result)
    return plan


def _run(cmd: list[str], cwd: str | None = None, capture: bool = False) -> str:
    """Run a command, returning stdout when ``capture`` is set."""
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        capture_output=capture,
        text=True,
    )
    return proc.stdout or ""


def _fail(stage: str, error: object) -> dict:
    """Structured, non-raising failure for a given submission stage."""
    return {"ok": False, "stage": stage, "error": str(error)}


def _origin_owner_name(url: str) -> tuple[str | None, str | None]:
    """Parse (owner, name) from a GitHub clone URL, else (None, None).

    Handles both ``https://github.com/OWNER/NAME(.git)?`` and the SSH form
    ``git@github.com:OWNER/NAME.git``.
    """
    m = re.search(r"github\.com[/:]([^/]+)/(.+?)(?:\.git)?$", (url or "").strip())
    if not m:
        return (None, None)
    return (m.group(1), m.group(2))


def _is_already_forked(exc: Exception) -> bool:
    """True when a fork failure means the fork already exists."""
    stderr = getattr(exc, "stderr", "") or ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    text = stderr + str(exc)
    return bool(re.search(r"already (exists|forked)", text, re.I))


def _submit_via_gh(name: str, profile: dict, community_file: str) -> dict:
    """Drive the gh CLI to fork, branch, write, and open a PR.

    Runs entirely inside a disposable temp clone so the caller's working
    tree is never touched. Any failure is reported via ``_fail`` (it never
    raises) so the caller can fall back to the issue URL. Requires an
    authenticated ``gh`` session.
    """
    branch = f"profile/{name}"
    with tempfile.TemporaryDirectory(prefix="clia_submit_") as work:
        clone_dir = os.path.join(work, "checkout")
        try:
            _run(["gh", "repo", "fork", REPO, "--clone=false"], cwd=work, capture=True)
        except subprocess.CalledProcessError as e:
            if not _is_already_forked(e):
                return _fail("fork", e)
        try:
            _run(["gh", "repo", "clone", REPO, clone_dir], cwd=work)
        except subprocess.CalledProcessError as e:
            return _fail("clone", e)
        try:
            origin = _run(
                ["git", "-C", clone_dir, "remote", "get-url", "origin"],
                capture=True,
            ).strip()
        except subprocess.CalledProcessError as e:
            return _fail("identity", e)
        owner, name_ = _origin_owner_name(origin)
        if (owner, name_) != ("George-RD", "cli-anything-meerk40t"):
            return _fail("identity", f"unexpected origin: {origin}")
        try:
            _run(
                ["git", "-C", clone_dir, "rev-parse", "--verify", branch],
                capture=True,
            )
        except subprocess.CalledProcessError:
            pass
        else:
            return _fail("checkout", f"branch {branch} already exists locally")
        try:
            remote = _run(
                ["git", "-C", clone_dir, "ls-remote", "--heads", "origin", branch],
                capture=True,
            ).strip()
        except subprocess.CalledProcessError as e:
            return _fail("checkout", e)
        if remote:
            return _fail("checkout", f"branch {branch} already exists on remote")
        try:
            _run(["git", "-C", clone_dir, "checkout", "-b", branch], cwd=clone_dir)
        except subprocess.CalledProcessError as e:
            return _fail("checkout", e)
        out_path = Path(clone_dir) / community_file
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            return _fail("write", e)
        try:
            _run(["git", "-C", clone_dir, "add", str(out_path)])
            _run(
                ["git", "-C", clone_dir, "commit", "-m", f"Add community profile {name}"],
            )
            _run(
                ["git", "-C", clone_dir, "push", "-u", "origin", branch],
            )
        except subprocess.CalledProcessError as e:
            return _fail("push", e)
        try:
            pr_url = _run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--title",
                    f"Community machine profile: {name}",
                    "--body",
                    _issue_body(profile),
                    "--label",
                    "community-profile",
                ],
                cwd=clone_dir,
                capture=True,
            ).strip()
        except subprocess.CalledProcessError as e:
            return _fail("pr-create", e)
        return {
            "submitted": True,
            "method": "pull-request",
            "branch": branch,
            "pr_url": pr_url,
        }
