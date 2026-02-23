"""
Shared utilities for loading/saving BibTeX and parsing LaTeX for citekeys.
"""
from pathlib import Path
import re
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter


def load_bib(path: Path) -> dict[str, dict]:
    """Load a .bib file and return a dict of citekey -> entry (fields only, no type)."""
    path = Path(path)
    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    with path.open(encoding="utf-8") as f:
        db = bibtexparser.load(f, parser=parser)
    result = {}
    for entry in db.entries:
        key = entry.get("ID") or entry.pop("id", None)
        if not key:
            continue
        entry_type = entry.get("ENTRYTYPE", "misc")
        result[key] = {"ENTRYTYPE": entry_type, **{k: v for k, v in entry.items() if k not in ("ID", "ENTRYTYPE")}}
        result[key]["ID"] = key
    return result


def save_bib(entries: dict[str, dict], path: Path) -> None:
    """Write a dict of citekey -> entry to a .bib file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = bibtexparser.bibdatabase.BibDatabase()
    db.entries = []
    for key, fields in entries.items():
        entry = {"ID": key, "ENTRYTYPE": fields.get("ENTRYTYPE", "misc")}
        for k, v in fields.items():
            if k in ("ID", "ENTRYTYPE"):
                continue
            if v is not None and str(v).strip():
                entry[k] = str(v)
        db.entries.append(entry)
    writer = BibTexWriter()
    writer.indent = "  "
    writer.order_entries_by = None
    with path.open("w", encoding="utf-8") as f:
        bibtexparser.dump(db, f, writer=writer)


def parse_fullcites(tex_path: Path) -> set[str]:
    """Extract all \\fullcite{key} citekeys from a .tex file."""
    tex_path = Path(tex_path)
    text = tex_path.read_text(encoding="utf-8")
    return set(re.findall(r"\\fullcite\{([^}]+)\}", text))


def get_title(entry: dict) -> str:
    """Get normalized title for comparison (strip braces, lower)."""
    t = entry.get("title") or ""
    t = re.sub(r"[\{\}]", "", t)
    return t.strip().lower()


def get_first_author(entry: dict) -> str:
    """Get first author last name for comparison."""
    author = entry.get("author") or ""
    if " and " in author:
        author = author.split(" and ")[0]
    author = author.strip()
    if "," in author:
        return author.split(",")[0].strip().lower()
    parts = author.split()
    return (parts[-1] if parts else "").lower()


def normalize_year(year_val) -> int | None:
    """Convert year field to sortable int. None for unknown; 9999 for in press/under review."""
    if year_val is None:
        return None
    s = str(year_val).strip().lower()
    if s in ("in press", "under review", "accepted", "submitted"):
        return 9999
    m = re.match(r"(\d{4})", s)
    if m:
        return int(m.group(1))
    return None


def merge_entry_fields(a: dict, b: dict) -> dict:
    """Merge two entries: non-empty values from either; prefer DOI if present in either."""
    merged = dict(a)
    for k, v in b.items():
        if k in ("ID", "ENTRYTYPE"):
            continue
        if not v or not str(v).strip():
            continue
        if k not in merged or not str(merged.get(k) or "").strip():
            merged[k] = v
        elif k == "doi" and (v and str(v).strip()):
            merged[k] = v
    return merged


def normalize_doi(doi: str) -> str:
    """Strip https://doi.org/ prefix to 10.xxxx/yyyy form."""
    if not doi:
        return ""
    s = str(doi).strip()
    for prefix in ("https://doi.org/", "http://doi.org/"):
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
    return s.strip()
