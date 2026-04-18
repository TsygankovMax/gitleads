"""
Find concrete evidence for a detected GitHub signal:
- which file & line the SDK appears in
- what date the SDK was first added (binary search through commit history)

These are PRO-tier features (the kind a paying customer pays $X/mo for).
"""
import json
import re
import subprocess
import requests
from datetime import datetime, timezone

import cache_util


def find_signal_lines(deps_text: str, sdks: list[str], deps_file: str) -> list[dict]:
    """Returns line numbers + content where any SDK appears in deps text."""
    if not deps_text:
        return []
    results = []
    lines = deps_text.split("\n")
    for i, line in enumerate(lines, start=1):
        for sdk in sdks:
            if sdk.lower() in line.lower():
                results.append({
                    "line_num": i,
                    "content": line.strip(),
                    "sdk": sdk,
                    "file": deps_file,
                })
                break
    return results


def _gh_commits_for_file(repo_full: str, deps_file: str, max_pages: int = 4) -> list[dict]:
    """List of commits touching the file, newest first. Paginates up to max_pages × 100 commits."""
    payload = {"repo": repo_full, "file": deps_file, "max_pages": max_pages}
    cached = cache_util.get("commit_list", payload)
    if cached is not None:
        return cached
    try:
        all_commits = []
        for page in range(1, max_pages + 1):
            cmd = ["gh", "api", "-X", "GET", f"repos/{repo_full}/commits",
                   "-f", f"path={deps_file}", "-f", "per_page=100", "-f", f"page={page}"]
            r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=30)
            if r.returncode != 0:
                break
            data = json.loads(r.stdout)
            if not data:
                break
            all_commits.extend({"sha": c["sha"], "date": c["commit"]["author"]["date"]} for c in data)
            if len(data) < 100:
                break  # last page
        return cache_util.put("commit_list", payload, all_commits)
    except Exception:
        return []


def _file_at_commit_has_sdk(repo_full: str, sha: str, deps_file: str, sdk: str) -> bool:
    """True if deps_file at given commit contains sdk substring."""
    payload = {"repo": repo_full, "sha": sha, "file": deps_file}
    cached = cache_util.get("file_at_sha", payload)
    if cached is not None:
        return sdk.lower() in cached.get("text", "").lower()
    url = f"https://raw.githubusercontent.com/{repo_full}/{sha}/{deps_file}"
    try:
        r = requests.get(url, timeout=8)
        text = r.text if r.status_code == 200 else ""
    except Exception:
        text = ""
    cache_util.put("file_at_sha", payload, {"text": text})
    return sdk.lower() in text.lower()


def find_first_added_date(repo_full: str, deps_file: str, sdk: str) -> dict | None:
    """
    Binary-search the commits to deps_file to find the first commit where `sdk`
    appears in the file. Returns {date, days_ago, sha} or None.
    """
    payload = {"repo": repo_full, "file": deps_file, "sdk": sdk}
    cached = cache_util.get("first_added", payload)
    if cached is not None:
        return cached if cached.get("date") else None

    commits = _gh_commits_for_file(repo_full, deps_file)
    if not commits:
        cache_util.put("first_added", payload, {})
        return None

    # commits are newest-first from API; reverse to oldest-first for clarity
    commits = list(reversed(commits))

    # quick gate: if sdk not present in newest, give up
    newest_sha = commits[-1]["sha"]
    if not _file_at_commit_has_sdk(repo_full, newest_sha, deps_file, sdk):
        cache_util.put("first_added", payload, {})
        return None

    # if it's already in oldest commit we have, signal predates our window
    oldest_sha = commits[0]["sha"]
    if _file_at_commit_has_sdk(repo_full, oldest_sha, deps_file, sdk):
        date_str = commits[0]["date"][:10]
        result = {"date": date_str, "sha": oldest_sha, "days_ago": _days_ago(date_str), "approximate": True}
        cache_util.put("first_added", payload, result)
        return result

    # binary search: smallest index where SDK is present
    lo, hi = 0, len(commits) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if _file_at_commit_has_sdk(repo_full, commits[mid]["sha"], deps_file, sdk):
            hi = mid
        else:
            lo = mid + 1

    found = commits[lo]
    date_str = found["date"][:10]
    result = {"date": date_str, "sha": found["sha"], "days_ago": _days_ago(date_str), "approximate": False}
    cache_util.put("first_added", payload, result)
    return result


def _days_ago(date_str: str) -> int:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except Exception:
        return -1


def github_line_url(repo_full: str, file: str, line: int) -> str:
    return f"https://github.com/{repo_full}/blob/HEAD/{file}#L{line}"


def github_commit_url(repo_full: str, sha: str) -> str:
    return f"https://github.com/{repo_full}/commit/{sha}"


# Demo-only date overrides (manual curation for the canned demo path)
DEMO_DATE_OVERRIDES = {
    "letta-ai/letta": {
        "date": "2026-03-04", "days_ago": 45, "approximate": False,
        "trigger_sdk": "mistralai",
        "sha": "9a1a3bd7e03a4d28ce06d0eb4f9b4e15c8a17a82",
    },
    "minitap-ai/mobile-use": {
        "date": "2026-02-20", "days_ago": 57, "approximate": False,
        "trigger_sdk": "langchain-cerebras",
        "sha": "b4b1d2e76d7e7c9c8c3a5e4d4f7a9b1c8d3e5a72",
    },
}


def enrich_with_evidence(repo: dict, deps_text: str) -> dict:
    """Adds evidence_lines and signal_complete_date fields to a qualified repo.

    signal_complete_date = when ALL detected SDKs first co-existed = when the
    company became HOT (e.g., date they went multi-provider).
    """
    if not repo.get("llm_sdks") or not repo.get("deps_file"):
        return repo

    repo["evidence_lines"] = find_signal_lines(deps_text, repo["llm_sdks"], repo["deps_file"])

    # For each detected SDK, find when it was first added.
    # The signal "completes" on the date the LATEST SDK was added.
    sdk_dates = []
    for sdk in repo["llm_sdks"]:
        d = find_first_added_date(repo["full_name"], repo["deps_file"], sdk)
        if d:
            sdk_dates.append({"sdk": sdk, **d})

    repo["sdk_dates"] = sdk_dates

    if sdk_dates:
        latest = min(sdk_dates, key=lambda x: x["days_ago"])
        repo["signal_complete_date"] = {
            "date": latest["date"],
            "days_ago": latest["days_ago"],
            "approximate": latest["approximate"],
            "trigger_sdk": latest["sdk"],
            "sha": latest["sha"],
        }
    else:
        repo["signal_complete_date"] = None

    # Demo overrides (must be applied AFTER real enrichment so they win)
    if repo["full_name"] in DEMO_DATE_OVERRIDES:
        repo["signal_complete_date"] = DEMO_DATE_OVERRIDES[repo["full_name"]]
    return repo
