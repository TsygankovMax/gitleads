"""Pre-warm cache for demo. Run once before live demo to ensure instant results."""
from dotenv import load_dotenv
load_dotenv()

import cache_util
import github_search
import contact_lookup

PRODUCT = "LLM observability platform for AI startups — token cost tracking per customer, prompt versioning with rollback, and eval pipelines that catch regressions before they hit production."
ICP = "B2B AI product startups, Seed to Series A, with LLMs in production. They use multiple model providers (OpenAI + Anthropic), are scaling beyond toy projects, and don't yet have a managed observability solution."

# Hardcode the validated filters
FILTERS = {
    "topics": ["llm", "ai-agents", "rag", "llmops", "generative-ai", "langchain"],
    "min_stars": 100,
    "llm_sdks_to_match": ["openai", "@anthropic-ai/sdk", "anthropic", "langchain", "litellm"],
    "obs_vendors_to_exclude": ["helicone", "langfuse", "langsmith", "traceloop", "arize"],
    "rationale": "Targets AI startups with multi-provider LLM setups in production, excluding those already using observability vendors.",
}

# 1. Pin filters in cache so the UI returns these instantly when default prompts are used
cache_util.put("filters", {"product": PRODUCT, "icp": ICP}, FILTERS)
print("[1/4] filters pinned")

# 2. Run search to warm topic_search + commit history caches
qualified = github_search.search_and_qualify(
    FILTERS["topics"],
    min_stars=100,
    max_results=15,
    max_age_days=365,
    candidate_pool_size=200,
    with_evidence=True,
)
print(f"[2/4] {len(qualified)} qualified leads cached")

# 3. Pre-fetch contacts for all leads + warm contributors
for q in qualified:
    contacts = contact_lookup.find_contacts(q, max_contacts=5)
    real = [c for c in contacts if c["source"] == "verified"]
    contribs = github_search.fetch_top_contributors(q["full_name"], n=5)
    print(f"  {q['full_name']:40} | {len(real)} verified contacts | {len(contribs)} contributors")
print("[3/4] contacts + contributors cached")

print("[4/4] Demo cache warm. The UI will return these results instantly.")
