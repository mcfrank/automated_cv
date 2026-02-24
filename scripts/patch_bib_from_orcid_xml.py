#!/usr/bin/env python3
"""
Patch citations.bib using authors (and journal) from ORCID XML when the bib entry
was mis-parsed and has only a single author. Fuzzy-matches bib entries to ORCID
works by title and year, then applies ORCID author/journal when ORCID has fuller data.

Usage:
  python scripts/patch_bib_from_orcid_xml.py --dry-run   # report only, no write
  python scripts/patch_bib_from_orcid_xml.py             # apply patches and write bib
"""
from __future__ import annotations

import argparse
import difflib
import logging
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIB_DIR = PROJECT_ROOT / "bib"
LOGS_DIR = PROJECT_ROOT / "logs"
ORCID_XML_WORKS_DIR = PROJECT_ROOT / "orcid_xml" / "works"
if not ORCID_XML_WORKS_DIR.exists():
    ORCID_XML_WORKS_DIR = PROJECT_ROOT / "orcid" / "works"

ORCID_WORK_NS = "http://www.orcid.org/ns/work"
ORCID_COMMON_NS = "http://www.orcid.org/ns/common"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Title similarity threshold for fuzzy match (0..1)
TITLE_SIMILARITY_THRESHOLD = 0.88


def _normalize_title(s: str) -> str:
    """Strip braces, lower, collapse spaces."""
    if not s:
        return ""
    t = re.sub(r"[\{\}]", "", s)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def _normalize_year_for_match(year_val) -> str:
    """Extract 4-digit year for matching; empty string if none."""
    if year_val is None:
        return ""
    s = str(year_val).strip()
    m = re.search(r"(\d{4})", s)
    return m.group(1) if m else ""


def _parse_orcid_works(works_dir: Path) -> list[dict]:
    """Parse ORCID work XML files; return list of {title, doi, year, author, journal}."""
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
        except ET.ParseError:
            pass
        except Exception:
            pass
    return works


def _title_similarity(a: str, b: str) -> float:
    """Return similarity ratio in [0, 1]."""
    an = _normalize_title(a)
    bn = _normalize_title(b)
    if not an or not bn:
        return 0.0
    return difflib.SequenceMatcher(None, an, bn).ratio()


def _bib_author_is_frank_only(bib_author: str) -> bool:
    """True if the bib entry's sole author is Frank, Michael C. (the mis-parse bug case)."""
    bib_a = (bib_author or "").strip()
    if not bib_a:
        return False
    if " and " in bib_a:
        return False  # multiple authors: do not patch
    name = bib_a.lower()
    return "frank" in name and "michael" in name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patch bib author/journal from ORCID XML by fuzzy matching title+year. Use --dry-run to preview."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report patches only; do not write bib")
    parser.add_argument("--bib", type=Path, default=None, help="Path to .bib file (default: bib/citations.bib)")
    parser.add_argument("--threshold", type=float, default=TITLE_SIMILARITY_THRESHOLD, help="Title similarity threshold (default: 0.88)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger(__name__)

    bib_path = args.bib or (BIB_DIR / "citations.bib")
    if not bib_path.is_absolute():
        bib_path = PROJECT_ROOT / bib_path
    if not bib_path.exists():
        log.error("Bib file not found: %s", bib_path)
        sys.exit(1)

    log.info("Loading ORCID works from %s", ORCID_XML_WORKS_DIR)
    orcid_works = _parse_orcid_works(ORCID_XML_WORKS_DIR)
    log.info("ORCID: %d works parsed", len(orcid_works))

    from scripts import bib_utils
    entries = bib_utils.load_bib(bib_path)
    log.info("Bib: %d entries", len(entries))

    # Build list of (citekey, entry, orcid_work) to patch
    patches = []
    for citekey, entry in entries.items():
        bib_title = (entry.get("title") or "").strip()
        bib_year = _normalize_year_for_match(entry.get("year"))
        bib_author = (entry.get("author") or "").strip()
        bib_journal = (entry.get("journal") or "").strip()
        if not bib_title:
            continue
        best_ratio = 0.0
        best_orcid = None
        for ow in orcid_works:
            if bib_year and ow.get("year") and _normalize_year_for_match(ow["year"]) != bib_year:
                continue
            ratio = _title_similarity(bib_title, ow.get("title") or "")
            if ratio >= args.threshold and ratio > best_ratio:
                best_ratio = ratio
                best_orcid = ow
        if best_orcid is None:
            continue
        # A) Patch author only when bib sole author is "Frank, Michael C." (bug); B) patch journal only when missing
        orcid_author = (best_orcid.get("author") or "").strip()
        orcid_journal = (best_orcid.get("journal") or "").strip()
        patch_author = orcid_author and _bib_author_is_frank_only(bib_author)
        patch_journal = orcid_journal and not bib_journal
        if patch_author or patch_journal:
            patches.append((citekey, entry, best_orcid, patch_author, patch_journal))

    if not patches:
        log.info("No patches to apply.")
        return

    log.info("Proposed %d patch(es):", len(patches))
    for citekey, entry, orcid_work, patch_author, patch_journal in patches:
        bib_title = (entry.get("title") or "")[:60]
        log.info("  [%s] %s...", citekey, bib_title + ("..." if len(entry.get("title") or "") > 60 else ""))
        if patch_author:
            log.info("    author: %s  ->  %s", (entry.get("author") or "")[:50], (orcid_work.get("author") or "")[:70])
        if patch_journal:
            log.info("    journal: (missing)  ->  %s", (orcid_work.get("journal") or "")[:60])

    if args.dry_run:
        log.info("Dry run: no changes written. Run without --dry-run to apply.")
        return

    for citekey, entry, orcid_work, patch_author, patch_journal in patches:
        if patch_author:
            entry["author"] = orcid_work.get("author") or entry.get("author")
        if patch_journal:
            entry["journal"] = orcid_work.get("journal") or ""

    bib_utils.save_bib(entries, bib_path)
    log.info("Wrote patched bib to %s", bib_path)


if __name__ == "__main__":
    main()
