# GitLeads MVP

Turn GitHub activity into ready-to-outreach B2B leads. Scans public AI startup repos for production LLM signals (multi-provider SDKs, recent dependency adds, observability gaps), enriches with verified leadership contacts, and generates pre-written outreach messages.

## Setup

```bash
cd app
pip install -r requirements.txt
```

Configure `.env` (copy from `.env.example`):
- `OPENAI_API_KEY` — for ICP-to-filter translation and DM generation
- `CONTACT_PROVIDER_KEY` — for verified contact enrichment (optional; mock data is used otherwise)
- `CONTACT_LIVE=true` — flip to enable live contact lookups; defaults to `false` (mock)
- GitHub auth via `gh auth login` (REST + Code Search)

## Run

```bash
streamlit run streamlit_app.py
```

App opens at http://localhost:8501

## Demo flow

1. Enter what you sell + your ICP (free text)
2. Engine generates GitHub topic + SDK + observability filters
3. Run search → returns qualified leads (HOT/WARM) with fresh signal dates
4. Each card shows: signal evidence (file+line+commit), leadership contacts, top GitHub contributors, pre-filled DM
5. Subscribe to filter for weekly digest of new matches

## Files

- `streamlit_app.py` — UI
- `github_search.py` — topic search, signal check, contributors
- `signal_evidence.py` — commit-history binary search for signal dates + code evidence
- `contact_lookup.py` — verified contact enrichment adapter
- `message_gen.py` — LLM-driven filter generation + DM personalization
- `cache_util.py` — file-based response cache
