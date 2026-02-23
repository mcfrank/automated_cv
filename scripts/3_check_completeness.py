#!/usr/bin/env python3
"""
Check bib entries for required fields, normalize DOI formatting, add DOIs via CrossRef.
Enrich Proceedings of the Cognitive Science Society entries using eScholarship API.
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


def escholarship_search_cogsci(title_substring: str) -> dict | None:
    query = """
    query {
      items(first: 100, tags: "type:ARTICLE") {
        nodes {
          title
          permalink
          journal
          volume
          issue
          published
          authors { nodes { name } }
        }
      }
    }
    """
    data = escholarship_graphql(query)
    if not data:
        return None
    nodes = (data.get("items") or {}).get("nodes") or []
    title_clean = (title_substring or "").lower().strip()[:80]
    for node in nodes:
        t = (node.get("title") or "").lower()
        if title_clean in t or title_clean[:50] in t:
            return node
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Check completeness and format of bib; enrich CogSci via eScholarship")
    parser.add_argument("--no-doi-enrich", action="store_true", help="Skip CrossRef DOI enrichment")
    parser.add_argument("--no-escholarship", action="store_true", help="Skip eScholarship CogSci enrichment")
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
    report = {"missing_fields": [], "doi_normalized": 0, "doi_added": 0, "cogsci_enriched": 0, "duplicate_year": []}

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

    log.info("Missing required fields: %d entries", len(report["missing_fields"]))

    if not args.no_escholarship:
        cogsci_count = 0
        for key, entry in list(entries.items()):
            booktitle = (entry.get("booktitle") or "").strip()
            if "cognitive science" not in booktitle.lower() and "Cognitive Science Society" not in booktitle:
                continue
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            node = escholarship_search_cogsci(title)
            if node:
                if not entry.get("journal") and node.get("journal"):
                    entry["journal"] = node["journal"]
                    cogsci_count += 1
                if not entry.get("volume") and node.get("volume"):
                    entry["volume"] = str(node["volume"])
                if not entry.get("issue") and node.get("issue"):
                    entry["issue"] = str(node["issue"])
                if node.get("permalink") and not entry.get("url"):
                    entry["url"] = node["permalink"]
            time.sleep(0.2)
        report["cogsci_enriched"] = cogsci_count
        log.info("CogSci entries enriched from eScholarship: %d", cogsci_count)

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
        f"DOI normalized: {report['doi_normalized']}",
        f"DOI added: {report['doi_added']}",
        f"CogSci enriched: {report['cogsci_enriched']}",
        f"Duplicate year fixed: {len(report['duplicate_year'])}",
    ]
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    log.info("Wrote report to %s", report_path)

    if args.write:
        bib_utils.save_bib(entries, bib_path)
        log.info("Wrote updated bib to %s", bib_path)


if __name__ == "__main__":
    main()
