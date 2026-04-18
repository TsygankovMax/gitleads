import os
import json
from dotenv import load_dotenv
import cache_util

load_dotenv()

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

VALID_TOPICS = {
    "llm", "ai-agents", "rag", "llmops", "generative-ai", "vector-search",
    "agentic-ai", "prompt-engineering", "embeddings", "fine-tuning",
    "multimodal-ai", "langchain", "semantic-search", "openai",
}
VALID_SDKS = {
    "openai", "@anthropic-ai/sdk", "anthropic", "@google/generative-ai",
    "google-generativeai", "langchain", "@langchain/core", "llamaindex",
    "litellm", "cohere", "mistralai", "@ai-sdk/openai", "@ai-sdk/anthropic",
}
VALID_OBS = {
    "helicone", "langfuse", "langsmith", "traceloop", "arize",
    "openinference", "langwatch", "weights-and-biases", "wandb",
}

try:
    from openai import OpenAI
    CLIENT = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
except ImportError:
    CLIENT = None


def _llm_call(prompt: str, max_tokens: int = 600, json_mode: bool = False) -> str:
    kwargs = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = CLIENT.chat.completions.create(**kwargs)
    return resp.choices[0].message.content.strip()


def generate_filters(product: str, icp: str) -> dict:
    """LLM call: turns product+ICP into GitHub topic filters."""
    payload = {"product": product, "icp": icp}
    cached = cache_util.get("filters", payload)
    if cached is not None:
        return cached

    if not CLIENT:
        return _fallback_filters(product, icp)

    prompt = f"""You are a GitHub-signal analyst for B2B sales. Map a product+ICP to GitHub search filters.

Product: {product}
Ideal customer: {icp}

You MUST pick values ONLY from these whitelisted options (do NOT invent new ones — invented topics/strings will break the search):

VALID GITHUB TOPICS (pick 2-4 most relevant):
llm, ai-agents, rag, llmops, generative-ai, vector-search, agentic-ai, prompt-engineering, embeddings, fine-tuning, multimodal-ai, langchain, semantic-search, openai

VALID LLM SDK STRINGS to match in deps (pick the ones the ICP would realistically use):
openai, @anthropic-ai/sdk, anthropic, @google/generative-ai, google-generativeai, langchain, @langchain/core, llamaindex, litellm, cohere, mistralai, @ai-sdk/openai, @ai-sdk/anthropic

VALID OBSERVABILITY VENDOR STRINGS to exclude (these are competitors — exclude only the relevant ones):
helicone, langfuse, langsmith, traceloop, arize, openinference, langwatch, weights-and-biases, wandb

Return JSON with these exact keys:
- topics: list of EXACTLY 5-6 strings from VALID GITHUB TOPICS only — pick the most relevant 5-6, even if some are loose-fit. Wider topic coverage is critical for fresh-signal yield.
- min_stars: integer between 50-500
- llm_sdks_to_match: list of strings from VALID LLM SDK STRINGS only
- obs_vendors_to_exclude: list of strings from VALID OBSERVABILITY VENDOR STRINGS only
- rationale: 1-sentence explanation of why these filters fit the product/ICP

Output ONLY valid JSON, no markdown fences, no preamble."""

    try:
        text = _llm_call(prompt, max_tokens=600, json_mode=True)
        result = json.loads(text)
        # Defensive: strip any hallucinated values not in whitelists
        result["topics"] = [t for t in result.get("topics", []) if t in VALID_TOPICS] or ["llm", "ai-agents", "rag", "llmops"]
        result["llm_sdks_to_match"] = [s for s in result.get("llm_sdks_to_match", []) if s in VALID_SDKS] or ["openai", "anthropic", "langchain"]
        result["obs_vendors_to_exclude"] = [v for v in result.get("obs_vendors_to_exclude", []) if v in VALID_OBS] or ["helicone", "langfuse", "langsmith", "traceloop", "arize"]
        cache_util.put("filters", payload, result)
        return result
    except Exception as e:
        result = _fallback_filters(product, icp)
        result["error"] = str(e)
        return result


def _fallback_filters(product: str, icp: str) -> dict:
    """Used when no API key set — sane defaults for LLM-obs scenario."""
    return {
        "topics": ["llm", "ai-agents", "rag", "llmops"],
        "min_stars": 100,
        "llm_sdks_to_match": ["openai", "anthropic", "langchain", "litellm"],
        "obs_vendors_to_exclude": ["helicone", "langfuse", "langsmith", "traceloop", "arize"],
        "rationale": "Default LLM-observability ICP (no API key set).",
    }


def generate_message(company: dict, contact: dict, product: str) -> str:
    """LLM call: <300 char honest LinkedIn DM citing the GitHub signal."""
    payload = {"company": company["full_name"], "contact": contact["name"], "product_hash": hash(product)}
    cached = cache_util.get("message", payload)
    if cached is not None:
        return cached

    if not CLIENT:
        return _fallback_message(company, contact, product)

    # If no real contact found, generate generic message — don't pretend
    if contact.get("source") in ("mock", "no_match", "apollo_no_match", "apollo_error", "no_key") or not contact.get("first_name"):
        return _no_contact_message(company, product)

    sdks = ", ".join(company.get("llm_sdks", []))
    first_name = contact["first_name"]

    prompt = f"""Write a LinkedIn DM under 300 characters from a salesperson to {first_name}, who is {contact['title']} at {company['owner']}.

Their GitHub repo {company['full_name']} ({company['stars']} stars) uses these LLM SDKs: {sdks}. They have multi-provider production patterns.

The salesperson sells: {product}

Rules:
- Under 300 characters total
- Open with their first name
- Reference the SPECIFIC signal you saw in their repo (multi-provider LLM usage)
- State the problem this signal implies for them
- Offer the solution in one phrase
- End with a soft CTA like "worth a quick chat?"
- No fluff, no "I hope this finds you well"
- Honest tone, like an engineer talking to an engineer

Output ONLY the message text, no preamble or quotes."""

    try:
        text = _llm_call(prompt, max_tokens=200)
        if len(text) > 320:
            text = text[:297] + "..."
        cache_util.put("message", payload, text)
        return text
    except Exception:
        return _fallback_message(company, contact, product)


def _fallback_message(company: dict, contact: dict, product: str) -> str:
    sdks = " + ".join(company.get("llm_sdks", [])[:2]) or "OpenAI"
    first = contact.get("first_name") or (contact["name"].split()[0] if contact.get("name") else "Hi")
    if first.startswith("["):
        first = "Hi"
    return (
        f"{first} — saw {company['owner']} ships {sdks} multi-provider in prod. "
        f"At {company['stars']/1000:.0f}K stars you're past DIY observability stage. "
        f"We solve token attribution + cost tracking for exactly this. Worth 15 min?"
    )[:300]


def _no_contact_message(company: dict, product: str) -> str:
    """Used when no contact found — honest 'add manually' template, no fake personalisation."""
    sdks = " + ".join(company.get("llm_sdks", [])[:3]) or "OpenAI"
    return (
        f"⚠ No verified contact found for {company['owner']}. Manual outreach: search "
        f"'{company['owner']} CTO' or 'Head of Engineering' on LinkedIn. Signal hook: "
        f"they ship {sdks} multi-provider in {company.get('deps_file', 'deps')}."
    )[:300]
