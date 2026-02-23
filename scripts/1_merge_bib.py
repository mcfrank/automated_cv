#!/usr/bin/env python3
"""
Merge bib/current_citations.bib and bib/website_citations.bib into bib/citations.bib.
Output merge_map.json for close matches (fuzzy match on title + first author).
Optionally apply an existing merge_map.json. Enrich missing DOIs via CrossRef.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import difflib

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIB_DIR = PROJECT_ROOT / "bib"
LOGS_DIR = PROJECT_ROOT / "logs"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def setup_logging(log_path: Path) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("merge_bib")
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
        for kb in keys[i + 1 :]:
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


def crossref_lookup(title: str, author: str, mailto: str | None) -> dict | None:
    import time
    import requests
    q = f"{title} {author}".strip()[:200]
    url = "https://api.crossref.org/works"
    params = {"query.bibliographic": q, "rows": 1}
    headers = {"User-Agent": f"CV-bib-merge/1.0 (mailto:{mailto or 'anonymous@example.com'})"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        items = data.get("message", {}).get("items") or []
        if items:
            return items[0]
    except Exception:
        pass
    time.sleep(0.5)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge two BibTeX files and output merge_map.json")
    parser.add_argument("--apply-merge-map", type=Path, default=None, help="Apply this merge_map.json and write merged bib")
    parser.add_argument("--no-doi-enrich", action="store_true", help="Skip CrossRef DOI enrichment")
    parser.add_argument("--no-fuzzy", action="store_true", help="Skip fuzzy match and merge_map.json output")
    args = parser.parse_args()

    date_suffix = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"1_merge_bib_{date_suffix}.log"
    log = setup_logging(log_path)
    log.info("Merge bib script started")

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

    if args.apply_merge_map and args.apply_merge_map.exists():
        with args.apply_merge_map.open(encoding="utf-8") as f:
            merge_map = json.load(f)
        drop = set(merge_map.get("drop", []))
        merge_into = merge_map.get("merge_into", {})
        for key_b, key_a in merge_into.items():
            drop.add(key_b)
            if key_a in website and key_b in website:
                website[key_a] = merge_entry_fields(website.get(key_a, {}), website[key_b])
            if key_a in current and key_b in current:
                current[key_a] = merge_entry_fields(current.get(key_a, {}), current[key_b])
        for k in drop:
            website.pop(k, None)
            current.pop(k, None)
        log.info("Applied merge_map: dropped %d keys", len(drop))

    merged = dict(current)
    for key, entry in website.items():
        if key in merged:
            merged[key] = merge_entry_fields(merged[key], entry)
        else:
            merged[key] = entry

    log.info("Merged total unique keys: %d", len(merged))

    for key, entry in list(merged.items()):
        if "year" in entry:
            years = entry.get("year")
            if isinstance(years, list):
                entry["year"] = years[0] if years else ""
            elif isinstance(years, str) and years.count(",") >= 1:
                entry["year"] = years.split(",")[0].strip()

    if not args.no_fuzzy:
        close = fuzzy_close_matches(merged)
        if close:
            merge_map_out = {
                "description": "Review and apply with --apply-merge-map. merge_into: key_to_drop -> key_to_keep.",
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

    if not args.no_doi_enrich:
        try:
            from dotenv import load_dotenv
            load_dotenv(PROJECT_ROOT / ".env")
            import os
            mailto = os.environ.get("CROSSREF_MAILTO", "")
        except ImportError:
            mailto = ""
        added = 0
        for key, entry in list(merged.items()):
            if entry.get("doi"):
                continue
            title = (entry.get("title") or "").strip()[:150]
            author = (entry.get("author") or "").split(" and ")[0].strip()[:80]
            if not title:
                continue
            result = crossref_lookup(title, author, mailto or None)
            if result:
                doi = result.get("DOI")
                if doi:
                    entry["doi"] = doi
                    added += 1
                    log.debug("Added DOI for %s: %s", key, doi)
        log.info("CrossRef DOI enrichment: added %d DOIs", added)

    save_bib(merged, out_path)
    log.info("Wrote %s (%d entries)", out_path, len(merged))


if __name__ == "__main__":
    main()
