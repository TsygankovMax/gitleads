"""Contact enrichment adapter. Wraps a third-party people-data provider.
The vendor name is intentionally not surfaced in user-facing strings."""
import os
import requests
from dotenv import load_dotenv
import cache_util

load_dotenv()

# Support both new and legacy env var names
PROVIDER_KEY = os.getenv("CONTACT_PROVIDER_KEY") or os.getenv("APOLLO_API_KEY", "")
LIVE_MODE = (os.getenv("CONTACT_LIVE") or os.getenv("APOLLO_LIVE", "false")).lower() == "true"

PROVIDER_BASE = os.getenv("CONTACT_PROVIDER_BASE", "")
HEADERS = {
    "Cache-Control": "no-cache",
    "Content-Type": "application/json",
    "X-Api-Key": PROVIDER_KEY,
    "accept": "application/json",
}


def _mock_contact(company: dict, idx: int = 0) -> dict:
    owner = company.get("owner", "company")
    roles = ["CTO", "VP Engineering", "Founder", "Head of AI", "Senior Engineer"]
    role = roles[idx % len(roles)]
    return {
        "name": f"[MOCK] {owner} {role}",
        "first_name": "",
        "title": role,
        "linkedin_url": f"https://linkedin.com/in/mock-{owner.lower()}-{idx}",
        "email": "",
        "photo_url": "",
        "obfuscated": False,
        "source": "mock",
    }


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    u = url.replace("https://", "").replace("http://", "").replace("www.", "")
    return u.split("/")[0].strip()


def _search_people(domain: str, max_contacts: int = 5) -> list[dict]:
    """1 credit. Filters to engineering/technical departments only."""
    payload = {
        "q_organization_domains_list": [domain],
        "person_seniorities": ["c_suite", "founder", "vp", "head", "director"],
        "person_departments": ["engineering", "information_technology", "product_management", "data_science"],
        "per_page": 10,
        "page": 1,
    }
    cached = cache_util.get("contact_search", payload)
    if cached is not None:
        people = cached
    else:
        try:
            r = requests.post(f"{PROVIDER_BASE}/mixed_people/api_search", json=payload, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                return []
            people = r.json().get("people", [])
            cache_util.put("contact_search", payload, people)
        except Exception:
            return []

    return _filter_technical_roles(people)[:max_contacts]


NON_TECH_TITLE_KEYWORDS = [
    "gtm", "go to market", "go-to-market", "sales", "revenue", "rev ops",
    "marketing", "growth", "demand gen", "brand",
    "customer success", "customer experience", "support",
    "people ops",
    "strategy", "business development", "biz dev", "bd ",
    "finance", "controller", "accounting",
    "legal", "compliance", "counsel",
    "human resources", "people", "talent", "recruiting",
    "community", "dao", "ecosystem", "social",
    "research analyst", "investment", "venture",
    "head of solutions",
]


def _filter_technical_roles(people: list[dict]) -> list[dict]:
    out = []
    for p in people:
        title = (p.get("title", "") or "").lower()
        if any(kw in title for kw in NON_TECH_TITLE_KEYWORDS):
            continue
        out.append(p)
    return out


def _enrich_person(person_id: str) -> dict:
    """1 credit per call. Reveals full name, LinkedIn URL, email."""
    payload = {"id": person_id}
    cached = cache_util.get("contact_match", {"id": person_id})
    if cached is not None:
        return cached
    try:
        r = requests.post(f"{PROVIDER_BASE}/people/match", json=payload, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return {}
        person = r.json().get("person", {}) or {}
        return cache_util.put("contact_match", {"id": person_id}, person)
    except Exception:
        return {}


def find_contacts(company: dict, max_contacts: int = 5) -> list[dict]:
    """
    Find up to `max_contacts` decision-makers for a company.
    SAFETY: returns mock contacts if LIVE_MODE != true.
    """
    if not LIVE_MODE:
        return [_mock_contact(company, i) for i in range(max_contacts)]

    if not PROVIDER_KEY:
        return [{**_mock_contact(company, 0), "source": "no_key"}]

    domain = _extract_domain(company.get("homepage", "")) or f"{company['owner'].lower()}.com"
    candidates = _search_people(domain, max_contacts=max_contacts)

    if not candidates:
        return [{**_mock_contact(company, 0), "source": "no_match"}]

    def _score(p):
        title = (p.get("title", "") or "").lower()
        if "cto" in title or "chief technology" in title: return 100
        if "head of eng" in title or "vp eng" in title or "vp of eng" in title: return 90
        if "founder" in title and "co-founder" not in title: return 80
        if "ceo" in title: return 75
        if "co-founder" in title: return 70
        if "head of ai" in title or "head of ml" in title or "ai lead" in title: return 65
        if "vp" in title or "head of" in title: return 60
        if "director" in title: return 50
        return 30

    candidates = sorted(candidates, key=_score, reverse=True)[:max_contacts]

    enriched = []
    for p in candidates:
        full = _enrich_person(p["id"])
        first = full.get("first_name") or p.get("first_name", "") or ""
        last = full.get("last_name") or ""
        last_obf = p.get("last_name_obfuscated", "") or ""
        display_name = full.get("name") or (f"{first} {last}".strip() if last else f"{first} {last_obf}".strip())
        enriched.append({
            "name": display_name or "[unknown]",
            "first_name": first,
            "title": full.get("title") or p.get("title", ""),
            "linkedin_url": full.get("linkedin_url", "") or "",
            "email": full.get("email", "") or "",
            "photo_url": full.get("photo_url", "") or "",
            "obfuscated": not bool(last),
            "source": "verified",
        })
    return enriched


def find_contact(company: dict) -> dict:
    contacts = find_contacts(company, max_contacts=1)
    return contacts[0] if contacts else _mock_contact(company)
