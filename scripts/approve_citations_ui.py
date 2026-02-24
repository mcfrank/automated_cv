#!/usr/bin/env python3
"""
Unified Streamlit UI: review proposed new papers (citations) first, then merge dupes (merge_map.json);
save approved additions and merge map; apply to write citations.bib.
Run after step 1 and step 2. Usage: streamlit run scripts/approve_citations_ui.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIB_DIR = PROJECT_ROOT / "bib"
LOGS_DIR = PROJECT_ROOT / "logs"
MERGE_MAP_PATH = PROJECT_ROOT / "merge_map.json"
APPROVED_ADDITIONS_PATH = PROJECT_ROOT / "approved_additions.json"
CURRENT_BIB = BIB_DIR / "current_citations.bib"
CITATIONS_BIB = BIB_DIR / "citations.bib"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st


def load_bib(path: Path | None = None):
    from scripts import bib_utils
    p = path or CITATIONS_BIB
    if not p.exists():
        p = CURRENT_BIB
    if not p.exists():
        return None
    return bib_utils.load_bib(p)


def entry_summary(entry: dict) -> str:
    title = (entry.get("title") or "—")[:80]
    author = (entry.get("author") or "—")
    if " and " in author:
        author = author.split(" and ")[0] + " et al."
    author = author[:50]
    year = entry.get("year") or "—"
    etype = entry.get("ENTRYTYPE") or "misc"
    return f"**{title}**  \n{author}  \n{year} · {etype}"


def list_proposed_files() -> list[Path]:
    """Newest first: 2_enriched_proposed_*.json then 1_gather_candidates_proposed_*.json."""
    if not LOGS_DIR.is_dir():
        return []
    paths = (
        list(LOGS_DIR.glob("2_enriched_proposed_*.json")) +
        list(LOGS_DIR.glob("1_gather_candidates_proposed_*.json"))
    )
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths


def load_existing_bib_keys() -> set[str]:
    from scripts import bib_utils
    for path in (CURRENT_BIB, CITATIONS_BIB):
        if path.exists():
            try:
                return set(bib_utils.load_bib(path).keys())
            except Exception:
                pass
    return set()


def title_to_slug(title: str, max_words: int = 4) -> str:
    if not title:
        return "unknown"
    words = re.sub(r"[^\w\s]", " ", title).split()[:max_words]
    return "_".join(w.lower() for w in words if w).replace(" ", "_") or "unknown"


def make_citekey(title: str, year: str, existing: set[str]) -> str:
    base = title_to_slug(title) + "_" + (year or "nodate")
    base = re.sub(r"[^\w]", "_", base)[:50]
    key = base
    n = 1
    while key in existing:
        key = f"{base}_{n}"
        n += 1
    return key


def item_to_bibtex(p: dict, citekey: str | None = None, existing_keys: set[str] | None = None) -> str:
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


def proposed_item_to_entry(p: dict, citekey: str) -> dict:
    """Build a bib entry dict from a proposed item (ORCID/arXiv) for approved_additions."""
    title = (p.get("title") or "").strip()
    year = (p.get("year") or "").strip()
    author = (p.get("author") or "Frank, Michael C.").strip()
    journal = (p.get("journal") or "").strip()
    doi = p.get("doi")
    arxiv_id = p.get("arxiv_id")
    entry = {
        "ENTRYTYPE": "article",
        "ID": citekey,
        "title": title,
        "year": year,
        "author": author,
    }
    if journal:
        entry["journal"] = journal
    if doi:
        entry["doi"] = doi
    if arxiv_id:
        entry["note"] = f"arXiv:{arxiv_id}"
    return entry


def item_id(p: dict, index: int) -> str:
    return f"{index}|{(p.get('title') or '')[:80]}|{p.get('doi') or ''}|{p.get('year') or ''}"


def render_merge_tab():
    entries = load_bib()
    if entries is None:
        st.error("No bib file found. Run step 1 (gather) then step 2 (enrich) first.")
        return
    if not MERGE_MAP_PATH.exists():
        st.warning("No merge_map.json. Run `python scripts/1_gather_candidates.py` first.")
        return
    with MERGE_MAP_PATH.open(encoding="utf-8") as f:
        merge_map = json.load(f)
    merge_into = merge_map.get("merge_into") or {}
    if not merge_into:
        st.info("No suggested pairs in merge_map.json.")
        return
    if "decisions" not in st.session_state:
        st.session_state.decisions = {f"{k_drop}|{k_keep}": None for k_drop, k_keep in merge_into.items()}
    pairs = list(merge_into.items())
    n_merge = sum(1 for k in st.session_state.decisions if st.session_state.decisions[k] is True)
    n_skip = sum(1 for k in st.session_state.decisions if st.session_state.decisions[k] is False)
    n_left = sum(1 for k in st.session_state.decisions if st.session_state.decisions[k] is None)
    st.metric("Merge", n_merge)
    st.metric("Don't merge", n_skip)
    st.metric("Pending", n_left)
    for key_drop, key_keep in pairs:
        e_keep = entries.get(key_keep, {})
        e_drop = entries.get(key_drop, {})
        dk = f"{key_drop}|{key_keep}"
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
                if st.button("Merge", key=f"merge_{key_drop}"):
                    st.session_state.decisions[dk] = True
                    st.rerun()
                if st.button("Don't merge", key=f"skip_{key_drop}"):
                    st.session_state.decisions[dk] = False
                    st.rerun()
                st.caption(label)
        st.divider()
    st.divider()
    if st.button("Save merge map (approved only)"):
        approved_merge_into = {}
        for key_str, v in st.session_state.decisions.items():
            if v is True and "|" in key_str:
                k_drop, k_keep = key_str.split("|", 1)
                approved_merge_into[k_drop] = k_keep
        out = {
            "description": "Approved merges only. Apply via this UI or: python scripts/apply_approvals.py",
            "drop": list(approved_merge_into.keys()),
            "merge_into": approved_merge_into,
        }
        with MERGE_MAP_PATH.open("w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        st.success(f"Saved {len(approved_merge_into)} approved merges to merge_map.json")


def render_new_papers_tab():
    json_files = list_proposed_files()
    if not json_files:
        st.error("No proposed list found. Run step 1 (1_gather_candidates.py) then optionally step 2.")
        st.info("Expected: logs/2_enriched_proposed_*.json or logs/1_gather_candidates_proposed_*.json")
        return
    selected = st.selectbox(
        "Proposed list (date)",
        options=range(len(json_files)),
        format_func=lambda i: json_files[i].name.replace("2_enriched_proposed_", "").replace("1_gather_candidates_proposed_", "").replace(".json", ""),
        index=0,
    )
    path = json_files[selected]
    try:
        possible_missing = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        st.error(f"Could not load {path.name}: {e}")
        return
    if not possible_missing:
        st.info("No proposed papers in this list.")
        return
    existing_keys = load_existing_bib_keys()
    if "approved_list" not in st.session_state:
        st.session_state.approved_list = []
    if "accepted_ids" not in st.session_state:
        st.session_state.accepted_ids = set()
    if "rejected" not in st.session_state:
        st.session_state.rejected = set()
    if "editing" not in st.session_state:
        st.session_state.editing = None

    pending = [
        i for i in range(len(possible_missing))
        if item_id(possible_missing[i], i) not in st.session_state.rejected
        and item_id(possible_missing[i], i) not in st.session_state.accepted_ids
    ]
    n_approved = len(st.session_state.approved_list or [])
    n_rejected = len(st.session_state.rejected)
    st.metric("Accepted (in list)", n_approved)
    st.metric("Rejected", n_rejected)
    st.metric("Pending", len(pending))

    def mark_rejected(iid):
        st.session_state.rejected.add(iid)
        st.rerun()

    def add_accepted(p, citekey, entry_dict, iid: str):
        st.session_state.approved_list = st.session_state.approved_list or []
        st.session_state.approved_list.append({"citekey": citekey, "entry": entry_dict, "bib_entry": entry_dict})
        st.session_state.accepted_ids = st.session_state.accepted_ids or set()
        st.session_state.accepted_ids.add(iid)
        st.rerun()

    for idx in pending:
        p = possible_missing[idx]
        iid = item_id(p, idx)
        source = p.get("source") or ""
        title = (p.get("title") or "")[:200]
        doi = p.get("doi")
        year = p.get("year") or ""
        arxiv_id = p.get("arxiv_id")

        if st.session_state.editing == idx:
            citekey = p.get("citekey") or make_citekey(p.get("title") or "", year, existing_keys)
            default_bib = item_to_bibtex(p, citekey=citekey, existing_keys=existing_keys)
            edited = st.text_area("Edit BibTeX (then Add or Cancel)", value=default_bib, height=200, key=f"edit_{idx}")
            col1, col2, _ = st.columns([1, 1, 2])
            with col1:
                if st.button("Add edited entry", key=f"add_edit_{idx}"):
                    try:
                        m = re.search(r"@\w+\s*\{\s*([^,\s}]+)", edited)
                        ck = m.group(1) if m else citekey
                        from bibtexparser import bparser
                        import bibtexparser
                        parser = bparser.BibTexParser(common_strings=True)
                        db = bibtexparser.loads(edited, parser=parser)
                        if db.entries:
                            ent = db.entries[0]
                            entry_dict = {"ENTRYTYPE": ent.get("ENTRYTYPE", "article"), "ID": ck, **{k: v for k, v in ent.items() if k not in ("ID", "ENTRYTYPE")}}
                            add_accepted(p, ck, entry_dict, iid)
                            st.success("Added to approved list")
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
                if st.button("Accept", key=f"acc_{idx}"):
                    citekey = p.get("citekey") or make_citekey(p.get("title") or "", year, existing_keys)
                    if p.get("bib_entry"):
                        entry_dict = dict(p["bib_entry"])
                        entry_dict["ID"] = citekey
                        if "ENTRYTYPE" not in entry_dict:
                            entry_dict["ENTRYTYPE"] = "inproceedings"
                        add_accepted(p, citekey, entry_dict, iid)
                    else:
                        entry_dict = proposed_item_to_entry(p, citekey)
                        add_accepted(p, citekey, entry_dict, iid)
                if st.button("Reject", key=f"rej_{idx}"):
                    mark_rejected(iid)
                if st.button("Edit", key=f"edit_btn_{idx}"):
                    st.session_state.editing = idx
                    st.rerun()
        st.divider()

    if st.button("Save approved additions to approved_additions.json"):
        lst = st.session_state.get("approved_list") or []
        out = [{"citekey": x["citekey"], "entry": x.get("entry") or x.get("bib_entry")} for x in lst]
        APPROVED_ADDITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        APPROVED_ADDITIONS_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
        st.success(f"Saved {len(out)} approved additions to approved_additions.json")


def main():
    st.set_page_config(page_title="Approve citations", layout="wide")
    st.title("Merge & approve citations")
    st.caption("Review proposed new papers first, then merge dupes. Save approved additions and merge map, then Apply to write citations.bib.")

    tab1, tab2, tab3 = st.tabs(["New papers", "Merge dupes", "Apply"])
    with tab1:
        render_new_papers_tab()
    with tab2:
        render_merge_tab()
    with tab3:
        st.subheader("Apply and write citations.bib")
        st.caption("Uses merge_map.json and approved_additions.json to build bib/citations.bib from current_citations.bib + website_citations.bib.")
        current_path = BIB_DIR / "current_citations.bib"
        website_path = BIB_DIR / "website_citations.bib"
        if not current_path.exists() or not website_path.exists():
            st.error("Missing current_citations.bib or website_citations.bib.")
        else:
            if st.button("Apply and write citations.bib"):
                try:
                    from scripts.apply_approvals import run_apply
                    merged = run_apply(
                        merge_map_path=MERGE_MAP_PATH,
                        approved_path=APPROVED_ADDITIONS_PATH,
                        out_path=CITATIONS_BIB,
                        current_path=current_path,
                        website_path=website_path,
                        dry_run=False,
                    )
                    st.success(f"Wrote bib/citations.bib ({len(merged)} entries).")
                except Exception as e:
                    st.error(str(e))


if __name__ == "__main__":
    main()
