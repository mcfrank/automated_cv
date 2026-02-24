#!/usr/bin/env python3
"""
DEPRECATED: Use the refactored pipeline instead:
  1. python scripts/1_gather_candidates.py   (eScholarship enrich + proposed in step 1)
  2. python scripts/2_check_completeness_enrich.py --write  (completeness + CrossRef DOIs only)
  3. streamlit run scripts/approve_citations_ui.py

This script is kept for backwards compatibility. It checks completeness, normalizes
DOIs, enriches via CrossRef and eScholarship CogSci.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIB_DIR = PROJECT_ROOT / "bib"
LOGS_DIR = PROJECT_ROOT / "logs"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REQUIRED = {
    "article": ["author", "title", "journal", "year"],
    "book": ["author", "title", "year"],
    "inproceedings": ["author", "title", "booktitle", "year"],
    "conference": ["author", "title", "booktitle", "year"],
    "incollection": ["author", "title", "booktitle", "year"],
    "inbook": ["author", "title", "year"],
    "misc": ["title"],
    "unpublished": ["author", "title", "year"],
}


def _title_needs_sentence_case(title: str) -> bool:
    """True if title looks like all caps or title case (most words capitalized)."""
    if not title or len(title.strip()) < 2:
        return False
    s = title.strip()
    # All caps (and has letters)
    if s == s.upper() and any(c.isalpha() for c in s):
        return True
    # Title case: most words (2+ chars) start with upper and rest lower
    words = [w for w in s.split() if len(w) >= 2]
    if not words:
        return False
    title_case_count = sum(
        1 for w in words
        if w[0].isupper() and (len(w) == 1 or w[1:].lower() == w[1:])
    )
    return title_case_count >= 0.5 * len(words)


def _to_sentence_case(title: str) -> str:
    """Apply sentence case: first letter and first after ': ' or '. ' capitalized; rest lower; preserve acronyms (all-caps 2+ chars)."""
    if not title:
        return title
    words = title.split()
    new_words = []
    for w in words:
        if len(w) >= 2 and w.isupper() and w.isalpha():
            new_words.append(w)
        else:
            new_words.append(w.lower())
    s = " ".join(new_words)
    if not s:
        return title
    s = s[0].upper() + s[1:]
    for sep in (": ", ". "):
        i = 0
        while i < len(s):
            i = s.find(sep, i)
            if i < 0:
                break
            j = i + len(sep)
            if j < len(s) and s[j].isalpha():
                s = s[:j] + s[j].upper() + s[j + 1 :]
            i = j + 1
    return s


def normalize_title_sentence_case(title: str) -> str | None:
    """If title looks like all caps or title case, return sentence-case version; else return None (no change)."""
    if not title or not title.strip():
        return None
    if not _title_needs_sentence_case(title):
        return None
    return _to_sentence_case(title.strip())


def setup_logging(log_path: Path) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("check_completeness")
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


def crossref_lookup(title: str, author: str, mailto: str | None) -> dict | None:
    import requests
    q = f"{title} {author}".strip()[:200]
    url = "https://api.crossref.org/works"
    params = {"query.bibliographic": q, "rows": 1}
    headers = {"User-Agent": f"CV-completeness/1.0 (mailto:{mailto or 'anonymous@example.com'})"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        items = data.get("message", {}).get("items") or []
        return items[0] if items else None
    except Exception:
        return None
    finally:
        time.sleep(0.5)


def escholarship_graphql(query: str) -> dict | None:
    import requests
    try:
        r = requests.post(
            "https://escholarship.org/graphql",
            headers={"Content-Type": "application/json"},
            json={"query": query},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            return None
        return data.get("data")
    except Exception:
        return None


# eScholarship unit for Proceedings of the Cognitive Science Society (UC-hosted)
COGSCI_UNIT_ID = "cognitivesciencesociety"
# Only add new CogSci entries from eScholarship when this author is in the author list
COGSCI_NEW_ENTRY_AUTHOR = "Michael C. Frank"
# eScholarship author query uses email (API supports author(email: ...)); fetches only this author's items.
COGSCI_ESCHOLARSHIP_AUTHOR_EMAIL = "mcfrank@stanford.edu"
# Cap items fetched from CogSci unit (None = no limit, fetch full ~20k; set to int to limit for faster runs).
ESCHOLARSHIP_MAX_ITEMS = None


def escholarship_item_by_doi(doi: str) -> dict | None:
    """Fetch a single item from eScholarship by DOI (works for CogSci proceedings with DOIs)."""
    if not doi or not doi.strip():
        return None
    doi_clean = doi.strip()
    if doi_clean.startswith("http"):
        doi_clean = doi_clean.split("doi.org/")[-1].strip()
    query = """
    query ItemByDoi($id: String!, $scheme: IDScheme) {
      item(id: $id, scheme: $scheme) {
        title
        permalink
        journal
        volume
        issue
        published
        authors { nodes { name } }
      }
    }
    """
    try:
        import requests
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


def _escholarship_author_items_page(email: str, first: int | None = None, more: str | None = None) -> dict | None:
    """One page of items for author(email: ...). Same node shape as unit items for _node_to_bib_entry."""
    nodes_fields = "title permalink journal volume issue published authors { nodes { name } }"
    if more:
        q = f"""
        query AuthorItems($email: String!, $more: String) {{
          author(email: $email) {{
            items(more: $more) {{
              total
              more
              nodes {{ {nodes_fields} }}
            }}
          }}
        }}
        """
        vars = {"email": email, "more": more}
    else:
        q = f"""
        query AuthorItems($email: String!, $first: Int) {{
          author(email: $email) {{
            items(first: $first) {{
              total
              more
              nodes {{ {nodes_fields} }}
            }}
          }}
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


def escholarship_author_cogsci_items(log, email: str) -> list[dict]:
    """Fetch all items for author(email); return only those that are CogSci proceedings (journal contains 'cognitive science')."""
    cogsci = []
    more = None
    page = 0
    while page < 50:
        data = _escholarship_author_items_page(email, first=200 if more is None else None, more=more)
        if not data:
            log.debug("eScholarship author query returned no data")
            break
        author_obj = (data.get("author") or {})
        items = author_obj.get("items") or {}
        nodes = items.get("nodes") or []
        total = items.get("total") or 0
        if page == 0:
            log.info("eScholarship author(%s): %s items total", email, total)
        for node in nodes:
            journal = (node.get("journal") or "").strip().lower()
            if "cognitive science" in journal or "cogsci" in journal:
                cogsci.append(node)
        log.debug("eScholarship author page %d: %d nodes, %d CogSci so far", page + 1, len(nodes), len(cogsci))
        more = items.get("more")
        if not more or not nodes:
            break
        page += 1
        time.sleep(0.2)
    log.info("eScholarship author CogSci items: %d", len(cogsci))
    return cogsci


def _escholarship_unit_page(first: int | None = None, more: str | None = None) -> dict | None:
    """One page of unit items; use first=N or more=cursor. Requests authors for new-entry creation."""
    nodes_fields = "title permalink journal volume issue published authors { nodes { name } }"
    if more:
        q = f"""
        query UnitItems($unitId: ID!, $more: String) {{
          unit(id: $unitId) {{
            items(more: $more) {{
              total
              more
              nodes {{ {nodes_fields} }}
            }}
          }}
        }}
        """
        vars = {"unitId": COGSCI_UNIT_ID, "more": more}
    else:
        q = f"""
        query UnitItems($unitId: ID!, $first: Int) {{
          unit(id: $unitId) {{
            items(first: $first) {{
              total
              more
              nodes {{ {nodes_fields} }}
            }}
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


def escholarship_cogsci_unit_items(log, max_items: int | None = None) -> tuple[dict[str, dict], list[dict]]:
    """Fetch items from the CogSci unit; return (title_to_node for matching, all_nodes for new-entry retrieval).
    If max_items is set, stop after that many items (avoids downloading full ~20k; use None for full corpus)."""
    max_items = max_items if max_items is not None else ESCHOLARSHIP_MAX_ITEMS
    title_to_node = {}
    all_nodes = []
    more = None
    page = 0
    max_pages = 15
    while page < max_pages:
        if max_items and len(all_nodes) >= max_items:
            log.info("eScholarship: stopping at %d items (limit; use --escholarship-full for full corpus)", len(all_nodes))
            break
        data = _escholarship_unit_page(first=200 if more is None else None, more=more)
        if not data:
            log.debug("eScholarship unit query returned no data")
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
        total = items.get("total") or 0
        if page == 0:
            log.info("eScholarship CogSci unit (%s): %s items total", COGSCI_UNIT_ID, total)
        log.debug("eScholarship page %d: %d nodes", page + 1, len(nodes))
        more = items.get("more")
        if not more or not nodes:
            break
        page += 1
        time.sleep(0.2)
    return title_to_node, all_nodes


def _node_already_in_bib(node: dict, entries: dict, title_similar_threshold: float = 0.85) -> bool:
    """True if any bib entry matches this node by title (fuzzy)."""
    import difflib
    node_title = (node.get("title") or "").strip().lower()
    if not node_title:
        return False
    for entry in entries.values():
        t = (entry.get("title") or "").strip().lower()
        if t and difflib.SequenceMatcher(None, node_title, t).ratio() >= title_similar_threshold:
            return True
    return False


def _node_has_author(node: dict, target_author: str) -> bool:
    """True if any of the node's authors match target_author (flexible: e.g. 'Michael C. Frank' or 'Frank, Michael C.')."""
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
    """Build (citekey, entry dict) for a CogSci node; return None if missing title."""
    import re
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


def escholarship_find_cogsci_entry(entry: dict, title_to_node: dict[str, dict], log) -> dict | None:
    """Find eScholarship node for this bib entry: try by DOI first, then by title match in unit items."""
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
            import difflib
            if difflib.SequenceMatcher(None, title_norm, unit_title).ratio() > 0.85:
                return node
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Check completeness and format of bib; enrich CogSci via eScholarship")
    parser.add_argument("--no-doi-enrich", action="store_true", help="Skip CrossRef DOI enrichment")
    parser.add_argument("--no-escholarship", action="store_true", help="Skip eScholarship CogSci enrichment")
    parser.add_argument("--escholarship-max-items", type=int, default=None, metavar="N", help="Cap CogSci unit items to N (default: no limit, fetch full ~20k)")
    parser.add_argument("--write", action="store_true", help="Write updated bib/citations.bib")
    args = parser.parse_args()

    date_suffix = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"3_check_completeness_{date_suffix}.log"
    log = setup_logging(log_path)
    log.info("Check completeness started")

    bib_path = BIB_DIR / "citations.bib"
    if not bib_path.exists():
        bib_path = BIB_DIR / "current_citations.bib"
    if not bib_path.exists():
        log.error("No bib file found")
        sys.exit(1)

    from scripts import bib_utils

    entries = bib_utils.load_bib(bib_path)
    report = {"missing_fields": [], "doi_normalized": 0, "doi_added": 0, "cogsci_enriched": 0, "cogsci_added": 0, "duplicate_year": [], "enrich_missing_count": 0, "sentence_case_fixed": 0}

    for key, entry in list(entries.items()):
        etype = entry.get("ENTRYTYPE", "misc")
        required = REQUIRED.get(etype, ["author", "title", "year"])
        for field in required:
            if not entry.get(field) or not str(entry.get(field)).strip():
                report["missing_fields"].append((key, field, etype))
        if entry.get("doi"):
            raw = entry["doi"]
            norm = bib_utils.normalize_doi(raw)
            if norm and norm != raw:
                entry["doi"] = norm
                report["doi_normalized"] += 1
        if "year" in entry and isinstance(entry.get("year"), str) and entry["year"].count(",") > 0:
            report["duplicate_year"].append(key)
            entry["year"] = entry["year"].split(",")[0].strip()
        # Sentence case for titles: fix only if all caps or title case; capitalize first and first after : or .
        if entry.get("title"):
            new_title = normalize_title_sentence_case(entry["title"])
            if new_title is not None and new_title != entry["title"]:
                entry["title"] = new_title
                report["sentence_case_fixed"] += 1

    log.info("Missing required fields: %d entries", len(report["missing_fields"]))
    if report["sentence_case_fixed"]:
        log.info("Sentence case: fixed %d title(s)", report["sentence_case_fixed"])

    # Enrich entries missing required fields via CrossRef (by title + author)
    mailto = ""
    try:
        from dotenv import load_dotenv
        import os
        load_dotenv(PROJECT_ROOT / ".env")
        mailto = os.environ.get("CROSSREF_MAILTO", "")
    except ImportError:
        pass
    keys_missing_fields = {k for k, _, _ in report["missing_fields"]}
    enrich_count = 0
    for key in list(keys_missing_fields):
        entry = entries.get(key)
        if not entry:
            continue
        title = (entry.get("title") or "").strip()
        author = (entry.get("author") or "").strip()
        if not title:
            continue
        result = crossref_lookup(title, author, mailto or None)
        if not result:
            continue
        if not entry.get("author") and result.get("author"):
            authors = result.get("author") or []
            parts = []
            for a in authors:
                given = (a.get("given") or "").strip()
                family = (a.get("family") or "").strip()
                if family:
                    parts.append(f"{family}, {given}".strip(", ") if given else family)
            if parts:
                entry["author"] = " and ".join(parts)
                enrich_count += 1
        if not entry.get("year"):
            for part in ("published-print", "published-online"):
                date_parts = (result.get(part) or {}).get("date-parts", [[]])
                if date_parts and date_parts[0]:
                    entry["year"] = str(date_parts[0][0])
                    enrich_count += 1
                    break
        if not entry.get("journal") and result.get("container-title"):
            titles = result.get("container-title") or []
            if titles:
                entry["journal"] = titles[0]
                enrich_count += 1
        if not entry.get("doi") and result.get("DOI"):
            entry["doi"] = result["DOI"]
            enrich_count += 1
    report["enrich_missing_count"] = enrich_count
    if enrich_count:
        log.info("CrossRef enriched %d missing fields across entries", enrich_count)

    if not args.no_escholarship:
        cogsci_candidates = [
            (key, entry) for key, entry in entries.items()
            if ("cognitive science" in (entry.get("booktitle") or "").lower()
                or "Cognitive Science Society" in (entry.get("booktitle") or ""))
            and (entry.get("title") or "").strip()
        ]
        log.info("eScholarship: %d CogSci proceedings entries to try", len(cogsci_candidates))
        esch_max = args.escholarship_max_items if args.escholarship_max_items is not None else ESCHOLARSHIP_MAX_ITEMS
        title_to_node, all_nodes = escholarship_cogsci_unit_items(log, max_items=esch_max)
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
                    log.info("eScholarship enriched: %s", key)
        report["cogsci_enriched"] = cogsci_count
        log.info("CogSci entries enriched from eScholarship: %d", cogsci_count)

        # Add new bib entries: (1) author(email) when eScholarship has items under that email,
        # (2) from the unit items we already fetched for enrichment, filter by author name (no extra download)
        author_cogsci_nodes = escholarship_author_cogsci_items(log, COGSCI_ESCHOLARSHIP_AUTHOR_EMAIL)
        existing_keys = set(entries.keys())
        cogsci_added = 0
        for node in author_cogsci_nodes:
            if _node_already_in_bib(node, entries):
                continue
            pair = _node_to_bib_entry(node, existing_keys)
            if not pair:
                continue
            citekey, new_entry = pair
            entries[citekey] = new_entry
            existing_keys.add(citekey)
            cogsci_added += 1
            log.info("eScholarship new entry (author email): %s", citekey)
        for node in all_nodes:
            if _node_already_in_bib(node, entries):
                continue
            if not _node_has_author(node, COGSCI_NEW_ENTRY_AUTHOR):
                continue
            pair = _node_to_bib_entry(node, existing_keys)
            if not pair:
                continue
            citekey, new_entry = pair
            entries[citekey] = new_entry
            existing_keys.add(citekey)
            cogsci_added += 1
            log.info("eScholarship new entry (unit + author filter): %s", citekey)
        report["cogsci_added"] = cogsci_added
        if cogsci_added:
            log.info("CogSci new entries from eScholarship: %d", cogsci_added)

    if not args.no_doi_enrich:
        mailto = ""
        try:
            from dotenv import load_dotenv
            import os
            load_dotenv(PROJECT_ROOT / ".env")
            mailto = os.environ.get("CROSSREF_MAILTO", "")
        except ImportError:
            pass
        added = 0
        for key, entry in list(entries.items()):
            if entry.get("doi"):
                continue
            title = (entry.get("title") or "").strip()[:150]
            author = (entry.get("author") or "").split(" and ")[0].strip()[:80]
            if not title:
                continue
            result = crossref_lookup(title, author, mailto or None)
            if result and result.get("DOI"):
                entry["doi"] = result["DOI"]
                added += 1
        report["doi_added"] = added
        log.info("CrossRef DOI enrichment: added %d", added)

    report_path = LOGS_DIR / f"3_check_completeness_report_{date_suffix}.txt"
    report_lines = [
        "# Completeness report",
        f"Missing required fields: {len(report['missing_fields'])}",
        "",
        "\n".join(f"  {k} ({t}): missing {f}" for k, f, t in report["missing_fields"][:50]),
        "",
        f"Enrich missing (CrossRef): {report['enrich_missing_count']} fields filled",
        f"DOI normalized: {report['doi_normalized']}",
        f"DOI added: {report['doi_added']}",
        f"Sentence case (titles): {report['sentence_case_fixed']} fixed",
        f"CogSci enriched (eScholarship): {report['cogsci_enriched']}",
        f"CogSci new entries (eScholarship): {report['cogsci_added']}",
        f"Duplicate year fixed: {len(report['duplicate_year'])}",
    ]
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    log.info("Wrote report to %s", report_path)

    if args.write:
        bib_utils.save_bib(entries, bib_path)
        log.info("Wrote updated bib to %s", bib_path)


if __name__ == "__main__":
    main()
