#!/usr/bin/env python3
"""
DEPRECATED: Use the unified UI instead: streamlit run scripts/approve_citations_ui.py
(merge review + new papers review + Apply in one app).

This script is kept for backwards compatibility. It reviews merge_map.json only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

BIB_DIR = PROJECT_ROOT / "bib"
MERGE_MAP_PATH = PROJECT_ROOT / "merge_map.json"


def load_bib():
    from scripts import bib_utils
    path = BIB_DIR / "citations.bib"
    if not path.exists():
        path = BIB_DIR / "current_citations.bib"
    if not path.exists():
        return None
    return bib_utils.load_bib(path)


def entry_summary(entry: dict) -> str:
    title = (entry.get("title") or "—")[:80]
    author = (entry.get("author") or "—")
    if " and " in author:
        author = author.split(" and ")[0] + " et al."
    author = author[:50]
    year = entry.get("year") or "—"
    etype = entry.get("ENTRYTYPE") or "misc"
    return f"**{title}**  \n{author}  \n{year} · {etype}"


def main():
    st.set_page_config(page_title="Merge map review", layout="wide")
    st.title("Merge map review")
    st.caption("Review suggested duplicate pairs. Choose **Merge** (drop right into left) or **Don't merge**. Save when done.")

    entries = load_bib()
    if entries is None:
        st.error("No bib file found. Run `python scripts/1_merge_bib.py` first to generate bib/citations.bib and merge_map.json.")
        return

    if not MERGE_MAP_PATH.exists():
        st.warning("No merge_map.json found. Run `python scripts/1_merge_bib.py` first to generate it.")
        return

    with MERGE_MAP_PATH.open(encoding="utf-8") as f:
        merge_map = json.load(f)

    merge_into = merge_map.get("merge_into") or {}
    if not merge_into:
        st.info("No suggested pairs in merge_map.json. You're done!")
        return

    if "decisions" not in st.session_state:
        st.session_state.decisions = {f"{k_drop}|{k_keep}": None for k_drop, k_keep in merge_into.items()}

    pairs = list(merge_into.items())
    decision_key = lambda k_drop, k_keep: f"{k_drop}|{k_keep}"
    n_merge = sum(1 for k in st.session_state.decisions if st.session_state.decisions[k] is True)
    n_skip = sum(1 for k in st.session_state.decisions if st.session_state.decisions[k] is False)
    n_left = sum(1 for k in st.session_state.decisions if st.session_state.decisions[k] is None)
    st.sidebar.metric("Merge", n_merge)
    st.sidebar.metric("Don't merge", n_skip)
    st.sidebar.metric("Pending", n_left)

    for key_drop, key_keep in pairs:
        e_keep = entries.get(key_keep, {})
        e_drop = entries.get(key_drop, {})
        dk = decision_key(key_drop, key_keep)
        decision = st.session_state.decisions.get(dk)
        label = "✅ Merge" if decision is True else ("⏭️ Don't merge" if decision is False else "—")

        with st.container():
            col1, col2, col3 = st.columns([1, 1, 0.35])
            with col1:
                st.markdown(f"#### KEEP · `{key_keep}`")
                st.markdown(entry_summary(e_keep))
            with col2:
                st.markdown(f"#### DROP → merge into left · `{key_drop}`")
                st.markdown(entry_summary(e_drop))
            with col3:
                st.markdown("**Decision**")
                if st.button("Merge", key=f"merge_{key_drop}", help="Drop right entry into left"):
                    st.session_state.decisions[dk] = True
                    st.rerun()
                if st.button("Don't merge", key=f"skip_{key_drop}", help="Keep both entries"):
                    st.session_state.decisions[dk] = False
                    st.rerun()
                st.caption(label)
        st.divider()

    st.sidebar.divider()
    if st.sidebar.button("Save merge map (approved only)"):
        approved_merge_into = {}
        for key_str, v in st.session_state.decisions.items():
            if v is True and "|" in key_str:
                k_drop, k_keep = key_str.split("|", 1)
                approved_merge_into[k_drop] = k_keep
        out = {
            "description": "Approved merges only. Apply with: python scripts/1_merge_bib.py --apply-merge-map merge_map.json",
            "drop": list(approved_merge_into.keys()),
            "merge_into": approved_merge_into,
        }
        with MERGE_MAP_PATH.open("w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        st.sidebar.success(f"Saved {len(approved_merge_into)} approved merges to merge_map.json")
        st.sidebar.caption("Re-run script 1 with --apply-merge-map to apply.")


if __name__ == "__main__":
    main()
