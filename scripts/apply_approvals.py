#!/usr/bin/env python3
"""
Apply approved merge_map.json and approved_additions.json to produce bib/citations.bib.
Load current_citations.bib and website_citations.bib, apply merge map (drop/merge keys),
merge the two bibs, add all approved new entries, write citations.bib.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIB_DIR = PROJECT_ROOT / "bib"
LOGS_DIR = PROJECT_ROOT / "logs"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def apply_merge_map(
    current: dict[str, dict],
    website: dict[str, dict],
    merge_map: dict,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Apply merge_map to current and website in place; return (current, website) with keys dropped/merged."""
    drop = set(merge_map.get("drop", []))
    merge_into = merge_map.get("merge_into", {})
    from scripts import bib_utils
    merge_entry_fields = bib_utils.merge_entry_fields
    for key_b, key_a in merge_into.items():
        drop.add(key_b)
        if key_a in website and key_b in website:
            website[key_a] = merge_entry_fields(website.get(key_a, {}), website[key_b])
        if key_a in current and key_b in current:
            current[key_a] = merge_entry_fields(current.get(key_a, {}), current[key_b])
    for k in drop:
        website.pop(k, None)
        current.pop(k, None)
    return current, website


def run_apply(
    merge_map_path: Path,
    approved_path: Path,
    out_path: Path,
    current_path: Path,
    website_path: Path,
    dry_run: bool = False,
) -> dict[str, dict]:
    """Load current + website, apply merge_map and approved_additions, return merged dict. Optionally write out_path."""
    from scripts import bib_utils
    load_bib = bib_utils.load_bib
    save_bib = bib_utils.save_bib
    merge_entry_fields = bib_utils.merge_entry_fields

    current = load_bib(current_path)
    website = load_bib(website_path)

    if merge_map_path.exists():
        with merge_map_path.open(encoding="utf-8") as f:
            merge_map = json.load(f)
        apply_merge_map(current, website, merge_map)

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

    if approved_path.exists():
        try:
            data = json.loads(approved_path.read_text(encoding="utf-8"))
        except Exception:
            data = []
    else:
        data = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                citekey = item.get("citekey")
                entry = item.get("entry") or item.get("bib_entry")
                if citekey and entry:
                    merged[citekey] = dict(entry)
                    if "ID" not in merged[citekey]:
                        merged[citekey]["ID"] = citekey
                    if "ENTRYTYPE" not in merged[citekey]:
                        merged[citekey]["ENTRYTYPE"] = "article"

    if not dry_run and out_path:
        save_bib(merged, out_path)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply merge_map and approved_additions to write citations.bib")
    parser.add_argument("--merge-map", type=Path, default=None, help="Path to merge_map.json (default: project root)")
    parser.add_argument("--approved-additions", type=Path, default=None, help="Path to approved_additions.json (default: project root)")
    parser.add_argument("--dry-run", action="store_true", help="Do not write citations.bib")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("apply_approvals")

    current_path = BIB_DIR / "current_citations.bib"
    website_path = BIB_DIR / "website_citations.bib"
    out_path = BIB_DIR / "citations.bib"
    merge_map_path = args.merge_map or PROJECT_ROOT / "merge_map.json"
    approved_path = args.approved_additions or PROJECT_ROOT / "approved_additions.json"

    if not current_path.exists():
        log.error("Missing %s", current_path)
        sys.exit(1)
    if not website_path.exists():
        log.error("Missing %s", website_path)
        sys.exit(1)

    merged = run_apply(
        merge_map_path=merge_map_path,
        approved_path=approved_path,
        out_path=out_path,
        current_path=current_path,
        website_path=website_path,
        dry_run=args.dry_run,
    )
    log.info("Total entries: %d", len(merged))
    if not args.dry_run:
        log.info("Wrote %s", out_path)


if __name__ == "__main__":
    main()
