import subprocess
import json
import re
import requests
import cache_util

LLM_SDKS = [
    "openai", "@anthropic-ai/sdk", "anthropic", "@google/generative-ai",
    "google-generativeai", "langchain", "@langchain/core", "llamaindex",
    "litellm", "cohere", "mistralai", "@ai-sdk/openai", "@ai-sdk/anthropic",
]

OBS_VENDORS = [
    "helicone", "langfuse", "langsmith", "traceloop", "arize",
    "openinference", "langwatch",
]

COMPETITOR_DENYLIST = {
    "langchain-ai", "run-llama", "BerriAI", "helicone", "langfuse",
    "langsmith", "traceloop", "arize-ai", "mirascope", "boundaryml",
    "promptfoo", "ragas", "openinference", "langwatch",
}

# Big-tech orgs that publish tutorials/research, not commercial products
BIGTECH_DENYLIST = {
    "microsoft", "google", "google-research", "google-deepmind", "meta",
    "facebook", "facebookresearch", "amazon", "aws", "nvidia", "NVIDIA",
    "openai", "apple", "huggingface", "tencent", "bytedance", "alibaba",
    "DeepSeek-ai", "deepseek-ai", "PaddlePaddle",
}

# Infrastructure / model-serving / OSS-platform orgs — they ARE the layer
# below LLM observability, not consumers. Helicone competes with / sits above them.
INFRA_DENYLIST = {
    "vllm-project", "ollama", "ggerganov", "ggml-org", "llama-cpp",
    "mlflow", "kubeflow", "ray-project", "modal-labs",
    "InternLM", "internlm", "ggml",
}

# Description / name keywords that indicate educational/tutorial/awesome content
NOISE_KEYWORDS = [
    "tutorial", "tutorials", "lesson", "lessons", "course", "courses",
    "beginner", "beginners", "learning", "learn-", "awesome-", "awesome ",
    "examples", "guide ", "guides", "from scratch", "from-scratch",
    "handbook", "cookbook", "playbook", "bootcamp", "workshop",
    "for-beginners", "starter-kit", "boilerplate", "template",
]

# Homepage URLs that indicate non-commercial (academic, learning, hosting-only)
NON_COMMERCIAL_DOMAINS = [
    "arxiv.org", ".edu", ".ac.", "github.io", "readthedocs.io",
    "huggingface.co/papers", "papers.with",
]


def _looks_like_noise(repo: dict) -> tuple[bool, str]:
    """Returns (is_noise, reason) — used to drop tutorials/research/big-tech."""
    name = repo["full_name"].lower()
    desc = (repo.get("description") or "").lower()
    home = (repo.get("homepage") or "").lower()
    owner = repo["owner"].lower()

    if owner in {b.lower() for b in BIGTECH_DENYLIST}:
        return True, f"big-tech org ({repo['owner']})"

    if owner in {b.lower() for b in INFRA_DENYLIST}:
        return True, f"infrastructure layer / OSS platform ({repo['owner']})"

    blob = f"{name} {desc}"
    for kw in NOISE_KEYWORDS:
        if kw in blob:
            return True, f"keyword '{kw.strip()}' in name/description"

    for dom in NON_COMMERCIAL_DOMAINS:
        if dom in home:
            return True, f"non-commercial homepage ({dom})"

    return False, ""


def _gh_api(endpoint: str, params: dict) -> dict:
    cmd = ["gh", "api", "-X", "GET", endpoint]
    for k, v in params.items():
        cmd.extend(["-f", f"{k}={v}"])
    result = subprocess.run(
        cmd, capture_output=True, timeout=60,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api failed: {result.stderr}")
    return json.loads(result.stdout)


def search_topic_repos(topics: list[str], min_stars: int = 100, pushed_after: str = "2025-10-01") -> list[dict]:
    payload = {"topics": topics, "min_stars": min_stars, "pushed_after": pushed_after}
    cached = cache_util.get("topic_search", payload)
    if cached is not None:
        return cached

    all_repos = {}
    for topic in topics:
        q = f"topic:{topic} stars:>{min_stars} pushed:>{pushed_after}"
        try:
            data = _gh_api("search/repositories", {"q": q, "per_page": "50", "sort": "stars"})
        except Exception as e:
            print(f"Topic {topic} failed: {e}")
            continue
        for item in data.get("items", []):
            full = item["full_name"]
            if full not in all_repos:
                all_repos[full] = {
                    "full_name": full,
                    "owner": item["owner"]["login"],
                    "owner_type": item["owner"]["type"],
                    "stars": item["stargazers_count"],
                    "pushed_at": item["pushed_at"],
                    "homepage": item.get("homepage"),
                    "description": item.get("description") or "",
                    "fork": item["fork"],
                    "archived": item.get("archived", False),
                    "topic_hits": [topic],
                }
            else:
                all_repos[full]["topic_hits"].append(topic)

    result = list(all_repos.values())
    cache_util.put("topic_search", payload, result)
    return result


def filter_real_companies(repos: list[dict]) -> list[dict]:
    seen_owners = set()
    filtered = []
    for r in sorted(repos, key=lambda x: -x["stars"]):
        if r["owner"] in seen_owners:
            continue
        if r["owner"] in COMPETITOR_DENYLIST:
            continue
        if r["owner_type"] != "Organization":
            continue
        if not r["homepage"]:
            continue
        if r["fork"] or r["archived"]:
            continue
        is_noise, reason = _looks_like_noise(r)
        if is_noise:
            r["_dropped_reason"] = reason
            continue
        seen_owners.add(r["owner"])
        filtered.append(r)
    return filtered


def fetch_deps_text(full_name: str) -> tuple[str, str]:
    """Returns (filename, content) of first deps file found, or ('', '')."""
    payload = {"repo": full_name}
    cached = cache_util.get("deps", payload)
    if cached is not None:
        return cached["filename"], cached["content"]

    for fname in ["package.json", "pyproject.toml", "requirements.txt"]:
        url = f"https://raw.githubusercontent.com/{full_name}/HEAD/{fname}"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200 and len(r.text) > 10:
                cache_util.put("deps", payload, {"filename": fname, "content": r.text})
                return fname, r.text
        except Exception:
            continue

    cache_util.put("deps", payload, {"filename": "", "content": ""})
    return "", ""


def strip_comments_and_optional(text: str, deps_file: str) -> str:
    """Drop commented lines and lines inside [optional]/[dev]/[test] sections.
    Used to avoid false positives like '# openai-agents  # optional: examples'."""
    if not text:
        return ""
    out = []
    in_drop_section = False
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        # requirements.txt comments
        if deps_file.endswith(".txt") and stripped.startswith("#"):
            continue
        # pyproject.toml comments + optional/dev/test sections
        if deps_file.endswith(".toml"):
            if stripped.startswith("#"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                sec = stripped[1:-1].lower()
                in_drop_section = any(k in sec for k in ("optional", "dev", "test", "lint", "docs"))
                if not in_drop_section:
                    out.append(line)
                continue
            if in_drop_section:
                continue
        out.append(line)
    return "\n".join(out)


def signal_check(repo: dict) -> dict:
    fname, raw_text = fetch_deps_text(repo["full_name"])
    text = strip_comments_and_optional(raw_text, fname)
    repo["deps_file"] = fname
    repo["deps_text_clean"] = text  # cache for evidence step
    repo["llm_sdks"] = []
    repo["obs_vendors"] = []

    if not text:
        repo["status"] = "UNKNOWN"
        return repo

    text_lower = text.lower()
    for sdk in LLM_SDKS:
        if sdk.lower() in text_lower:
            repo["llm_sdks"].append(sdk)

    for vendor in OBS_VENDORS:
        # word-boundary match to avoid "summarize" matching "arize"
        if re.search(rf"\b{re.escape(vendor)}\b", text_lower):
            repo["obs_vendors"].append(vendor)

    if repo["obs_vendors"]:
        repo["status"] = "COLD"
    elif len(repo["llm_sdks"]) >= 2:
        repo["status"] = "HOT"
    elif len(repo["llm_sdks"]) == 1:
        repo["status"] = "WARM"
    else:
        repo["status"] = "UNKNOWN"
    return repo


def fetch_top_contributors(repo_full: str, n: int = 5) -> list[dict]:
    """Top N committers, free API call, owner-of-repo filtered out."""
    payload = {"repo": repo_full, "n": n}
    cached = cache_util.get("contributors", payload)
    if cached is not None:
        return cached
    try:
        data = _gh_api(f"repos/{repo_full}/contributors", {"per_page": str(n + 5)})
        owner = repo_full.split("/")[0].lower()
        out = []
        for c in data:
            login = c.get("login", "")
            if not login or login.endswith("[bot]") or login.lower() == owner:
                continue
            out.append({
                "login": login,
                "commits": c.get("contributions", 0),
                "avatar": c.get("avatar_url", ""),
                "github_url": c.get("html_url", ""),
                "linkedin_search_url": f"https://www.linkedin.com/search/results/people/?keywords={login}%20{owner}",
            })
            if len(out) >= n:
                break
        return cache_util.put("contributors", payload, out)
    except Exception:
        return []


def search_and_qualify(
    topics: list[str],
    min_stars: int = 50,
    max_results: int = 15,
    max_age_days: int = 90,
    candidate_pool_size: int = 50,
    with_evidence: bool = True,
) -> list[dict]:
    """
    Full pipeline:
      topic search → filter to real companies → signal check → date enrichment →
      drop stale (signal older than max_age_days) → return top N.

    candidate_pool_size = how many real-company repos to enrich (more = better
    chance of filling page with fresh signals, but slower first run).
    """
    import signal_evidence
    raw = search_topic_repos(topics, min_stars=min_stars)
    real = filter_real_companies(raw)[:candidate_pool_size]
    qualified_pre_age = [signal_check(r) for r in real]
    qualified_pre_age = [r for r in qualified_pre_age if r["status"] in ("HOT", "WARM")]

    if not with_evidence:
        return qualified_pre_age[:max_results]

    # Enrich + date-filter incrementally; stop when we've collected enough fresh leads
    fresh = []
    for r in qualified_pre_age:
        text = r.get("deps_text_clean") or ""
        if not text:
            _, raw = fetch_deps_text(r["full_name"])
            text = strip_comments_and_optional(raw, r["deps_file"])
        signal_evidence.enrich_with_evidence(r, text)
        sig = r.get("signal_complete_date")
        if not sig:
            continue
        # Drop if definitely stale (> max_age_days, regardless of approximate)
        if sig["days_ago"] > max_age_days:
            continue
        fresh.append(r)
        if len(fresh) >= max_results:
            break

    return fresh
