"""Validate corpus integrity and write computed coverage statistics."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

if __package__:
    from .literature_utils import load_config, normalized_title, read_jsonl
else:
    from literature_utils import load_config, normalized_title, read_jsonl


def validate_articles(
    articles: list[dict[str, Any]],
    config: dict[str, Any],
    failed_count: int = 0,
    target_override: int | None = None,
) -> dict[str, Any]:
    """Compute exact corpus statistics and structural validation results."""

    diseases = list(config["diseases"])
    categories = list(config["categories"])

    disease_counts = Counter(
        tag
        for article in articles
        for tag in set(article.get("disease_tags") or [])
    )

    category_counts = Counter(
        tag
        for article in articles
        for tag in set(article.get("category_tags") or [])
    )

    year_counts = Counter(
        str(article["publication_year"])
        for article in articles
        if article.get("publication_year") is not None
    )

    year_disease_counts = {
        disease: Counter(
            str(article["publication_year"])
            for article in articles
            if disease in article.get("disease_tags", [])
            and article.get("publication_year") is not None
        )
        for disease in diseases
    }

    pmids = [
        str(article["pmid"])
        for article in articles
        if article.get("pmid")
    ]

    dois = [
        str(article["doi"]).strip().lower()
        for article in articles
        if article.get("doi")
    ]

    titles = [
        normalized_title(article.get("title"))
        for article in articles
        if article.get("title")
    ]

    duplicate_pmids = len(pmids) - len(set(pmids))
    duplicate_dois = len(dois) - len(set(dois))
    duplicate_titles = len(titles) - len(set(titles))

    missing_title = sum(
        not article.get("title")
        for article in articles
    )

    missing_abstract = sum(
        not article.get("abstract")
        for article in articles
    )

    malformed = sum(
        not article.get("article_id")
        or not article.get("pmid")
        or not isinstance(article.get("authors"), list)
        or not isinstance(article.get("disease_tags"), list)
        or not isinstance(article.get("category_tags"), list)
        for article in articles
    )

    non_english = sum(
        bool(article.get("language") and article["language"] != "eng")
        for article in articles
    )

    invalid_year = sum(
        not _year_is_valid(article.get("publication_year"), config)
        for article in articles
    )

    full_text_count = sum(
        bool(article.get("full_text"))
        for article in articles
    )

    target = (
        target_override
        if target_override is not None
        else int(config.get("target_per_disease", 120))
    )

    disease_coverage = {
        disease: disease_counts.get(disease, 0)
        for disease in diseases
    }

    quotas = {
        disease: {
            category: round(
                target
                * float(
                    config["categories"][category]["target_share"]
                )
            )
            for category in categories
        }
        for disease in diseases
    }

    category_balance = {
        disease: {
            category: sum(
                category in article.get("category_tags", [])
                and disease in article.get("disease_tags", [])
                for article in articles
            )
            for category in categories
        }
        for disease in diseases
    }

    years_in_range = list(
        range(
            int(config["date_range"]["start_year"]),
            int(config["date_range"]["end_year"]) + 1,
        )
    )

    year_distribution_warnings: list[str] = []

    for year in years_in_range:
        if year_counts.get(str(year), 0) == 0:
            year_distribution_warnings.append(
                f"No records for publication year {year}"
            )

    for disease in diseases:
        total = disease_counts.get(disease, 0)

        if total:
            highest_year, year_count = max(
                (
                    (
                        year,
                        year_disease_counts[disease].get(
                            str(year), 0
                        ),
                    )
                    for year in years_in_range
                ),
                key=lambda pair: pair[1],
            )

            if year_count / total > 0.40:
                year_distribution_warnings.append(
                    f"{disease}: {year_count}/{total} records "
                    f"({year_count / total:.1%}) are from {highest_year}"
                )

    coverage_met = all(
        count >= target
        for count in disease_coverage.values()
    )

    category_balance_met = all(
        category_balance[disease][category]
        >= quotas[disease][category]
        for disease in diseases
        for category in categories
    )

    passed = coverage_met and not any(
        (
            duplicate_pmids,
            duplicate_dois,
            duplicate_titles,
            missing_title,
            missing_abstract,
            non_english,
            invalid_year,
            malformed,
        )
    )

    return {
        "total_unique_articles": len(articles),
        "disease_counts": disease_coverage,
        "category_counts": {
            category: category_counts.get(category, 0)
            for category in categories
        },
        "category_counts_by_disease": category_balance,
        "category_targets_by_disease": quotas,
        "year_distribution": dict(sorted(year_counts.items())),
        "year_distribution_by_disease": {
            disease: {
                str(year): year_disease_counts[disease].get(
                    str(year), 0
                )
                for year in years_in_range
            }
            for disease in diseases
        },
        "year_distribution_warnings": year_distribution_warnings,
        "with_pmc_full_text": full_text_count,
        "abstract_only": len(articles) - full_text_count,
        "duplicates_removed": _duplicate_report_count(config),
        "duplicate_checks": {
            "duplicate_pmids": duplicate_pmids,
            "duplicate_dois": duplicate_dois,
            "duplicate_normalized_titles": duplicate_titles,
        },
        "missing_title": missing_title,
        "missing_abstract": missing_abstract,
        "non_english_records": non_english,
        "invalid_publication_years": invalid_year,
        "malformed_records": malformed,
        "failed_records": failed_count,
        "target_per_disease": target,
        "disease_coverage_met": coverage_met,
        "category_balance_met": category_balance_met,
        "validation_passed": passed,
    }


def build_distribution_csv(
    path: Path,
    articles: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    """Write overall and per-disease year/category distributions."""

    total_articles = len(articles)
    rows: list[dict[str, Any]] = []

    start_year = int(config["date_range"]["start_year"])
    end_year = int(config["date_range"]["end_year"])

    for year in range(start_year, end_year + 1):
        count = sum(
            article.get("publication_year") == year
            for article in articles
        )

        rows.append(
            {
                "dimension": "year",
                "group": "all",
                "value": year,
                "count": count,
                "percentage": (
                    round(100 * count / total_articles, 2)
                    if total_articles
                    else 0
                ),
            }
        )

    for disease in config["diseases"]:
        disease_records = [
            article
            for article in articles
            if disease in article.get("disease_tags", [])
        ]

        denominator = len(disease_records)

        for year in range(start_year, end_year + 1):
            count = sum(
                article.get("publication_year") == year
                for article in disease_records
            )

            rows.append(
                {
                    "dimension": "year",
                    "group": disease,
                    "value": year,
                    "count": count,
                    "percentage": (
                        round(100 * count / denominator, 2)
                        if denominator
                        else 0
                    ),
                }
            )

    for disease in config["diseases"]:
        count = sum(
            disease in article.get("disease_tags", [])
            for article in articles
        )

        rows.append(
            {
                "dimension": "disease",
                "group": "all",
                "value": disease,
                "count": count,
                "percentage": (
                    round(100 * count / total_articles, 2)
                    if total_articles
                    else 0
                ),
            }
        )

    for category in config["categories"]:
        count = sum(
            category in article.get("category_tags", [])
            for article in articles
        )

        rows.append(
            {
                "dimension": "category",
                "group": "all",
                "value": category,
                "count": count,
                "percentage": (
                    round(100 * count / total_articles, 2)
                    if total_articles
                    else 0
                ),
            }
        )

    for disease in config["diseases"]:
        denominator = sum(
            disease in article.get("disease_tags", [])
            for article in articles
        )

        for category in config["categories"]:
            count = sum(
                disease in article.get("disease_tags", [])
                and category in article.get("category_tags", [])
                for article in articles
            )

            rows.append(
                {
                    "dimension": "category",
                    "group": disease,
                    "value": category,
                    "count": count,
                    "percentage": (
                        round(100 * count / denominator, 2)
                        if denominator
                        else 0
                    ),
                }
            )

    path.parent.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(
        rows,
        columns=[
            "dimension",
            "group",
            "value",
            "count",
            "percentage",
        ],
    ).to_csv(path, index=False)


def _artifact_checks(
    root: Path,
    config: dict[str, Any],
    articles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check parseability, record consistency, provenance, and text files."""

    jsonl_path = root / config["output"]["articles_jsonl"]

    malformed_jsonl = 0

    with jsonl_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        malformed_jsonl += 1
                except json.JSONDecodeError:
                    malformed_jsonl += 1

    article_csv = root / config["output"]["articles_csv"]

    catalog_csv = (
        root
        / config["output"]["metadata_dir"]
        / "literature_catalog.csv"
    )

    expected_ids = [
        str(article.get("article_id") or "")
        for article in articles
    ]

    csv_ok = article_csv.exists() and catalog_csv.exists()

    csv_count = 0
    catalog_count = 0
    catalog_matches = False

    if csv_ok:
        csv_frame = pd.read_csv(
            article_csv,
            dtype=str,
            keep_default_na=False,
        )

        catalog_frame = pd.read_csv(
            catalog_csv,
            dtype=str,
            keep_default_na=False,
        )

        csv_count = len(csv_frame)
        catalog_count = len(catalog_frame)

        csv_ok = (
            csv_count == len(articles)
            and csv_frame["article_id"].tolist() == expected_ids
        )

        catalog_matches = (
            catalog_count == len(articles)
            and catalog_frame["article_id"].tolist() == expected_ids
            and catalog_frame["has_full_text"].tolist()
            == [
                str(bool(article.get("full_text")))
                for article in articles
            ]
        )

    missing_full_text_files = [
        article.get("article_id")
        for article in articles
        if article.get("full_text")
        and (
            not article.get("full_text_path")
            or not (
                root / str(article["full_text_path"])
            ).is_file()
        )
    ]

    missing_provenance = sum(
        not article.get("source")
        or not article.get("source_url")
        or not isinstance(article.get("provenance"), dict)
        or not article.get("provenance")
        for article in articles
    )

    missing_retrieved_at = sum(
        not article.get("retrieved_at")
        for article in articles
    )

    return {
        "jsonl_parseable": malformed_jsonl == 0,
        "malformed_jsonl_lines": malformed_jsonl,
        "articles_csv_consistent": bool(csv_ok),
        "articles_csv_rows": csv_count,
        "catalog_consistent": bool(catalog_matches),
        "catalog_rows": catalog_count,
        "missing_full_text_files": missing_full_text_files,
        "missing_provenance": missing_provenance,
        "missing_retrieved_at": missing_retrieved_at,
    }


def _write_handoff(
    path: Path,
    summary: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Write a compact machine-readable Stage 1 manifest."""

    payload = {
        "dataset_name": "NeuraVision Literature Corpus",
        "stage": 1,
        "modality": "text",
        "status": (
            "ready"
            if summary["validation_passed"]
            else "not_ready"
        ),
        "total_unique_articles": summary["total_unique_articles"],
        "date_range": {
            "start": int(config["date_range"]["start_year"]),
            "end": int(config["date_range"]["end_year"]),
        },
        "diseases": [
            {
                "key": key,
                "label": value["label"],
                "count": summary["disease_counts"].get(key, 0),
            }
            for key, value in config["diseases"].items()
        ],
        "files": {
            "jsonl": config["output"]["articles_jsonl"],
            "csv": config["output"]["articles_csv"],
            "catalog": (
                f"{config['output']['metadata_dir']}"
                "/literature_catalog.csv"
            ),
            "article_text_dir": config["output"]["article_text_dir"],
        },
        "schema_version": "1.0",
        "full_text_count": summary["with_pmc_full_text"],
        "abstract_only_count": summary["abstract_only"],
        "validation_passed": summary["validation_passed"],
        "notes": (
            "Literature data for the existing multimodal "
            "NeuraVision model; this repository does not "
            "contain or train that model."
        ),
    }

    path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _duplicate_report_count(
    config: dict[str, Any],
) -> int:
    """Read the number of recorded duplicate merges, if available."""

    root = config.get("_root")

    if not root:
        return 0

    path = (
        Path(root)
        / config["output"]["metadata_dir"]
        / "duplicate_report.csv"
    )

    if not path.exists():
        return 0

    with path.open(
        encoding="utf-8",
        newline="",
    ) as handle:
        return sum(
            1
            for _ in csv.DictReader(handle)
        )


def _year_is_valid(
    value: Any,
    config: dict[str, Any],
) -> bool:
    """Return whether a source year is numeric and configured."""

    try:
        year = int(value)
    except (TypeError, ValueError):
        return False

    return (
        int(config["date_range"]["start_year"])
        <= year
        <= int(config["date_range"]["end_year"])
    )


def _write_dataset_report(
    path: Path,
    summary: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Write a report-ready methodology and dataset summary."""

    disease_labels = config["diseases"]
    category_labels = config["categories"]

    lines = [
        "# NeuraVision Literature Dataset Summary",
        "",
        "## Purpose",
        "",
        (
            "A reproducible, traceable biomedical-literature "
            "corpus for the NeuraVision neurological "
            "knowledge-discovery project."
        ),
        (
            "This dataset repository performs literature "
            "acquisition and normalization only; it does not "
            "implement embeddings, retrieval, RAG, or LLM reasoning."
        ),
        "",
        "## Sources and collection methodology",
        "",
        (
            "Records were searched and fetched from PubMed using "
            "NCBI E-utilities ESearch and EFetch. Queries are "
            "defined in `config/literature_config.yaml`; each "
            "disease/category query is stratified by publication "
            "year within the configured range to avoid a "
            "recency-only sample. Search results are fetched in "
            "batches, parsed from PubMed XML, and filtered for "
            "title, abstract, supported year, and English language "
            "where language metadata is present. Article provenance "
            "retains query groups, full query expressions, retrieval "
            "timestamp, PMID URL, and database."
        ),
        (
            "PMC full text is attempted only through NCBI's "
            "official PMC Article Dataset on AWS. The script checks "
            "article-version metadata and retrieves XML only for "
            "non-retracted Open Access Subset versions. JATS XML is "
            "parsed without including the references section; "
            "unavailable or non-open-access text is not bypassed, "
            "and its PubMed abstract remains available."
        ),
        "",
        (
            "## Date range: "
            f"{config['date_range']['start_year']}–"
            f"{config['date_range']['end_year']}"
        ),
        "",
        "## Dataset distribution",
        "",
        f"- Unique articles: {summary['total_unique_articles']}",
        f"- PMC full text: {summary['with_pmc_full_text']}",
        f"- Abstract only: {summary['abstract_only']}",
        (
            "- Duplicate records merged/removed: "
            f"{summary['duplicates_removed']}"
        ),
        (
            "- Failed records or requests: "
            f"{summary['failed_records']}"
        ),
        "",
        "### By disease",
        "",
        "| Disease | Records |",
        "|---|---:|",
    ]

    lines.extend(
        f"| {disease_labels[disease]['label']} | {count} |"
        for disease, count in summary["disease_counts"].items()
    )

    lines.extend(
        [
            "",
            "### By category",
            "",
            "| Category | Records |",
            "|---|---:|",
        ]
    )

    lines.extend(
        f"| {category_labels[category]['label']} | {count} |"
        for category, count in summary["category_counts"].items()
    )

    lines.extend(
        [
            "",
            "### By publication year",
            "",
            "| Year | Records |",
            "|---|---:|",
        ]
    )

    lines.extend(
        f"| {year} | {count} |"
        for year, count in summary["year_distribution"].items()
    )

    lines.extend(
        [
            "",
            "### By disease and publication year",
            "",
        ]
    )

    years = list(
        range(
            int(config["date_range"]["start_year"]),
            int(config["date_range"]["end_year"]) + 1,
        )
    )

    lines.append(
        "| Disease | "
        + " | ".join(map(str, years))
        + " | Total |"
    )

    lines.append(
        "|"
        + "---|" * (len(years) + 2)
    )

    for disease, settings in disease_labels.items():
        values = summary["year_distribution_by_disease"][disease]

        lines.append(
            "| "
            + settings["label"]
            + " | "
            + " | ".join(
                str(values[str(year)])
                for year in years
            )
            + f" | {summary['disease_counts'][disease]} |"
        )

    lines.extend(
        [
            "",
            "### Year distribution quality flags",
            "",
        ]
    )

    lines.extend(
        [
            f"- WARNING: {warning}"
            for warning in summary["year_distribution_warnings"]
        ]
        or [
            (
                "- No missing years or disease-year concentration "
                "above 40%."
            )
        ]
    )

    lines.extend(
        [
            "",
            "## Duplicate handling",
            "",
            (
                "Records are matched in priority order by DOI, "
                "PMID, then normalized title. Disease/category "
                "labels, identifiers, publication types, keywords, "
                "MeSH terms, and author values are merged into the "
                "canonical record. The generated "
                "`duplicate_report.csv` records each merge and its "
                "match key."
            ),
            "",
            "## Limitations",
            "",
            (
                "Search results depend on PubMed indexing and "
                "configurable text/category terms; categories "
                "overlap and are query-derived rather than mutually "
                "exclusive. Full text is limited to non-retracted "
                "article versions in PMC's Open Access Subset "
                "available through the official PMC Article Dataset. "
                "Some records may have incomplete abstracts or "
                "metadata and are excluded when required "
                "title/abstract/year information is absent. Counts "
                "reflect the data collected at run time and are not "
                "estimates of the full literature."
            ),
            "",
            "## Reproducibility",
            "",
            (
                "Create a Python environment, install "
                "`requirements.txt`, optionally set `NCBI_EMAIL` "
                "and `NCBI_API_KEY`, then run "
                "`python scripts/ingest_pubmed.py --config "
                "config/literature_config.yaml`. For a small smoke "
                "build, add `--dry-run`; follow with "
                "`python scripts/fetch_pmc_fulltext.py`, "
                "`python scripts/normalize_articles.py`, "
                "`python scripts/build_catalog.py`, and "
                "`python scripts/validate_literature.py`. API "
                "responses are cached under `cache/eutils`; ESearch "
                "and EFetch XML are retained under "
                "`raw/literature`. The cache and raw snapshots can "
                "be removed to force a fresh collection."
            ),
            "",
            (
                "Validation result: "
                f"**{'PASS' if summary['validation_passed'] else 'FAIL'}**. "
                "Disease targets met: "
                f"**{'yes' if summary['disease_coverage_met'] else 'no'}**."
            ),
        ]
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def validate(
    config_path: Path,
    target_override: int | None = None,
) -> dict[str, Any]:
    """Read corpus files, write computed summary, and return it."""

    config = load_config(config_path)

    root = config_path.parent.parent
    config["_root"] = str(root)

    articles = read_jsonl(
        root / config["output"]["articles_jsonl"]
    )

    failed = read_jsonl(
        root
        / config["output"]["metadata_dir"]
        / "failed_records.jsonl"
    )

    summary = validate_articles(
        articles,
        config,
        len(failed),
        target_override,
    )

    output = (
        root
        / config["output"]["metadata_dir"]
        / "download_summary.json"
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary["artifact_checks"] = _artifact_checks(
        root,
        config,
        articles,
    )

    artifact_checks = summary["artifact_checks"]

    artifacts_passed = (
        artifact_checks["jsonl_parseable"]
        and artifact_checks["articles_csv_consistent"]
        and artifact_checks["catalog_consistent"]
        and not artifact_checks["missing_full_text_files"]
        and artifact_checks["missing_provenance"] == 0
        and artifact_checks["missing_retrieved_at"] == 0
    )

    summary["validation_passed"] = (
        summary["validation_passed"]
        and artifacts_passed
    )

    summary["validation_failures"] = [
        key
        for key, passed in {
            "duplicate_pmids": (
                summary["duplicate_checks"]["duplicate_pmids"] == 0
            ),
            "duplicate_dois": (
                summary["duplicate_checks"]["duplicate_dois"] == 0
            ),
            "duplicate_normalized_titles": (
                summary["duplicate_checks"][
                    "duplicate_normalized_titles"
                ]
                == 0
            ),
            "missing_titles": summary["missing_title"] == 0,
            "missing_abstracts": summary["missing_abstract"] == 0,
            "invalid_years": (
                summary["invalid_publication_years"] == 0
            ),
            "disease_coverage": summary["disease_coverage_met"],
            "jsonl": artifact_checks["jsonl_parseable"],
            "articles_csv": (
                artifact_checks["articles_csv_consistent"]
            ),
            "catalog": artifact_checks["catalog_consistent"],
            "full_text_files": (
                not artifact_checks["missing_full_text_files"]
            ),
            "provenance": (
                artifact_checks["missing_provenance"] == 0
            ),
            "retrieved_at": (
                artifact_checks["missing_retrieved_at"] == 0
            ),
        }.items()
        if not passed
    ]

    build_distribution_csv(
        output.parent / "literature_distribution.csv",
        articles,
        config,
    )

    _write_handoff(
        output.parent / "literature_handoff.json",
        summary,
        config,
    )

    output.write_text(
        json.dumps(
            summary,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    _write_dataset_report(
        output.parent / "DATASET_SUMMARY.md",
        summary,
        config,
    )

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "config/literature_config.yaml"
        ),
    )

    parser.add_argument(
        "--target-per-disease",
        type=int,
    )

    args = parser.parse_args()

    summary = validate(
        args.config,
        args.target_per_disease,
    )

    config = load_config(args.config)

    print("=" * 50)
    print("NeuraVision Literature Dataset Build Summary")
    print("=" * 50)

    print(
        f"Unique articles: "
        f"{summary['total_unique_articles']}"
    )

    for disease, settings in config["diseases"].items():
        print(
            f"{settings['label']}: "
            f"{summary['disease_counts'].get(disease, 0)}"
        )

    for category, settings in config["categories"].items():
        print(
            f"{settings['label']}: "
            f"{summary['category_counts'].get(category, 0)}"
        )

    print(
        f"PMC full-text articles: "
        f"{summary['with_pmc_full_text']}"
    )

    print(
        f"Abstract-only articles: "
        f"{summary['abstract_only']}"
    )

    print(
        f"Duplicates removed: "
        f"{summary['duplicates_removed']}"
    )

    print(
        f"Failed records: "
        f"{summary['failed_records']}"
    )

    print(
        "Validation: "
        f"{'PASS' if summary['validation_passed'] else 'FAIL'}"
    )

    if summary.get("validation_failures"):
        print(
            "Validation failures: "
            + ", ".join(summary["validation_failures"])
        )

    print("=" * 50)


if __name__ == "__main__":
    main()