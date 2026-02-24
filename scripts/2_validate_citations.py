#!/usr/bin/env python3
"""
DEPRECATED: Use the refactored pipeline instead:
  1. python scripts/1_gather_candidates.py   (includes ORCID/arXiv discovery → proposed list)
  2. python scripts/2_check_completeness_enrich.py --write  (optional citekey report in report file)
  3. streamlit run scripts/approve_citations_ui.py

This script is kept for backwards compatibility. It validates citekeys and discovers
possible missing works via ORCID and arXiv.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIB_DIR = PROJECT_ROOT / "bib"
TEX_DIR = PROJECT_ROOT / "tex"
LOGS_DIR = PROJECT_ROOT / "logs"
# Local ORCID export: orcid_xml/works/*.xml or orcid/works/*.xml
ORCID_XML_WORKS_DIR = PROJECT_ROOT / "orcid_xml" / "works"
if not ORCID_XML_WORKS_DIR.exists():
    ORCID_XML_WORKS_DIR = PROJECT_ROOT / "orcid" / "works"
ORCID_WORK_NS = "http://www.orcid.org/ns/work"
ORCID_COMMON_NS = "http://www.orcid.org/ns/common"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def setup_logging(log_path: Path) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("validate_citations")
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


def orcid_local_works(works_dir: Path, log: logging.Logger | None = None) -> list[dict]:
    """Parse ORCID work XML files from a directory (e.g. orcid_xml/works). Returns list of {title, doi, year, author, journal}."""
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


def _orcid_get_works_groups(data: dict) -> list[dict]:
    """Extract work groups from ORCID API response. Handles both /works and /record shapes."""
    # Direct /works response: { "group": [ ... ] }
    groups = data.get("group") or data.get("groups")
    if groups is not None:
        return groups if isinstance(groups, list) else []
    # Full /record response: record.activities-summary.works.group (hyphens or underscores)
    activities = (data.get("activities-summary") or data.get("activities_summary") or {})
    works = activities.get("works") or {}
    groups = works.get("group") or works.get("groups")
    if groups is not None:
        return groups if isinstance(groups, list) else []
    return []


def orcid_works(
    orcid_id: str,
    client_id: str,
    client_secret: str,
    log: logging.Logger | None = None,
    use_sandbox: bool = False,
) -> list[dict]:
    """Fetch works from ORCID. Uses /read-public token; tries /works then /record if 403.
    Credentials must match the environment: production (orcid.org) vs sandbox (sandbox.orcid.org).
    Set use_sandbox=True if your client ID/secret were registered at sandbox.orcid.org/developer-tools.
    """
    import requests
    logger = log or logging.getLogger(__name__)
    if use_sandbox:
        token_url = "https://sandbox.orcid.org/oauth/token"
        api_base = "https://api.sandbox.orcid.org"
        logger.info("ORCID: using sandbox (sandbox.orcid.org)")
    else:
        token_url = "https://orcid.org/oauth/token"
        api_base = "https://api.orcid.org"
        logger.info("ORCID: using production (orcid.org)")
    token_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "/read-public",
    }
    accept = "application/vnd.orcid+json"
    try:
        tr = requests.post(token_url, data=token_data, headers={"Accept": "application/json"}, timeout=15)
        tr.raise_for_status()
        token = tr.json().get("access_token")
        if not token:
            logger.warning("ORCID: no access_token in token response (check client ID/secret)")
            return []
        auth_headers = {"Authorization": f"Bearer {token}", "Accept": accept}
        works_url = f"{api_base}/v3.0/{orcid_id}/works"
        ar = requests.get(works_url, headers=auth_headers, timeout=15)
        if ar.status_code == 200:
            data = ar.json()
            groups = _orcid_get_works_groups(data)
            return groups
        if ar.status_code == 403:
            logger.info("ORCID /works returned 403; trying /record")
            record_url = f"{api_base}/v3.0/{orcid_id}/record"
            rec = requests.get(record_url, headers=auth_headers, timeout=15)
            if rec.status_code == 403:
                logger.warning(
                    "ORCID 403 Forbidden on both /works and /record. "
                    "ORCID docs: 'Integrators using the member API can use the /read-public scope to read ORCID record summaries.' "
                    "Reading records may require Member API; use --no-discovery to skip ORCID discovery."
                )
                return []
            rec.raise_for_status()
            data = rec.json()
            groups = _orcid_get_works_groups(data)
            return groups
        ar.raise_for_status()
        return []
    except requests.HTTPError as e:
        logger.warning("ORCID request failed: %s", e)
        return []
    except Exception as e:
        logger.warning("ORCID error: %s", e)
        return []


ATOM_NS = "http://www.w3.org/2005/Atom"


def arxiv_author_search(max_results: int = 50, log: logging.Logger | None = None) -> list[dict]:
    """Search arXiv for author Michael C. Frank. API is case-sensitive: use au:michael_c_frank (lowercase)."""
    import xml.etree.ElementTree as ET
    import requests
    logger = log or logging.getLogger(__name__)
    # Case-sensitive: au:michael_c_frank returns results; au:Michael_C_Frank returns 0
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
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate citations and suggest missing works")
    parser.add_argument("--no-discovery", action="store_true", help="Skip ORCID/arXiv discovery")
    args = parser.parse_args()

    date_suffix = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"2_validate_citations_{date_suffix}.log"
    log = setup_logging(log_path)
    log.info("Validate citations started")

    tex_path = TEX_DIR / "main.tex"
    bib_path = BIB_DIR / "citations.bib"
    if not bib_path.exists():
        bib_path = BIB_DIR / "current_citations.bib"
    if not tex_path.exists():
        log.error("Missing %s", tex_path)
        sys.exit(1)
    if not bib_path.exists():
        log.error("Missing bib file")
        sys.exit(1)

    from scripts import bib_utils

    cited = bib_utils.parse_fullcites(tex_path)
    entries = bib_utils.load_bib(bib_path)
    bib_keys = set(entries.keys())

    missing = cited - bib_keys
    uncited = bib_keys - cited

    log.info("Cited in tex: %d; in bib: %d", len(cited), len(bib_keys))
    if missing:
        log.warning("Missing from bib (%d): %s", len(missing), sorted(missing))
    if uncited:
        log.info("Uncited in tex (%d): %s", len(uncited), sorted(uncited)[:30])

    report_lines = [
        "# Citation validation report",
        f"Missing citekeys (in tex but not in bib): {len(missing)}",
        "" if not missing else "\n".join(f"  - {k}" for k in sorted(missing)),
        "",
        f"Uncited keys (in bib but not in tex): {len(uncited)}",
        "" if not uncited else "\n".join(f"  - {k}" for k in sorted(uncited)[:50]),
    ]
    if len(uncited) > 50:
        report_lines.append(f"  ... and {len(uncited) - 50} more")

    possible_missing = []
    if not args.no_discovery:
        if ORCID_XML_WORKS_DIR.exists():
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
                    for e in entries.values()
                )
                if not found:
                    possible_missing.append({
                        "source": "ORCID",
                        "title": title,
                        "doi": doi,
                        "year": year,
                        "author": w.get("author"),
                        "journal": w.get("journal"),
                    })
            log.info("ORCID: %d works from local XML, %d possible missing", len(orcid_works_list), len([p for p in possible_missing if p.get("source") == "ORCID"]))
        else:
            log.info("ORCID: no orcid_xml/works (or orcid/works) directory, skipping ORCID discovery")

        log.info("Querying arXiv: au:michael_c_frank (lowercase), size=50")
        time.sleep(0.5)
        arxiv_works = arxiv_author_search(max_results=50, log=log)
        for w in arxiv_works:
            title = w.get("title") or ""
            if not title:
                continue
            found = any(title_similar(e.get("title") or "", title) > 0.85 for e in entries.values())
            if not found:
                possible_missing.append({
                    "source": "arXiv",
                    "title": title,
                    "arxiv_id": w.get("arxiv_id"),
                    "year": w.get("year") or "",
                })
        log.info("arXiv: %d works, %d possible missing", len(arxiv_works), len([p for p in possible_missing if p.get("source") == "arXiv"]))

    report_lines.append("")
    report_lines.append("## Possible missing papers (for manual review)")
    report_lines.append("")
    for p in possible_missing[:40]:
        report_lines.append(f"  [{p.get('source', '')}] {(p.get('title') or '')[:100]} (doi={p.get('doi')}, year={p.get('year')}, arxiv={p.get('arxiv_id')})")

    report_text = "\n".join(report_lines)
    log.info("Report:\n%s", report_text)
    report_path = LOGS_DIR / f"2_validate_citations_report_{date_suffix}.txt"
    report_path.write_text(report_text, encoding="utf-8")
    log.info("Wrote report to %s", report_path)

    possible_missing_path = LOGS_DIR / f"2_validate_citations_possible_missing_{date_suffix}.json"
    possible_missing_path.write_text(json.dumps(possible_missing, indent=2), encoding="utf-8")
    log.info("Wrote possible missing list to %s", possible_missing_path)


if __name__ == "__main__":
    main()
