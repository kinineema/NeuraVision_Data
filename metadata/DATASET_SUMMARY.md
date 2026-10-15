# NeuraVision Literature Dataset Summary

## Purpose

A reproducible, traceable biomedical-literature corpus for the NeuraVision neurological knowledge-discovery project.
This dataset repository performs literature acquisition and normalization only; it does not implement embeddings, retrieval, RAG, or LLM reasoning.

## Sources and collection methodology

Records were searched and fetched from PubMed using NCBI E-utilities ESearch and EFetch. Queries are defined in `config/literature_config.yaml`; each disease/category query is stratified by publication year within the configured range to avoid a recency-only sample. Search results are fetched in batches, parsed from PubMed XML, and filtered for title, abstract, supported year, and English language where language metadata is present. Article provenance retains query groups, full query expressions, retrieval timestamp, PMID URL, and database.
PMC full text is attempted only through NCBI's official PMC Article Dataset on AWS. The script checks article-version metadata and retrieves XML only for non-retracted Open Access Subset versions. JATS XML is parsed without including the references section; unavailable or non-open-access text is not bypassed, and its PubMed abstract remains available.

## Date range: 2018–2026

## Dataset distribution

- Unique articles: 385
- PMC full text: 190
- Abstract only: 195
- Duplicate records merged/removed: 7
- Failed records or requests: 198

### By disease

| Disease | Records |
|---|---:|
| Alzheimer's disease | 119 |
| Parkinson's disease | 136 |
| Epilepsy | 118 |
| Multiple sclerosis | 117 |
| Stroke | 119 |

### By category

| Category | Records |
|---|---:|
| Reviews/systematic reviews | 271 |
| Clinical/RCT studies | 96 |
| Imaging/diagnostic | 61 |
| Treatment/therapy | 64 |
| Biomarker/progression | 63 |

### By publication year

| Year | Records |
|---|---:|
| 2018 | 93 |
| 2019 | 103 |
| 2020 | 34 |
| 2021 | 24 |
| 2022 | 24 |
| 2023 | 20 |
| 2024 | 29 |
| 2025 | 24 |
| 2026 | 34 |

### By disease and publication year

| Disease | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | Total |
|---|---|---|---|---|---|---|---|---|---|---|
| Alzheimer's disease | 7 | 14 | 9 | 18 | 16 | 12 | 10 | 9 | 24 | 119 |
| Parkinson's disease | 6 | 16 | 6 | 20 | 20 | 14 | 16 | 13 | 25 | 136 |
| Epilepsy | 25 | 39 | 17 | 5 | 6 | 4 | 10 | 9 | 3 | 118 |
| Multiple sclerosis | 35 | 23 | 6 | 7 | 9 | 9 | 5 | 12 | 11 | 117 |
| Stroke | 30 | 34 | 11 | 4 | 2 | 6 | 13 | 9 | 10 | 119 |

### Year distribution quality flags

- No missing years or disease-year concentration above 40%.

## Duplicate handling

Records are matched in priority order by DOI, PMID, then normalized title. Disease/category labels, identifiers, publication types, keywords, MeSH terms, and author values are merged into the canonical record. The generated `duplicate_report.csv` records each merge and its match key.

## Limitations

Search results depend on PubMed indexing and configurable text/category terms; categories overlap and are query-derived rather than mutually exclusive. Full text is limited to non-retracted article versions in PMC's Open Access Subset available through the official PMC Article Dataset. Some records may have incomplete abstracts or metadata and are excluded when required title/abstract/year information is absent. Counts reflect the data collected at run time and are not estimates of the full literature.

## Reproducibility

Create a Python environment, install `requirements.txt`, optionally set `NCBI_EMAIL` and `NCBI_API_KEY`, then run `python scripts/ingest_pubmed.py --config config/literature_config.yaml`. For a small smoke build, add `--dry-run`; follow with `python scripts/fetch_pmc_fulltext.py`, `python scripts/normalize_articles.py`, `python scripts/build_catalog.py`, and `python scripts/validate_literature.py`. API responses are cached under `cache/eutils`; ESearch and EFetch XML are retained under `raw/literature`. The cache and raw snapshots can be removed to force a fresh collection.

Validation result: **PASS**. Disease targets met: **yes**.
