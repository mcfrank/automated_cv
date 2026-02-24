#!/usr/bin/env python3
"""
Gather all candidate citations: merge current + website .bib, output merge_map.json,
discover proposed additions from ORCID (local XML), arXiv, and eScholarship (CogSci).
Enrich existing CogSci proceedings from eScholarship. No CrossRef in this step.
"""
from __future__ import annotations

import argparse
import difflib
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIB_DIR = PROJECT_ROOT / "bib"
LOGS_DIR = PROJECT_ROOT / "logs"

# ORCID: orcid_xml/works or orcid/works
ORCID_XML_WORKS_DIR = PROJECT_ROOT / "orcid_xml" / "works"
if not ORCID_XML_WORKS_DIR.exists():
    ORCID_XML_WORKS_DIR = PROJECT_ROOT / "orcid" / "works"
ORCID_WORK_NS = "http://www.orcid.org/ns/work"
ORCID_COMMON_NS = "http://www.orcid.org/ns/common"

# eScholarship CogSci
COGSCI_UNIT_ID = "cognitivesciencesociety"
COGSCI_NEW_ENTRY_AUTHOR = "Michael C. Frank"
COGSCI_ESCHOLARSHIP_AUTHOR_EMAIL = "mcfrank@stanford.edu"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def setup_logging(log_path: Path) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gather_candidates")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh.setFormatter(fmt)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


# --- Merge + fuzzy (from 1_merge_bib) ---
def fuzzy_close_matches(entries: dict[str, dict]) -> list[tuple[str, str, float]]:
    from scripts import bib_utils
    get_title = bib_utils.get_title
    get_first_author = bib_utils.get_first_author
    pairs = []
    keys = list(entries.keys())
    for i, ka in enumerate(keys):
        ea = entries[ka]
        ta = get_title(ea)
        fa = get_first_author(ea)
        if not ta:
            continue
        for kb in keys[i + 1:]:
            eb = entries[kb]
            tb = get_title(eb)
            fb = get_first_author(eb)
            if not tb:
                continue
            title_ratio = difflib.SequenceMatcher(None, ta, tb).ratio()
            author_match = 1.0 if fa and fb and fa == fb else 0.0
            if author_match and title_ratio >= 0.85:
                score = 0.5 * title_ratio + 0.5 * author_match
                pairs.append((ka, kb, score))
    return sorted(pairs, key=lambda x: -x[2])


# --- ORCID local (from 2_validate_citations) ---
def orcid_local_works(works_dir: Path, log: logging.Logger | None = None) -> list[dict]:
    """Parse ORCID work XML files; return list of {title, doi, year, author, journal}."""
    import xml.etree.ElementTree as ET
    logger = log or logging.getLogger(__name__)
    works = []
    if not works_dir.is_dir():
        return works
    for path in sorted(works_dir.glob("*.xml")):
        try:
            root = ET.parse(path).getroot()
            title_el = root.find(f".//{{{ORCID_COMMON_NS}}}title")
            title = (title_el.text or "").strip() if title_el is not None and title_el.text else ""
            if not title:
                title_el = root.find(f".//{{{ORCID_WORK_NS}}}title")
                if title_el is not None:
                    child = title_el.find(f"{{{ORCID_COMMON_NS}}}title")
                    if child is not None and child.text:
                        title = child.text.strip()
            if not title:
                continue
            year = ""
            pub = root.find(f".//{{{ORCID_COMMON_NS}}}publication-date")
            if pub is not None:
                y_el = pub.find(f"{{{ORCID_COMMON_NS}}}year")
                if y_el is not None and y_el.text:
                    year = y_el.text.strip()
            doi = None
            for ext in root.findall(f".//{{{ORCID_COMMON_NS}}}external-id"):
                type_el = ext.find(f"{{{ORCID_COMMON_NS}}}external-id-type")
                if type_el is not None and (type_el.text or "").strip().lower() == "doi":
                    val_el = ext.find(f"{{{ORCID_COMMON_NS}}}external-id-value")
                    if val_el is not None and val_el.text:
                        doi = val_el.text.strip()
                    break
            journal = ""
            j_el = root.find(f".//{{{ORCID_WORK_NS}}}journal-title")
            if j_el is not None and j_el.text:
                journal = (j_el.text or "").strip()
            authors = []
            for contrib in root.findall(f".//{{{ORCID_WORK_NS}}}contributor"):
                name_el = contrib.find(f"{{{ORCID_WORK_NS}}}credit-name")
                if name_el is not None and name_el.text:
                    authors.append((name_el.text or "").strip())
            author = " and ".join(a for a in authors if a) if authors else None
            works.append({"title": title, "doi": doi, "year": year, "author": author or None, "journal": journal or None})
        except ET.ParseError as e:
            logger.debug("ORCID XML parse error %s: %s", path.name, e)
        except Exception as e:
            logger.debug("ORCID XML error %s: %s", path.name, e)
    return works


# --- arXiv (from 2_validate_citations) ---
ATOM_NS = "http://www.w3.org/2005/Atom"


def arxiv_author_search(max_results: int = 50, log: logging.Logger | None = None) -> list[dict]:
    import xml.etree.ElementTree as ET
    import requests
    logger = log or logging.getLogger(__name__)
    q = "au:michael_c_frank"
    base_url = "http://export.arxiv.org/api/query"
    params = {
        "search_query": q,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    try:
        r = requests.get(base_url, params=params, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        entry_els = root.findall(f"{{{ATOM_NS}}}entry")
        logger.info("arXiv response: %d bytes, %d entries", len(r.content), len(entry_els))
        entries = []
        for entry in entry_els:
            title_el = entry.find(f"{{{ATOM_NS}}}title")
            title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""
            id_el = entry.find(f"{{{ATOM_NS}}}id")
            arxiv_id = id_el.text.strip() if id_el is not None and id_el.text else ""
            published = entry.find(f"{{{ATOM_NS}}}published")
            year = published.text[:4] if published is not None and published.text else ""
            authors = []
            for a in entry.findall(f"{{{ATOM_NS}}}author"):
                name_el = a.find(f"{{{ATOM_NS}}}name")
                if name_el is not None and name_el.text:
                    authors.append(name_el.text)
            entries.append({"title": title, "arxiv_id": arxiv_id, "year": year, "authors": authors})
        return entries
    except Exception as e:
        logger.warning("arXiv request/parse error: %s", e)
        return []


def title_similar(a: str, b: str) -> float:
    a = (a or "").lower().strip()
    b = (b or "").lower().strip()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# --- eScholarship (from 3_check_completeness) ---
def escholarship_item_by_doi(doi: str) -> dict | None:
    import requests
    if not doi or not doi.strip():
        return None
    doi_clean = doi.strip()
    if doi_clean.startswith("http"):
        doi_clean = doi_clean.split("doi.org/")[-1].strip()
    query = """
    query ItemByDoi($id: String!, $scheme: IDScheme) {
      item(id: $id, scheme: $scheme) {
        title permalink journal volume issue published
        authors { nodes { name } }
      }
    }
    """
    try:
        r = requests.post(
            "https://escholarship.org/graphql",
            headers={"Content-Type": "application/json"},
            json={"query": query, "variables": {"id": doi_clean, "scheme": "DOI"}},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            return None
        return (data.get("data") or {}).get("item")
    except Exception:
        return None


def _escholarship_unit_page(first: int | None = None, more: str | None = None) -> dict | None:
    nodes_fields = "title permalink journal volume issue published authors { nodes { name } }"
    if more:
        q = f"""
        query UnitItems($unitId: ID!, $more: String) {{
          unit(id: $unitId) {{
            items(more: $more) {{ total more nodes {{ {nodes_fields} }} }}
          }}
        }}
        """
        vars = {"unitId": COGSCI_UNIT_ID, "more": more}
    else:
        q = f"""
        query UnitItems($unitId: ID!, $first: Int) {{
          unit(id: $unitId) {{
            items(first: $first) {{ total more nodes {{ {nodes_fields} }} }}
          }}
        }}
        """
        vars = {"unitId": COGSCI_UNIT_ID, "first": first or 200}
    try:
        import requests
        r = requests.post(
            "https://escholarship.org/graphql",
            headers={"Content-Type": "application/json"},
            json={"query": q, "variables": vars},
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            return None
        return data.get("data")
    except Exception:
        return None


def escholarship_cogsci_unit_items(log: logging.Logger, max_items: int | None = None) -> tuple[dict[str, dict], list[dict]]:
    """Return (title_to_node, all_nodes)."""
    title_to_node = {}
    all_nodes = []
    more = None
    page = 0
    max_pages = 15
    while page < max_pages:
        if max_items and len(all_nodes) >= max_items:
            break
        data = _escholarship_unit_page(first=200 if more is None else None, more=more)
        if not data:
            break
        unit = (data.get("unit") or {})
        items = unit.get("items") or {}
        nodes = items.get("nodes") or []
        for node in nodes:
            if max_items and len(all_nodes) >= max_items:
                break
            t = (node.get("title") or "").strip().lower()
            if t:
                title_to_node[t[:120]] = node
            all_nodes.append(node)
        if page == 0:
            log.info("eScholarship CogSci unit (%s): %s items total", COGSCI_UNIT_ID, items.get("total") or 0)
        more = items.get("more")
        if not more or not nodes:
            break
        page += 1
        time.sleep(0.2)
    return title_to_node, all_nodes


def _node_already_in_bib(node: dict, entries: dict, title_similar_threshold: float = 0.85) -> bool:
    node_title = (node.get("title") or "").strip().lower()
    if not node_title:
        return False
    for entry in entries.values():
        t = (entry.get("title") or "").strip().lower()
        if t and difflib.SequenceMatcher(None, node_title, t).ratio() >= title_similar_threshold:
            return True
    return False


def _node_has_author(node: dict, target_author: str) -> bool:
    if not target_author or not target_author.strip():
        return False
    parts = target_author.strip().lower().split()
    if not parts:
        return False
    authors = (node.get("authors") or {}).get("nodes") or []
    for a in authors:
        name = (a.get("name") or "").strip().lower()
        if not name:
            continue
        if all(p in name for p in parts):
            return True
    return False


def _node_to_bib_entry(node: dict, existing_keys: set[str]) -> tuple[str, dict] | None:
    title = (node.get("title") or "").strip()
    if not title:
        return None
    authors = (node.get("authors") or {}).get("nodes") or []
    author_str = " and ".join((a.get("name") or "").strip() for a in authors if (a.get("name") or "").strip())
    published = (node.get("published") or "").strip()
    year = published[:4] if len(published) >= 4 else ""
    journal = (node.get("journal") or "").strip() or "Proceedings of the Cognitive Science Society"
    volume = (node.get("volume") or "").strip()
    issue = (node.get("issue") or "").strip()

    def slug(s: str, max_words: int = 3) -> str:
        words = re.sub(r"[^\w\s]", " ", s).split()[:max_words]
        return "_".join(w.lower() for w in words if w).replace(" ", "_") or "unknown"

    first_author = (authors[0].get("name") or "").strip() if authors else "unknown"
    base_key = slug(first_author) + "_" + (year or "nodate") + "_" + slug(title, 2)
    base_key = re.sub(r"[^\w]", "_", base_key)[:50]
    citekey = base_key
    n = 1
    while citekey in existing_keys:
        citekey = f"{base_key}_{n}"
        n += 1

    entry = {
        "ENTRYTYPE": "inproceedings",
        "title": title,
        "year": year or "",
        "booktitle": journal,
    }
    if author_str:
        entry["author"] = author_str
    if volume:
        entry["volume"] = volume
    if issue:
        entry["issue"] = issue
    return citekey, entry


def escholarship_find_cogsci_entry(entry: dict, title_to_node: dict[str, dict], log: logging.Logger) -> dict | None:
    title = (entry.get("title") or "").strip()
    doi = (entry.get("doi") or "").strip()
    if doi:
        node = escholarship_item_by_doi(doi)
        if node:
            log.debug("eScholarship match by DOI for %s", title[:50])
            return node
    if not title:
        return None
    title_norm = title.lower()[:120]
    for unit_title, node in title_to_node.items():
        if title_norm in unit_title or unit_title in title_norm:
            return node
        if len(title_norm) > 30 and len(unit_title) > 30:
            if difflib.SequenceMatcher(None, title_norm, unit_title).ratio() > 0.85:
                return node
    return None


def _escholarship_author_items_page(email: str, first: int | None = None, more: str | None = None) -> dict | None:
    nodes_fields = "title permalink journal volume issue published authors { nodes { name } }"
    if more:
        q = f"""
        query AuthorItems($email: String!, $more: String) {{
          author(email: $email) {{ items(more: $more) {{ total more nodes {{ {nodes_fields} }} }} }}
        }}
        """
        vars = {"email": email, "more": more}
    else:
        q = f"""
        query AuthorItems($email: String!, $first: Int) {{
          author(email: $email) {{ items(first: $first) {{ total more nodes {{ {nodes_fields} }} }} }}
        }}
        """
        vars = {"email": email, "first": first or 200}
    try:
        import requests
        r = requests.post(
            "https://escholarship.org/graphql",
            headers={"Content-Type": "application/json"},
            json={"query": q, "variables": vars},
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            return None
        return data.get("data")
    except Exception:
        return None


def escholarship_author_cogsci_items(log: logging.Logger, email: str) -> list[dict]:
    cogsci = []
    more = None
    page = 0
    while page < 50:
        data = _escholarship_author_items_page(email, first=200 if more is None else None, more=more)
        if not data:
            break
        author_obj = (data.get("author") or {})
        items = author_obj.get("items") or {}
        nodes = items.get("nodes") or []
        if page == 0:
            log.info("eScholarship author(%s): %s items total", email, items.get("total") or 0)
        for node in nodes:
            journal = (node.get("journal") or "").strip().lower()
            if "cognitive science" in journal or "cogsci" in journal:
                cogsci.append(node)
        more = items.get("more")
        if not more or not nodes:
            break
        page += 1
        time.sleep(0.2)
    log.info("eScholarship author CogSci items: %d", len(cogsci))
    return cogsci


def main() -> None:
    parser = argparse.ArgumentParser(description="Gather candidates from .bib, ORCID, arXiv, eScholarship; output merge_map and proposed list")
    parser.add_argument("--no-fuzzy", action="store_true", help="Skip fuzzy match and merge_map.json output")
    parser.add_argument("--no-orcid", action="store_true", help="Skip ORCID discovery")
    parser.add_argument("--no-arxiv", action="store_true", help="Skip arXiv discovery")
    parser.add_argument("--no-escholarship", action="store_true", help="Skip eScholarship CogSci discovery and enrichment")
    parser.add_argument("--escholarship-max-items", type=int, default=None, metavar="N", help="Cap CogSci unit items to N")
    args = parser.parse_args()

    date_suffix = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"1_gather_candidates_{date_suffix}.log"
    log = setup_logging(log_path)
    log.info("Gather candidates started")

    current_path = BIB_DIR / "current_citations.bib"
    website_path = BIB_DIR / "website_citations.bib"
    out_path = BIB_DIR / "citations.bib"
    merge_map_path = PROJECT_ROOT / "merge_map.json"

    if not current_path.exists():
        log.error("Missing %s", current_path)
        sys.exit(1)
    if not website_path.exists():
        log.error("Missing %s", website_path)
        sys.exit(1)

    from scripts import bib_utils
    load_bib = bib_utils.load_bib
    save_bib = bib_utils.save_bib
    merge_entry_fields = bib_utils.merge_entry_fields

    current = load_bib(current_path)
    website = load_bib(website_path)
    log.info("Loaded %d from current, %d from website", len(current), len(website))

    merged = dict(current)
    for key, entry in website.items():
        if key in merged:
            merged[key] = merge_entry_fields(merged[key], entry)
        else:
            merged[key] = entry

    for key, entry in list(merged.items()):
        if "year" in entry:
            years = entry.get("year")
            if isinstance(years, list):
                entry["year"] = years[0] if years else ""
            elif isinstance(years, str) and years.count(",") >= 1:
                entry["year"] = years.split(",")[0].strip()

    log.info("Merged total unique keys: %d", len(merged))

    # Fuzzy close matches
    if not args.no_fuzzy:
        close = fuzzy_close_matches(merged)
        if close:
            merge_map_out = {
                "description": "Review and apply with apply_approvals or UI. merge_into: key_to_drop -> key_to_keep.",
                "drop": [],
                "merge_into": {},
            }
            for ka, kb, score in close:
                merge_map_out["merge_into"][kb] = ka
                merge_map_out["drop"].append(kb)
            with merge_map_path.open("w", encoding="utf-8") as f:
                json.dump(merge_map_out, f, indent=2)
            log.info("Wrote %d close-match pairs to %s", len(close), merge_map_path)
        else:
            log.info("No close matches found")

    proposed: list[dict] = []

    # ORCID
    if not args.no_orcid and ORCID_XML_WORKS_DIR.exists():
        log.info("Loading ORCID works from %s", ORCID_XML_WORKS_DIR)
        orcid_works_list = orcid_local_works(ORCID_XML_WORKS_DIR, log=log)
        for w in orcid_works_list:
            title = w.get("title") or ""
            if not title:
                continue
            doi = w.get("doi")
            year = w.get("year") or ""
            found = any(
                title_similar(e.get("title") or "", title) > 0.9 or e.get("doi") == doi
                for e in merged.values()
            )
            if not found:
                proposed.append({
                    "source": "ORCID",
                    "title": title,
                    "doi": doi,
                    "year": year,
                    "author": w.get("author"),
                    "journal": w.get("journal"),
                })
        log.info("ORCID: %d works, %d proposed", len(orcid_works_list), len([p for p in proposed if p.get("source") == "ORCID"]))
    elif not args.no_orcid:
        log.info("ORCID: no orcid_xml/works (or orcid/works), skipping")

    # arXiv
    if not args.no_arxiv:
        log.info("Querying arXiv: au:michael_c_frank, size=50")
        time.sleep(0.5)
        arxiv_works = arxiv_author_search(max_results=50, log=log)
        for w in arxiv_works:
            title = w.get("title") or ""
            if not title:
                continue
            found = any(title_similar(e.get("title") or "", title) > 0.85 for e in merged.values())
            if not found:
                proposed.append({
                    "source": "arXiv",
                    "title": title,
                    "arxiv_id": w.get("arxiv_id"),
                    "year": w.get("year") or "",
                })
        log.info("arXiv: %d works, %d proposed", len(arxiv_works), len([p for p in proposed if p.get("source") == "arXiv"]))

    # eScholarship: enrich existing CogSci + gather proposed
    if not args.no_escholarship:
        esch_max = args.escholarship_max_items
        title_to_node, all_nodes = escholarship_cogsci_unit_items(log, max_items=esch_max)
        cogsci_candidates = [
            (key, entry) for key, entry in merged.items()
            if ("cognitive science" in (entry.get("booktitle") or "").lower()
                or "Cognitive Science Society" in (entry.get("booktitle") or ""))
            and (entry.get("title") or "").strip()
        ]
        log.info("eScholarship: %d CogSci proceedings entries to enrich", len(cogsci_candidates))
        cogsci_count = 0
        for key, entry in cogsci_candidates:
            node = escholarship_find_cogsci_entry(entry, title_to_node, log)
            if node:
                updated = False
                if not entry.get("journal") and node.get("journal"):
                    entry["journal"] = node["journal"]
                    updated = True
                if not entry.get("volume") and node.get("volume"):
                    entry["volume"] = str(node["volume"])
                    updated = True
                if not entry.get("issue") and node.get("issue"):
                    entry["issue"] = str(node["issue"])
                    updated = True
                if node.get("permalink") and not entry.get("url"):
                    entry["url"] = node["permalink"]
                    updated = True
                if updated:
                    cogsci_count += 1
                    log.debug("eScholarship enriched: %s", key)
        log.info("CogSci entries enriched from eScholarship: %d", cogsci_count)

        existing_keys = set(merged.keys())
        author_cogsci_nodes = escholarship_author_cogsci_items(log, COGSCI_ESCHOLARSHIP_AUTHOR_EMAIL)
        for node in author_cogsci_nodes:
            if _node_already_in_bib(node, merged):
                continue
            pair = _node_to_bib_entry(node, existing_keys)
            if not pair:
                continue
            citekey, bib_entry = pair
            existing_keys.add(citekey)
            authors = (node.get("authors") or {}).get("nodes") or []
            author_str = " and ".join((a.get("name") or "").strip() for a in authors if (a.get("name") or "").strip())
            journal = (node.get("journal") or "").strip() or "Proceedings of the Cognitive Science Society"
            published = (node.get("published") or "").strip()
            year = published[:4] if len(published) >= 4 else ""
            proposed.append({
                "source": "eScholarship",
                "title": (node.get("title") or "").strip(),
                "year": year,
                "author": author_str or None,
                "journal": journal,
                "citekey": citekey,
                "bib_entry": bib_entry,
            })
        for node in all_nodes:
            if _node_already_in_bib(node, merged):
                continue
            if not _node_has_author(node, COGSCI_NEW_ENTRY_AUTHOR):
                continue
            pair = _node_to_bib_entry(node, existing_keys)
            if not pair:
                continue
            citekey, bib_entry = pair
            existing_keys.add(citekey)
            authors = (node.get("authors") or {}).get("nodes") or []
            author_str = " and ".join((a.get("name") or "").strip() for a in authors if (a.get("name") or "").strip())
            journal = (node.get("journal") or "").strip() or "Proceedings of the Cognitive Science Society"
            published = (node.get("published") or "").strip()
            year = published[:4] if len(published) >= 4 else ""
            proposed.append({
                "source": "eScholarship",
                "title": (node.get("title") or "").strip(),
                "year": year,
                "author": author_str or None,
                "journal": journal,
                "citekey": citekey,
                "bib_entry": bib_entry,
            })
        log.info("eScholarship proposed: %d", len([p for p in proposed if p.get("source") == "eScholarship"]))

    save_bib(merged, out_path)
    log.info("Wrote %s (%d entries)", out_path, len(merged))

    proposed_path = LOGS_DIR / f"1_gather_candidates_proposed_{date_suffix}.json"
    proposed_path.write_text(json.dumps(proposed, indent=2), encoding="utf-8")
    log.info("Wrote proposed list to %s (%d items)", proposed_path, len(proposed))


if __name__ == "__main__":
    main()
