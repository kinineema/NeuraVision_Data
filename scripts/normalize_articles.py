"""Normalize article JSONL records and refresh their flat CSV export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

if __package__:
    from .literature_utils import load_config, read_jsonl, write_jsonl
else:
    from literature_utils import load_config, read_jsonl, write_jsonl


def normalize(article: dict[str, Any], text_dir: Path) -> dict[str, Any]:
    """Apply consistent null/list defaults without inventing source metadata."""
    normalized = dict(article)
    for field in ("disease_tags", "category_tags", "publication_types", "mesh_terms", "keywords", "authors"):
        normalized[field] = normalized.get(field) or []
    for field in ("pmid", "pmcid", "doi", "title", "abstract", "journal", "publication_year",
                  "publication_date", "language", "full_text", "full_text_source", "source_url"):
        normalized.setdefault(field, None)
    if not normalized.get("full_text") and normalized.get("pmid"):
        text_path = text_dir / f"{normalized['pmid']}.txt"
        if text_path.exists():
            normalized["full_text"] = text_path.read_text(encoding="utf-8")
            normalized["full_text_source"] = "PMC"
    text_path = text_dir / f"{normalized['pmid']}.txt" if normalized.get("pmid") else None
    normalized["has_full_text"] = bool(normalized.get("full_text"))
    normalized["full_text_path"] = (
        text_path.relative_to(text_dir.parents[2]).as_posix()
        if text_path and normalized["has_full_text"] and text_path.exists()
        else None
    )
    status = normalized.get("full_text_status") or normalized.get("full_text_fetch_status")
    if normalized["has_full_text"]:
        status = "available"
    elif normalized.get("pmcid"):
        status = status or "not_fetched"
    else:
        status = "no_pmcid"
    normalized["full_text_fetch_status"] = status
    normalized.setdefault("article_id", f"PMID_{normalized['pmid']}" if normalized["pmid"] else None)
    normalized.setdefault("source", "PubMed")
    normalized.setdefault("provenance", {})
    normalized.setdefault("retrieved_at", None)
    return normalized


def export_csv(path: Path, articles: list[dict[str, Any]]) -> None:
    """Create a CSV-friendly view of the normalized records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    columns = list(dict.fromkeys(key for article in articles for key in article))
    for article in articles:
        row = dict(article)
        row["authors"] = json.dumps(article["authors"], ensure_ascii=False)
        row["publication_types"] = "|".join(article["publication_types"])
        row["mesh_terms"] = "|".join(article["mesh_terms"])
        row["keywords"] = "|".join(article["keywords"])
        row["disease_tags"] = "|".join(article["disease_tags"])
        row["category_tags"] = "|".join(article["category_tags"])
        row["provenance"] = json.dumps(article["provenance"], ensure_ascii=False)
        row["full_text_sections"] = json.dumps(article.get("full_text_sections", []), ensure_ascii=False)
        rows.append(row)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/literature_config.yaml"))
    args = parser.parse_args()
    config = load_config(args.config)
    root = args.config.parent.parent
    jsonl_path = root / config["output"]["articles_jsonl"]
    text_dir = root / config["output"]["article_text_dir"]
    articles = [normalize(item, text_dir) for item in read_jsonl(jsonl_path)]
    write_jsonl(jsonl_path, articles)
    export_csv(root / config["output"]["articles_csv"], articles)


if __name__ == "__main__":
    main()
