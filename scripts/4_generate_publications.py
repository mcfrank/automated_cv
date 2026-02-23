#!/usr/bin/env python3
"""
Generate tex/publications.tex: standalone LaTeX stub with Publications section
(Books, Peer-Reviewed Journal Articles, Chapters/Commentaries/Other, Conference Proceedings)
sorted by year. Filter: author contains Frank. Config: publications_config.json for manual lines and overrides.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIB_DIR = PROJECT_ROOT / "bib"
TEX_DIR = PROJECT_ROOT / "tex"
LOGS_DIR = PROJECT_ROOT / "logs"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SECTION_BOOKS = "Books"
SECTION_JOURNAL = "Peer-Reviewed Journal Articles"
SECTION_CHAPTERS = "Chapters, Commentaries, and other Manuscripts"
SECTION_PROCEEDINGS = "Peer-Reviewed Conference Proceedings"

TYPE_TO_SECTION = {
    "book": SECTION_BOOKS,
    "article": SECTION_JOURNAL,
    "incollection": SECTION_CHAPTERS,
    "inbook": SECTION_CHAPTERS,
    "inproceedings": SECTION_PROCEEDINGS,
    "conference": SECTION_PROCEEDINGS,
    "misc": SECTION_PROCEEDINGS,
    "unpublished": SECTION_CHAPTERS,
}


def setup_logging(log_path: Path) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("generate_publications")
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


def is_my_work(entry: dict, author_substring: str = "Frank") -> bool:
    author = (entry.get("author") or "").strip()
    return author_substring.lower() in author.lower()


def sort_key_for_entry(entry: dict) -> tuple:
    from scripts import bib_utils
    y = bib_utils.normalize_year(entry.get("year"))
    if y is None:
        y = 0
    if y >= 9999:
        year_key = (0, 0)
    else:
        year_key = (-y, 1)
    author = (entry.get("author") or "").strip()
    title = (entry.get("title") or "").strip()
    return (year_key[0], year_key[1], author.lower(), title.lower())


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate tex/publications.tex stub")
    parser.add_argument("--bib", type=Path, default=None, help="Path to merged bib (default: bib/citations.bib)")
    parser.add_argument("--config", type=Path, default=None, help="Path to publications_config.json")
    args = parser.parse_args()

    date_suffix = datetime.now().strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"4_generate_publications_{date_suffix}.log"
    log = setup_logging(log_path)
    log.info("Generate publications started")

    bib_path = args.bib or BIB_DIR / "citations.bib"
    if not bib_path.exists():
        bib_path = BIB_DIR / "current_citations.bib"
    if not bib_path.exists():
        log.error("No bib file found")
        sys.exit(1)

    config_path = args.config or PROJECT_ROOT / "publications_config.json"
    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    manual_lines = config.get("manual_lines") or {}
    section_overrides = config.get("section_overrides") or {}

    from scripts import bib_utils

    entries = bib_utils.load_bib(bib_path)
    my_entries = {k: v for k, v in entries.items() if is_my_work(v)}
    log.info("Total entries %d; my works %d", len(entries), len(my_entries))

    sections = {
        SECTION_BOOKS: [],
        SECTION_JOURNAL: [],
        SECTION_CHAPTERS: [],
        SECTION_PROCEEDINGS: [],
    }
    for key, entry in my_entries.items():
        section = section_overrides.get(key)
        if not section:
            section = TYPE_TO_SECTION.get(entry.get("ENTRYTYPE", "misc"), SECTION_CHAPTERS)
            if entry.get("ENTRYTYPE") == "misc" and entry.get("booktitle"):
                section = SECTION_PROCEEDINGS
        if section not in sections:
            sections[section] = []
        sections[section].append((key, entry))

    for sec in sections:
        sections[sec].sort(key=lambda x: sort_key_for_entry(x[1]))

    out_path = TEX_DIR / "publications.tex"
    TEX_DIR.mkdir(parents=True, exist_ok=True)
    bib_resource = "citations.bib"

    preamble = r"""\documentclass{article}
\usepackage{hyperref}
\usepackage{geometry}
\usepackage{enumitem}
\usepackage[backend=biber, style=apa, url=false, doi=false]{biblatex}
\addbibresource{""" + bib_resource + r"""}

\geometry{body={6.5in, 8.5in}, left=1.0in, top=1.25in}
\setlength\parindent{0em}

\begin{document}
\section*{Publications}
"""

    body_parts = []
    body_parts.append(r"\subsection*{" + SECTION_BOOKS + "}\n")
    body_parts.append(r"\begin{enumerate}" + "\n")
    if SECTION_BOOKS in manual_lines:
        for line in manual_lines[SECTION_BOOKS]:
            body_parts.append("  " + line.strip() + "\n")
    for key, _ in sections[SECTION_BOOKS]:
        body_parts.append(f"  \\item \\fullcite{{{key}}}.\n")
    body_parts.append(r"\end{enumerate}" + "\n\n")

    body_parts.append(r"\subsection*{" + SECTION_JOURNAL + "}\n")
    body_parts.append(r"\begin{enumerate}[resume]" + "\n")
    if SECTION_JOURNAL in manual_lines:
        for line in manual_lines[SECTION_JOURNAL]:
            body_parts.append("  " + line.strip() + "\n")
    for key, _ in sections[SECTION_JOURNAL]:
        body_parts.append(f"  \\item \\fullcite{{{key}}}.\n")
    body_parts.append(r"\end{enumerate}" + "\n\n")

    body_parts.append(r"\subsection*{" + SECTION_CHAPTERS + "}\n")
    body_parts.append(r"\begin{enumerate}[resume]" + "\n")
    if SECTION_CHAPTERS in manual_lines:
        for line in manual_lines[SECTION_CHAPTERS]:
            body_parts.append("  " + line.strip() + "\n")
    for key, _ in sections[SECTION_CHAPTERS]:
        body_parts.append(f"  \\item \\fullcite{{{key}}}.\n")
    body_parts.append(r"\end{enumerate}" + "\n\n")

    body_parts.append(r"\subsection*{" + SECTION_PROCEEDINGS + "}\n")
    body_parts.append(r"\begin{enumerate}[resume]" + "\n")
    if SECTION_PROCEEDINGS in manual_lines:
        for line in manual_lines[SECTION_PROCEEDINGS]:
            body_parts.append("  " + line.strip() + "\n")
    for key, _ in sections[SECTION_PROCEEDINGS]:
        body_parts.append(f"  \\item \\fullcite{{{key}}}.\n")
    body_parts.append(r"\end{enumerate}" + "\n\n")

    full_tex = preamble + "\n".join(body_parts) + r"\end{document}" + "\n"
    out_path.write_text(full_tex, encoding="utf-8")
    log.info("Wrote %s (%d Books, %d Journal, %d Chapters, %d Proceedings)",
             out_path,
             len(sections[SECTION_BOOKS]),
             len(sections[SECTION_JOURNAL]),
             len(sections[SECTION_CHAPTERS]),
             len(sections[SECTION_PROCEEDINGS]))


if __name__ == "__main__":
    main()
