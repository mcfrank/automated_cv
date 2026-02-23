#!/usr/bin/env python3
"""
Validate citations: citekeys in tex vs bib; report missing/uncited; suggest missing works via CrossRef, ORCID, arXiv.
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


def crossref_author_works(author: str, mailto: str | None, max_results: int = 100) -> list[dict]:
    import requests
    url = "https://api.crossref.org/works"
    params = {"query.author": author, "rows": min(max_results, 100)}
    headers = {"User-Agent": f"CV-validate/1.0 (mailto:{mailto or 'anonymous@example.com'})"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        return data.get("message", {}).get("items") or []
    except Exception:
        return []


def orcid_works(orcid_id: str, client_id: str, client_secret: str) -> list[dict]:
    import requests
    token_url = "https://orcid.org/oauth/token"
    token_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "/read-public",
    }
    try:
        tr = requests.post(token_url, data=token_data, headers={"Accept": "application/json"}, timeout=15)
        tr.raise_for_status()
        token = tr.json().get("access_token")
        if not token:
            return []
        api_url = f"https://api.orcid.org/v3.0/{orcid_id}/works"
        ar = requests.get(api_url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=15)
        ar.raise_for_status()
        data = ar.json()
        return data.get("group") or []
    except Exception:
        return []


def arxiv_author_search(author: str, max_results: int = 50) -> list[dict]:
    import xml.etree.ElementTree as ET
    import requests
    q = f"au:{author.replace(' ', '_')}"
    url = "http://export.arxiv.org/api/query"
    params = {"search_query": q, "start": 0, "max_results": max_results}
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        entries = []
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""
            id_el = entry.find("atom:id", ns)
            arxiv_id = id_el.text.strip() if id_el is not None and id_el.text else ""
            published = entry.find("atom:published", ns)
            year = published.text[:4] if published is not None and published.text else ""
            authors = [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns) if a.find("atom:name", ns) is not None]
            entries.append({"title": title, "arxiv_id": arxiv_id, "year": year, "authors": authors})
        return entries
    except Exception:
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
    parser.add_argument("--no-discovery", action="store_true", help="Skip CrossRef/ORCID/arXiv discovery")
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
        mailto = ""
        try:
            from dotenv import load_dotenv
            load_dotenv(PROJECT_ROOT / ".env")
            mailto = os.environ.get("CROSSREF_MAILTO", "")
        except ImportError:
            pass

        log.info("Querying CrossRef for author Frank, Michael C...")
        time.sleep(0.5)
        cr_works = crossref_author_works("Frank, Michael C", mailto, max_results=80)
        for w in cr_works:
            title = (w.get("title") or [""])[0]
            doi = w.get("DOI")
            year = (w.get("published-print") or w.get("published-online") or {}).get("date-parts", [[]])[0][:1]
            year_str = str(year[0]) if year else ""
            if not title:
                continue
            found = False
            for k, e in entries.items():
                if e.get("doi") == doi or title_similar(e.get("title") or "", title) > 0.9:
                    found = True
                    break
            if not found:
                possible_missing.append({"source": "CrossRef", "title": title[:100], "doi": doi, "year": year_str})
        log.info("CrossRef: %d works, %d possible missing", len(cr_works), len([p for p in possible_missing if p["source"] == "CrossRef"]))

        orcid_id = "0000-0002-7551-4378"
        secrets_path = PROJECT_ROOT / ".secrets"
        if secrets_path.exists():
            try:
                secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
                cid = secrets.get("ORCID_CLIENT_ID") or secrets.get("orcid_client_id")
                csec = secrets.get("ORCID_CLIENT_SECRET") or secrets.get("orcid_client_secret")
                if cid and csec:
                    log.info("Querying ORCID for %s...", orcid_id)
                    orcid_groups = orcid_works(orcid_id, cid, csec)
                    for g in orcid_groups:
                        work_summary = (g.get("work-summary") or [])
                        for ws in work_summary[:5]:
                            title_el = (ws.get("title") or {}).get("title") or {}
                            title = (title_el.get("value") or "") if isinstance(title_el, dict) else str(title_el)
                            if not title:
                                continue
                            ext_ids = (ws.get("external-ids") or {}).get("external-id") or []
                            doi = None
                            for ext in ext_ids:
                                if (ext.get("external-id-type") or "").lower() == "doi":
                                    doi = ext.get("external-id-value")
                                    break
                            found = any(
                                title_similar(e.get("title") or "", title) > 0.9 or e.get("doi") == doi
                                for e in entries.values()
                            )
                            if not found:
                                possible_missing.append({"source": "ORCID", "title": title[:100], "doi": doi, "year": ""})
                    log.info("ORCID: %d group(s)", len(orcid_groups))
                else:
                    log.info("ORCID: no client credentials in .secrets")
            except Exception as e:
                log.debug("ORCID error: %s", e)
        else:
            log.info("ORCID: no .secrets file")

        log.info("Querying arXiv for au:Frank_Michael...")
        time.sleep(0.5)
        arxiv_works = arxiv_author_search("Frank_Michael", max_results=30)
        for w in arxiv_works:
            title = w.get("title") or ""
            if not title:
                continue
            found = any(title_similar(e.get("title") or "", title) > 0.85 for e in entries.values())
            if not found:
                possible_missing.append({
                    "source": "arXiv",
                    "title": title[:100],
                    "arxiv_id": w.get("arxiv_id"),
                    "year": w.get("year") or "",
                })
        log.info("arXiv: %d works, %d possible missing", len(arxiv_works), len([p for p in possible_missing if p.get("source") == "arXiv"]))

    report_lines.append("")
    report_lines.append("## Possible missing papers (for manual review)")
    report_lines.append("")
    for p in possible_missing[:40]:
        report_lines.append(f"  [{p.get('source', '')}] {p.get('title', '')} (doi={p.get('doi')}, year={p.get('year')}, arxiv={p.get('arxiv_id')})")

    report_text = "\n".join(report_lines)
    log.info("Report:\n%s", report_text)
    report_path = LOGS_DIR / f"2_validate_citations_report_{date_suffix}.txt"
    report_path.write_text(report_text, encoding="utf-8")
    log.info("Wrote report to %s", report_path)


if __name__ == "__main__":
    main()
