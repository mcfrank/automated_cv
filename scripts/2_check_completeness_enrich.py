#!/usr/bin/env python3
"""
Check bib entries for required fields, normalize DOI, sentence-case titles.
Enrich missing DOIs via CrossRef only (no author/year/journal enrichment).
Optionally load and enrich proposed list from step 1; write enriched proposed for step 3.
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
TEX_DIR = PROJECT_ROOT / "tex"
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
    if not title or len(title.strip()) < 2:
        return False
    s = title.strip()
    if s == s.upper() and any(c.isalpha() for c in s):
        return True
    words = [w for w in s.split() if len(w) >= 2]
    if not words:
        return False
    title_case_count = sum(
        1 for w in words
        if w[0].isupper() and (len(w) == 1 or w[1:].lower() == w[1:])
    )
    return title_case_count >= 0.5 * len(words)


def _to_sentence_case(title: str) -> str:
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
                s = s[:j] + s[j].upper() + s[j + 1:]
            i = j + 1
    return s


def normalize_title_sentence_case(title: str) -> str | None:
    if not title or not title.strip():
        return None
    if not _title_needs_sentence_case(title):
        return None
    return _to_sentence_case(title.strip())


def setup_logging(log_path: Path) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("check_completeness_enrich")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Check completeness and enrich DOIs only (CrossRef)")
    parser.add_argument("--no-doi-enrich", action="store_true", help="Skip CrossRef DOI enrichment")
    parser.add_argument("--write", action="store_true", help="Write updated bib/citations.bib")
    parser.add_argument("--no-proposed", action="store_true", help="Skip loading/enriching proposed list from step 1")
    args = parser.parse_args()

    date_suffix = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"2_check_completeness_{date_suffix}.log"
    log = setup_logging(log_path)
    log.info("Check completeness and enrich (DOIs only) started")

    bib_path = BIB_DIR / "citations.bib"
    if not bib_path.exists():
        bib_path = BIB_DIR / "current_citations.bib"
    if not bib_path.exists():
        log.error("No bib file found")
        sys.exit(1)

    from scripts import bib_utils

    entries = bib_utils.load_bib(bib_path)
    report = {
        "missing_fields": [],
        "doi_normalized": 0,
        "doi_added": 0,
        "duplicate_year": [],
        "sentence_case_fixed": 0,
    }

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
        if entry.get("title"):
            new_title = normalize_title_sentence_case(entry["title"])
            if new_title is not None and new_title != entry["title"]:
                entry["title"] = new_title
                report["sentence_case_fixed"] += 1

    log.info("Missing required fields: %d entries", len(report["missing_fields"]))
    if report["sentence_case_fixed"]:
        log.info("Sentence case: fixed %d title(s)", report["sentence_case_fixed"])

    # CrossRef: DOIs only
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

    # Optional: citekey validation (tex vs bib)
    tex_path = TEX_DIR / "main.tex"
    if tex_path.exists():
        cited = bib_utils.parse_fullcites(tex_path)
        bib_keys = set(entries.keys())
        missing = cited - bib_keys
        uncited = bib_keys - cited
        log.info("Cited in tex: %d; in bib: %d", len(cited), len(bib_keys))
        if missing:
            log.warning("Missing from bib (%d): %s", len(missing), sorted(missing)[:20])
        report["missing_citekeys"] = list(missing)
        report["uncited_keys_count"] = len(uncited)

    report_path = LOGS_DIR / f"2_check_completeness_report_{date_suffix}.txt"
    report_lines = [
        "# Completeness report",
        f"Missing required fields: {len(report['missing_fields'])}",
        "",
        "\n".join(f"  {k} ({t}): missing {f}" for k, f, t in report["missing_fields"][:50]),
        "",
        f"DOI normalized: {report['doi_normalized']}",
        f"DOI added: {report['doi_added']}",
        f"Sentence case (titles): {report['sentence_case_fixed']} fixed",
        f"Duplicate year fixed: {len(report['duplicate_year'])}",
    ]
    if "missing_citekeys" in report and report["missing_citekeys"]:
        report_lines.append("")
        report_lines.append(f"Missing citekeys (in tex but not in bib): {len(report['missing_citekeys'])}")
        report_lines.extend(f"  - {k}" for k in sorted(report["missing_citekeys"])[:30])
    if report.get("uncited_keys_count", 0) > 0:
        report_lines.append("")
        report_lines.append(f"Uncited keys in bib: {report['uncited_keys_count']}")
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    log.info("Wrote report to %s", report_path)

    # Load and optionally enrich proposed list from step 1
    enriched_proposed: list[dict] = []
    if not args.no_proposed and LOGS_DIR.is_dir():
        proposed_paths = list(LOGS_DIR.glob("1_gather_candidates_proposed_*.json"))
        proposed_paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if proposed_paths:
            try:
                enriched_proposed = json.loads(proposed_paths[0].read_text(encoding="utf-8"))
                log.info("Loaded proposed list from %s (%d items)", proposed_paths[0].name, len(enriched_proposed))
                if not args.no_doi_enrich and enriched_proposed:
                    mailto = ""
                    try:
                        from dotenv import load_dotenv
                        import os
                        load_dotenv(PROJECT_ROOT / ".env")
                        mailto = os.environ.get("CROSSREF_MAILTO", "")
                    except ImportError:
                        pass
                    for p in enriched_proposed:
                        if p.get("doi"):
                            continue
                        if p.get("bib_entry"):
                            continue
                        title = (p.get("title") or "").strip()[:150]
                        author = (p.get("author") or "Frank").split(" and ")[0].strip()[:80]
                        if not title:
                            continue
                        result = crossref_lookup(title, author, mailto or None)
                        if result and result.get("DOI"):
                            p["doi"] = result["DOI"]
                enriched_path = LOGS_DIR / f"2_enriched_proposed_{date_suffix}.json"
                enriched_path.write_text(json.dumps(enriched_proposed, indent=2), encoding="utf-8")
                log.info("Wrote enriched proposed to %s", enriched_path)
            except Exception as e:
                log.warning("Could not load or enrich proposed list: %s", e)

    if args.write:
        bib_utils.save_bib(entries, bib_path)
        log.info("Wrote updated bib to %s", bib_path)


if __name__ == "__main__":
    main()
