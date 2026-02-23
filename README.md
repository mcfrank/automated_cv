# CV Bibliography Merge and Automation

Python scripts to maintain an academic CV in LaTeX: merge two BibTeX files, validate citations, check completeness (including CogSci proceedings via eScholarship), and generate the Publications section as a standalone LaTeX stub for Overleaf.

## Layout

- **bib/** — `current_citations.bib`, `website_citations.bib` (inputs); `citations.bib` (merged output)
- **tex/** — `main.tex` (full CV); `publications.tex` (generated stub for pasting into Overleaf)
- **scripts/** — `1_merge_bib.py`, `2_validate_citations.py`, `3_check_completeness.py`, `4_generate_publications.py`, `merge_map_ui.py`, `bib_utils.py`
- **logs/** — Dated log and report files (e.g. `1_merge_bib_2025-02-23.log`)
- **merge_map.json** — Output of close-match pairs for review; apply with `--apply-merge-map`
- **publications_config.json** — Manual LaTeX lines and section overrides for publication generation
- **.secrets** — Optional: ORCID API credentials (JSON with `ORCID_CLIENT_ID`, `ORCID_CLIENT_SECRET`). Never commit. See [ORCID API tutorial](https://info.orcid.org/documentation/api-tutorials/api-tutorial-read-data-on-a-record/).

## Prerequisites

- Python 3.9+
- Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Optional: create a `.env` file with `CROSSREF_MAILTO=your@email` for polite CrossRef API use.

## Running the scripts

Run from the **project root** (the directory containing `bib/`, `tex/`, `scripts/`).

1. **Merge bib files and output close-match map**
   ```bash
   python scripts/1_merge_bib.py
   ```
   - Reads `bib/current_citations.bib` and `bib/website_citations.bib`
   - Writes `bib/citations.bib` and `merge_map.json` (fuzzy matches on title + first author)
   - Optionally enriches DOIs via CrossRef (disable with `--no-doi-enrich`)
   - To apply reviewed merges: edit `merge_map.json` (or use the Streamlit UI below) then run:
   ```bash
   python scripts/1_merge_bib.py --apply-merge-map merge_map.json
   ```

   **Review merge map in a browser (optional)**  
   Run the Streamlit UI to approve or skip each suggested pair, then save only approved merges to `merge_map.json`:
   ```bash
   streamlit run scripts/merge_map_ui.py
   ```
   Open the URL shown (e.g. http://localhost:8501). For each pair click **Merge** or **Don't merge**, then **Save merge map (approved only)** in the sidebar. Re-run step 1 with `--apply-merge-map merge_map.json`.

2. **Validate citations and discover missing works**
   ```bash
   python scripts/2_validate_citations.py
   ```
   - Reports citekeys in `tex/main.tex` missing from the bib, and uncited keys
   - Optionally queries CrossRef, ORCID (if `.secrets` has `ORCID_CLIENT_ID` and `ORCID_CLIENT_SECRET`), and arXiv for possible missing papers (`--no-discovery` to skip)

3. **Check completeness and enrich**
   ```bash
   python scripts/3_check_completeness.py --write
   ```
   - Checks required fields per entry type, normalizes DOIs, flags duplicate years
   - Can add DOIs via CrossRef (disable with `--no-doi-enrich`)
   - Enriches *Proceedings of the Cognitive Science Society* entries using the [eScholarship API](https://help.escholarship.org/support/solutions/articles/9000147730-escholarship-public-read-api) (`--no-escholarship` to skip)
   - Use `--write` to update `bib/citations.bib`

4. **Generate Publications section stub**
   ```bash
   python scripts/4_generate_publications.py
   ```
   - Reads `bib/citations.bib` and optional `publications_config.json`
   - Writes `tex/publications.tex`: minimal document with `\section*{Publications}` and subsections (Books, Journal Articles, Chapters, Conference Proceedings), sorted by year
   - Filter: entries whose author list contains "Frank"
   - Paste `tex/publications.tex` into Overleaf (and add your `citations.bib` to the project) to render

## Re-running next year

1. Update `bib/current_citations.bib` and/or `bib/website_citations.bib` with new entries.
2. Run scripts 1 → 2 → 3 → 4 in order.
3. Review `merge_map.json`; if you approve merges, run step 1 again with `--apply-merge-map merge_map.json`.
4. Copy `tex/publications.tex` into your main CV or Overleaf and rebuild the PDF.

## APIs used

| Purpose            | API / tool      | Notes                                                                 |
|--------------------|-----------------|-----------------------------------------------------------------------|
| DOI lookup         | CrossRef        | Free; optional `CROSSREF_MAILTO` in `.env`                            |
| Discovery          | ORCID Public API | ORCID iD 0000-0002-7551-4378; credentials in `.secrets` (CLIENT_ID, CLIENT_SECRET) |
| Preprints          | arXiv API       | [API basics](https://info.arxiv.org/help/api/basics.html)             |
| CogSci proceedings | eScholarship    | [Public Read API](https://help.escholarship.org/support/solutions/articles/9000147730-escholarship-public-read-api) (GraphQL) |

Scopus can be added later if you have an API key; document it in this README.
