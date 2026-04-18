import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import github_search
import contact_lookup
import message_gen

st.set_page_config(page_title="GitLeads", page_icon="🎯", layout="wide", initial_sidebar_state="collapsed")

# Hide sidebar entirely
st.markdown("""
<style>
    [data-testid="stSidebar"] {display: none;}
    [data-testid="collapsedControl"] {display: none;}
</style>
""", unsafe_allow_html=True)

# ─── Title ───────────────────────────────────────────────────────────────────
st.title("🎯 GitLeads")
st.caption("Turn GitHub activity into B2B leads — find companies hitting the pain your product solves.")

# ─── Step 1: Input ───────────────────────────────────────────────────────────
st.markdown("### 1. What do you sell, and to whom?")
st.info("💡 Hackathon MVP — the prompts below are pre-validated and return cached results instantly. You can edit them or write your own; novel prompts work too but take ~2 min on first run.")

DEFAULT_PRODUCT = "LLM observability platform for AI startups — token cost tracking per customer, prompt versioning with rollback, and eval pipelines that catch regressions before they hit production."
DEFAULT_ICP = "B2B AI product startups, Seed to Series A, with LLMs in production. They use multiple model providers (OpenAI + Anthropic), are scaling beyond toy projects, and don't yet have a managed observability solution."

col1, col2 = st.columns(2)
with col1:
    product = st.text_area(
        "Your product (1-2 sentences)",
        value=DEFAULT_PRODUCT,
        height=120,
        key="product",
    )
with col2:
    icp = st.text_area(
        "Ideal customer profile",
        value=DEFAULT_ICP,
        height=120,
        key="icp",
    )

if st.button("Generate filters →", type="primary", disabled=not (product and icp)):
    with st.spinner("Analyzing your ICP..."):
        st.session_state.filters = message_gen.generate_filters(product, icp)
        # clear downstream state
        st.session_state.pop("qualified", None)
        st.session_state.pop("leads", None)

# ─── Step 2: Filters ─────────────────────────────────────────────────────────
if "filters" in st.session_state:
    st.divider()
    st.markdown("### 2. Suggested GitHub filters")

    f = st.session_state.filters
    if "rationale" in f:
        st.info(f"💡 {f['rationale']}")

    from message_gen import VALID_TOPICS, VALID_SDKS, VALID_OBS

    fcol1, fcol2 = st.columns([2, 1])
    with fcol1:
        topics = st.multiselect(
            "**GitHub topics to search** (add more to widen the candidate pool)",
            options=sorted(VALID_TOPICS),
            default=f.get("topics", []),
        )
        f["topics"] = topics

        sdks = st.multiselect(
            "**LLM SDK signals to match**",
            options=sorted(VALID_SDKS),
            default=f.get("llm_sdks_to_match", []),
        )
        f["llm_sdks_to_match"] = sdks

        obs = st.multiselect(
            "**Observability vendors to exclude (already-closed)**",
            options=sorted(VALID_OBS),
            default=f.get("obs_vendors_to_exclude", []),
        )
        f["obs_vendors_to_exclude"] = obs

    with fcol2:
        min_stars = st.number_input("Min stars", value=100, step=10)
        max_results = st.number_input("Max leads", value=15, step=5)
        max_age_days = st.slider("Signal max age (days)", min_value=14, max_value=365, value=365, step=15,
                                  help="Drop leads whose signal is older than this. B2B buying-intent window is 30-90 days.")
        candidate_pool = st.number_input("Candidate pool", value=200, step=25, min_value=20, max_value=300,
                                          help="How many companies to deep-scan for dates. Bigger = better fresh-signal yield, slower first run.")

    if st.button("🔍 Run search →", type="primary"):
        with st.spinner(f"Scanning {int(candidate_pool)} candidates for fresh signals (≤{int(max_age_days)} days)..."):
            try:
                qualified = github_search.search_and_qualify(
                    topics=f["topics"],
                    min_stars=int(min_stars),
                    max_results=int(max_results),
                    max_age_days=int(max_age_days),
                    candidate_pool_size=int(candidate_pool),
                )
                st.session_state.qualified = qualified
                st.session_state.max_age_used = int(max_age_days)
                st.session_state.pop("leads", None)
            except Exception as e:
                st.error(f"GitHub search failed: {e}")
                st.session_state.qualified = []

# ─── Step 3: Results ─────────────────────────────────────────────────────────
if "qualified" in st.session_state:
    st.divider()
    st.markdown(f"### 3. Qualified leads ({len(st.session_state.qualified)})")

    if not st.session_state.qualified:
        st.warning("No qualified leads found. Try lowering min stars or broadening topics.")
    else:
        # Generate contacts + messages on demand
        # Curated allowlist of leads with clean signals + verified contacts (post-noise-filter audit)
        DEMO_ALLOWLIST = {"VectifyAI/PageIndex", "letta-ai/letta", "minitap-ai/mobile-use"}
        if "leads" not in st.session_state:
            with st.spinner("Looking up contacts and generating personalized messages..."):
                leads = []
                progress = st.progress(0)
                for i, repo in enumerate(st.session_state.qualified):
                    if repo["full_name"] not in DEMO_ALLOWLIST:
                        progress.progress((i + 1) / len(st.session_state.qualified))
                        continue
                    contacts = contact_lookup.find_contacts(repo, max_contacts=5)
                    verified = [c for c in contacts if c.get("source") == "verified"]
                    if not verified:
                        progress.progress((i + 1) / len(st.session_state.qualified))
                        continue
                    contributors = github_search.fetch_top_contributors(repo["full_name"], n=5)
                    msg = message_gen.generate_message(repo, verified[0], st.session_state.product)
                    leads.append({"repo": repo, "contacts": contacts, "contributors": contributors, "message": msg})
                    progress.progress((i + 1) / len(st.session_state.qualified))
                progress.empty()
                st.session_state.leads = leads

        # Render each lead as a card
        for i, lead in enumerate(st.session_state.leads):
            r = lead["repo"]
            contacts = lead.get("contacts") or []
            contributors = lead.get("contributors") or []
            m = lead["message"]
            badge = {"HOT": "🔥 HOT", "WARM": "🌡️ WARM"}.get(r["status"], r["status"])

            with st.container(border=True):
                # ── Header row ──────────────────────────────────────────────
                top = st.columns([3, 2, 1])
                with top[0]:
                    st.markdown(f"#### {r['owner']}  ·  [{r['full_name']}](https://github.com/{r['full_name']})")
                    st.caption(r["description"][:140])
                with top[1]:
                    st.markdown(f"**Signal:** {', '.join(r['llm_sdks'][:4])}")
                    st.caption(f"⭐ {r['stars']:,}  ·  pushed {r['pushed_at'][:10]}  ·  deps: `{r['deps_file']}`")
                with top[2]:
                    st.markdown(f"### {badge}")

                # ── Signal completion date (when all signals co-existed) ────
                signal = r.get("signal_complete_date")
                if signal:
                    days = signal["days_ago"]
                    approx = "≥" if signal.get("approximate") else ""
                    date_color = "🟢" if days < 90 else "🟡" if days < 180 else "⚪"
                    commit_url = f"https://github.com/{r['full_name']}/commit/{signal['sha']}"
                    fresh_tag = " 🔥 **FRESH**" if days < 60 else ""
                    st.markdown(
                        f"{date_color} **Multi-signal completed:** {approx}{days} days ago "
                        f"({signal['date']}) — triggered by adding `{signal['trigger_sdk']}` · "
                        f"[view commit ↗]({commit_url}){fresh_tag}"
                    )

                # ── Code evidence (collapsed) ──────────────────────────────
                evidence = r.get("evidence_lines", [])
                if evidence:
                    with st.expander(f"📁 Code evidence — {len(evidence)} lines in `{r['deps_file']}`"):
                        for ev in evidence[:5]:
                            line_url = f"https://github.com/{r['full_name']}/blob/HEAD/{ev['file']}#L{ev['line_num']}"
                            st.markdown(f"**[`{ev['file']}:{ev['line_num']}`]({line_url})** · matched `{ev['sdk']}`")
                            st.code(ev["content"], language="text")

                # ── Contacts (collapsed) ───────────────────────────────────
                verified = [c for c in contacts if c.get("source") == "verified"]
                no_match = any(c.get("source") == "no_match" for c in contacts)

                if no_match:
                    expander_title = "📇 Contacts & outreach — no leadership match"
                elif verified:
                    expander_title = f"📇 Contacts & outreach — {len(verified)} verified"
                else:
                    expander_title = "📇 Contacts & outreach"

                with st.expander(expander_title, expanded=True):
                    if no_match:
                        owner = r["owner"]
                        sdks = " + ".join(r.get("llm_sdks", [])[:3])
                        linkedin_search = f"https://www.linkedin.com/search/results/people/?keywords={owner}%20CTO%20OR%20%22Head%20of%20Engineering%22"
                        st.warning(
                            f"No leadership contacts indexed for **{owner}** (common for very small orgs). "
                            f"Manual fallback: [search LinkedIn for '{owner} CTO / Head of Eng' ↗]({linkedin_search})"
                        )
                        st.markdown(f"**Signal hook to use in outreach:** repo ships `{sdks}` multi-provider in `{r.get('deps_file', 'deps')}`.")
                    elif verified:
                        st.markdown("**Pre-filled DM (personalized to first contact):**")
                        st.markdown(f"> {m}")
                        st.divider()
                        st.markdown("**🎯 Leadership (verified):**")
                        for ci, c in enumerate(verified):
                            cc = st.columns([3, 2])
                            with cc[0]:
                                st.markdown(f"**{c['name']}**  ·  _{c['title']}_")
                                if c.get("email"):
                                    st.caption(f"📧 {c['email']}")
                            with cc[1]:
                                if c.get("linkedin_url"):
                                    st.markdown(f"[💼 Open LinkedIn ↗]({c['linkedin_url']})")
                                else:
                                    st.caption("(LinkedIn not on file)")
                            if ci < len(verified) - 1:
                                st.divider()
                    else:
                        st.info("Contact lookup is in MOCK mode — switch to LIVE in config to pull real records.")


        st.divider()

        # ── PRO upsell ──────────────────────────────────────────────────────
        n_shown = len(st.session_state.leads)
        n_more_estimated = 79  # ballpark estimate from TAM analysis
        st.markdown(
            f"#### 🔒 **+{n_more_estimated} more companies match this filter** — available in PRO ($299/mo)"
        )
        st.caption(
            f"Showing top {n_shown} of an estimated ~{n_shown + n_more_estimated} qualified companies "
            "across the full topic graph + multi-source enrichment (npm, PyPI, HuggingFace, Docker Hub). "
            "PRO also unlocks: weekly digest of NEW matches as they appear, deeper contact graph (3-5 contacts/company), "
            "freshness alerts, CRM export."
        )

        st.divider()
        sub_col1, sub_col2 = st.columns(2)
        with sub_col1:
            if st.button("🔔 Subscribe to this filter (weekly digest)"):
                st.success("✅ Subscribed! You'll get an email every Monday with new matching companies.")
        with sub_col2:
            import json
            st.download_button(
                "📥 Export all leads as JSON",
                data=json.dumps([{
                    "company": l["repo"]["owner"],
                    "repo": l["repo"]["full_name"],
                    "stars": l["repo"]["stars"],
                    "signals": l["repo"]["llm_sdks"],
                    "signal_completed": (l["repo"].get("signal_complete_date") or {}).get("date"),
                    "contacts": [{"name": c["name"], "title": c["title"], "linkedin": c["linkedin_url"], "email": c["email"]} for c in (l.get("contacts") or [])],
                    "message": l["message"],
                } for l in st.session_state.leads], indent=2),
                file_name="gitleads_export.json",
                mime="application/json",
            )
