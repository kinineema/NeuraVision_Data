# NeuraVision_Data

NeuraVision_Data is the reproducible biomedical literature ingestion repository for the NeuraVision university minor project. It collects public PubMed metadata and abstracts, with an optional attempt to retrieve openly accessible PMC full text.

This repository is **data ingestion only**. It does not implement embeddings, indexing, retrieval, a knowledge graph, RAG, chunking, or LLM reasoning.

## Requirements and setup

Python 3.12 is recommended (Python 3.11 is also supported by the current scripts).

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Optional environment variables:

- `NCBI_EMAIL`: contact address included with requests to NCBI.
- `NCBI_API_KEY`: NCBI API key; the client paces requests at up to 10 requests/second when set, otherwise at up to 3 requests/second.

The client supplies a meaningful `tool` value, caches responses, and retries transient HTTP/network failures with exponential backoff.

## Stage 1: PubMed literature

Search concepts, category extensions, year range, target size, and output paths are in `config/literature_config.yaml`. The configured diseases are Alzheimer's disease, Parkinson's disease, epilepsy, multiple sclerosis, and stroke; the publication range is 2018–2026. Category counts can overlap because a paper can match multiple configured query extensions.

From this repository directory:

```powershell
python scripts/ingest_pubmed.py --config config/literature_config.yaml --dry-run
python scripts/validate_literature.py --config config/literature_config.yaml --target-per-disease 5
```

The dry run uses a target of five articles per disease unless overridden with `--target-per-disease`. To collect the configured target of 120 per disease:

```powershell
python scripts/ingest_pubmed.py --config config/literature_config.yaml
python scripts/fetch_pmc_fulltext.py --config config/literature_config.yaml
python scripts/normalize_articles.py --config config/literature_config.yaml
python scripts/build_catalog.py --config config/literature_config.yaml
python scripts/validate_literature.py --config config/literature_config.yaml
```

Use `--resume` with `ingest_pubmed.py` to reuse existing normalized records. Use `--disease alzheimers` to limit an ingestion run to one configured disease key. Use `--limit N` with the PMC fetcher to limit full-text attempts during development. Cached responses are reused automatically; remove the relevant cache files only when a fresh API response is required.

## Outputs

- `raw/literature/`: ESearch and EFetch XML responses grouped by disease or batch; PMC JATS responses.
- `processed/literature/articles.jsonl`: canonical normalized article records.
- `processed/literature/articles.csv`: flat export of normalized records.
- `processed/literature/article_text/<PMID>.txt`: PMC full-text text, when openly accessible.
- `metadata/literature_catalog.csv`: report-friendly catalog.
- `metadata/download_summary.json`: computed corpus and validation metrics.
- `metadata/DATASET_SUMMARY.md`: report-friendly methodology and measured counts.
- `metadata/duplicate_report.csv`: identifier/title duplicate merges and merged labels.
- `metadata/failed_records.jsonl`: failed searches, records, and full-text attempts.
- `cache/eutils/`: response cache (excluded from version control).

The outputs report measured counts only. PubMed abstracts that fail full-text retrieval remain in the dataset as abstract-only records.

## Ethical, licensing, and data handling

Only public bibliographic data and abstracts available from PubMed are collected. PMC full text is fetched only when the official PMC Article Dataset on AWS lists a non-retracted version in the Open Access Subset. The article's license code is retained with the full-text record. Do not bypass paywalls or disregard article-specific licenses; downstream reuse of PMC text must comply with the license attached to each article. PubMed metadata and abstracts have their own terms and availability limitations. Do not commit private patient data or credentials. `.env`, downloaded raw XML, cache files, and generated datasets are excluded from Git by default; review each source license before distributing derived text.

## Tests

The test suite uses embedded XML fixtures and does not make network requests:

```powershell
pytest
```
