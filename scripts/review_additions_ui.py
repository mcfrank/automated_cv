#!/usr/bin/env python3
"""
DEPRECATED: Use the unified UI instead: streamlit run scripts/approve_citations_ui.py
(merge review + new papers review + Apply in one app). Proposed list now comes from
step 1: logs/1_gather_candidates_proposed_*.json or step 2: logs/2_enriched_proposed_*.json.

This script is kept for backwards compatibility. It reviews possible missing papers
and appends accepted entries to current_citations.bib.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
BIB_DIR = PROJECT_ROOT / "bib"
CURRENT_BIB = BIB_DIR / "current_citations.bib"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st


def list_possible_missing_files() -> list[Path]:
    """Return sorted paths of 2_validate_citations_possible_missing_*.json (newest first)."""
    if not LOGS_DIR.is_dir():
        return []
    paths = list(LOGS_DIR.glob("2_validate_citations_possible_missing_*.json"))
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths


def load_existing_bib_keys() -> set[str]:
    """Return set of cite keys from current_citations.bib (or citations.bib)."""
    from scripts import bib_utils
    for path in (CURRENT_BIB, BIB_DIR / "citations.bib"):
        if path.exists():
            try:
                return set(bib_utils.load_bib(path).keys())
            except Exception:
                pass
    return set()


def title_to_slug(title: str, max_words: int = 4) -> str:
    """Lowercase alphanumeric slug from title (first max_words words)."""
    if not title:
        return "unknown"
    words = re.sub(r"[^\w\s]", " ", title).split()[:max_words]
    return "_".join(w.lower() for w in words if w).replace(" ", "_") or "unknown"


def make_citekey(title: str, year: str, existing: set[str]) -> str:
    """Generate a unique cite key from title and year."""
    base = title_to_slug(title) + "_" + (year or "nodate")
    base = re.sub(r"[^\w]", "_", base)[:50]
    key = base
    n = 1
    while key in existing:
        key = f"{base}_{n}"
        n += 1
    return key


def item_to_bibtex(p: dict, citekey: str | None = None, existing_keys: set[str] | None = None) -> str:
    """Generate a minimal BibTeX entry for a possible-missing item."""
    existing = existing_keys or set()
    if citekey is None:
        citekey = make_citekey(p.get("title") or "", p.get("year") or "", existing)
    title = (p.get("title") or "").replace("{", "{{").replace("}", "}}")
    year = (p.get("year") or "").strip()
    doi = p.get("doi")
    arxiv_id = p.get("arxiv_id")
    author = (p.get("author") or "Frank, Michael C.").replace("{", "{{").replace("}", "}}")
    lines = [
        f"@article{{{citekey},",
        f"  author = {{{author}}},",
        f"  title = {{{title}}},",
        f"  year = {{{year}}},",
    ]
    journal = p.get("journal")
    if journal:
        lines.append(f"  journal = {{{journal.replace('{', '{{').replace('}', '}}')}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
    if arxiv_id:
        lines.append(f"  note = {{arXiv:{arxiv_id}}},")
    lines.append("}")
    return "\n".join(lines)


def append_to_bib(bib_path: Path, entry_text: str) -> None:
    """Append a BibTeX entry to the file (with newline if needed)."""
    bib_path.parent.mkdir(parents=True, exist_ok=True)
    if bib_path.exists():
        content = bib_path.read_text(encoding="utf-8").rstrip()
        if content and not content.endswith("\n"):
            content += "\n"
        content += "\n" + entry_text.strip() + "\n"
    else:
        content = entry_text.strip() + "\n"
    bib_path.write_text(content, encoding="utf-8")


def item_id(p: dict, index: int) -> str:
    """Stable id for session state (title + doi + year + index)."""
    return f"{index}|{(p.get('title') or '')[:80]}|{p.get('doi') or ''}|{p.get('year') or ''}"


def main():
    st.set_page_config(page_title="Review additions", layout="wide")
    st.title("Review possible missing papers")
    st.caption("From step 2 (validate citations). Accept adds to bib, Reject skips, Edit lets you fix the BibTeX before adding.")

    json_files = list_possible_missing_files()
    if not json_files:
        st.error("No possible-missing list found. Run `python3 scripts/2_validate_citations.py` first (without --no-discovery).")
        st.info("Expected file: logs/2_validate_citations_possible_missing_YYYY-MM-DD.json")
        return

    selected = st.sidebar.selectbox(
        "Report date",
        options=range(len(json_files)),
        format_func=lambda i: json_files[i].name.replace("2_validate_citations_possible_missing_", "").replace(".json", ""),
        index=0,
    )
    path = json_files[selected]
    try:
        possible_missing = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        st.error(f"Could not load {path.name}: {e}")
        return

    if not possible_missing:
        st.info("No possible missing papers in this report.")
        return

    existing_keys = load_existing_bib_keys()
    if "rejected" not in st.session_state:
        st.session_state.rejected = set()
    if "accepted" not in st.session_state:
        st.session_state.accepted = set()
    if "editing" not in st.session_state:
        st.session_state.editing = None  # index being edited

    pending = [i for i in range(len(possible_missing)) if item_id(possible_missing[i], i) not in st.session_state.rejected and item_id(possible_missing[i], i) not in st.session_state.accepted]
    n_accepted = len(st.session_state.accepted)
    n_rejected = len(st.session_state.rejected)
    st.sidebar.metric("Accepted (added to bib)", n_accepted)
    st.sidebar.metric("Rejected", n_rejected)
    st.sidebar.metric("Pending", len(pending))

    if not pending and (n_accepted or n_rejected):
        st.success("All items reviewed. Accepted entries were appended to " + str(CURRENT_BIB.name) + ".")
        if st.sidebar.button("Clear session (review again)"):
            st.session_state.rejected = set()
            st.session_state.accepted = set()
            st.session_state.editing = None
            st.rerun()
        return

    for idx in pending:
        p = possible_missing[idx]
        iid = item_id(p, idx)
        source = p.get("source") or ""
        title = (p.get("title") or "")[:200]
        doi = p.get("doi")
        year = p.get("year") or ""
        arxiv_id = p.get("arxiv_id")

        if st.session_state.editing == idx:
            citekey = make_citekey(p.get("title") or "", year, existing_keys)
            default_bib = item_to_bibtex(p, citekey=citekey, existing_keys=existing_keys)
            edited = st.text_area("Edit BibTeX (then Add or Cancel)", value=default_bib, height=200, key=f"edit_{idx}")
            col1, col2, _ = st.columns([1, 1, 2])
            with col1:
                if st.button("Add edited entry", key=f"add_edit_{idx}"):
                    try:
                        append_to_bib(CURRENT_BIB, edited)
                        m = re.search(r"@\w+\s*\{\s*([^,\s}]+)", edited)
                        if m:
                            existing_keys.add(m.group(1))
                        st.session_state.accepted.add(iid)
                        st.session_state.editing = None
                        st.success(f"Appended to {CURRENT_BIB.name}")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
            with col2:
                if st.button("Cancel", key=f"cancel_edit_{idx}"):
                    st.session_state.editing = None
                    st.rerun()
            st.divider()
            continue

        with st.container():
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**{title}**")
                st.caption(f"[{source}]  " + (f"DOI: {doi}  " if doi else "") + (f"Year: {year}  " if year else "") + (f"arXiv: {arxiv_id}" if arxiv_id else ""))
            with col2:
                if st.button("Accept", key=f"acc_{idx}", help="Add to bib with generated BibTeX"):
                    try:
                        citekey = make_citekey(p.get("title") or "", year, existing_keys)
                        entry = item_to_bibtex(p, citekey=citekey, existing_keys=existing_keys)
                        append_to_bib(CURRENT_BIB, entry)
                        existing_keys.add(citekey)
                        st.session_state.accepted.add(iid)
                        st.success(f"Added {citekey}")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
                if st.button("Reject", key=f"rej_{idx}", help="Skip this item"):
                    st.session_state.rejected.add(iid)
                    st.rerun()
                if st.button("Edit", key=f"edit_btn_{idx}", help="Edit BibTeX before adding"):
                    st.session_state.editing = idx
                    st.rerun()
            st.divider()

    if pending and st.sidebar.button("Clear session (reset Accept/Reject)"):
        st.session_state.rejected = set()
        st.session_state.accepted = set()
        st.session_state.editing = None
        st.rerun()


if __name__ == "__main__":
    main()
