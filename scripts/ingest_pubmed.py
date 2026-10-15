"""Search PubMed, balance candidates, normalize records, and export JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from tqdm import tqdm

if __package__:
    from .ncbi_client import NCBIClient
    from .literature_utils import load_config, merge_articles, parse_pubmed_article, read_jsonl, write_jsonl
else:
    from ncbi_client import NCBIClient
    from literature_utils import load_config, merge_articles, parse_pubmed_article, read_jsonl, write_jsonl

LOGGER = logging.getLogger("neura_ingestion")


def build_query(base_query: str, category_query: str, start_year: int, end_year: int) -> str:
    """Build a PubMed search query, keeping all concepts in configuration."""
    return f"({base_query}) AND ({category_query}) AND {start_year}:{end_year}[dp]"


def category_targets(target: int, categories: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Convert configurable category shares to integer quotas totaling target."""
    exact = {name: target * float(value["target_share"]) for name, value in categories.items()}
    quotas = {name: math.floor(value) for name, value in exact.items()}
    for name in sorted(exact, key=lambda item: exact[item] - quotas[item], reverse=True)[: target - sum(quotas.values())]:
        quotas[name] += 1
    return quotas


def _safe_component(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")


def collect_candidates(
    config: dict[str, Any],
    client: NCBIClient,
    raw_dir: Path,
    target: int,
    selected_disease: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Search disease/category combinations and merge query provenance per PMID."""
    start_year = int(config["date_range"]["start_year"])
    end_year = int(config["date_range"]["end_year"])
    multiplier = float(config.get("candidate_multiplier", 2))
    diseases = config["diseases"]
    categories = config["categories"]
    years = list(range(start_year, end_year + 1))
    quotas = category_targets(target, categories)
    candidates_per_year = {
        category: max(1, math.ceil(quotas[category] * multiplier / len(years)))
        for category in categories
    }
    disease_names = [selected_disease] if selected_disease else list(diseases)
    unknown = [name for name in disease_names if name not in diseases]
    if unknown:
        raise ValueError(f"Unknown disease key(s): {', '.join(unknown)}")
    candidates: dict[str, dict[str, Any]] = {}
    for disease in disease_names:
        disease_config = diseases[disease]
        for category, category_config in categories.items():
            for year in years:
                query = build_query(disease_config["query"], category_config["query"], year, year)
                requested = candidates_per_year[category]
                query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:10]
                key = f"search_{_safe_component(disease)}_{_safe_component(category)}_{year}_{requested}_{query_hash}"
                raw_path = raw_dir / disease / f"{category}_{year}_search.xml"
                try:
                    pmids = client.search(query, requested, key, raw_path)
                except Exception as error:
                    LOGGER.exception("Search failed for %s/%s/%d", disease, category, year)
                    candidates.setdefault(f"__failed__{key}", {"error": str(error), "query": query})
                    continue
                for pmid in pmids:
                    item = candidates.setdefault(
                        pmid,
                        {
                            "disease_tags": set(),
                            "category_tags": set(),
                            "queries": [],
                            "query_groups": [],
                        },
                    )
                    item["disease_tags"].add(disease)
                    item["category_tags"].add(category)
                    item["queries"].append(query)
                    item["query_groups"].append(f"{disease}:{category}:{year}")
    return candidates


def _eligible(article: dict[str, Any], config: dict[str, Any]) -> bool:
    return not _rejection_reasons(article, config)


def _rejection_reasons(article: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """Describe each reason an otherwise fetched PubMed record is excluded."""
    reasons: list[str] = []
    if not article.get("pmid"):
        reasons.append("missing PMID")
    if not article.get("title"):
        reasons.append("missing title")
    if not article.get("abstract"):
        reasons.append("missing abstract")
    year_range = config["date_range"]
    year = article.get("publication_year")
    if year is None:
        reasons.append("missing publication year")
    elif not int(year_range["start_year"]) <= year <= int(year_range["end_year"]):
        reasons.append(f"publication year {year} outside configured range")
    if article.get("language") and article.get("language") != "eng":
        reasons.append(f"non-English language: {article['language']}")
    return reasons


def _balanced_select(
    articles: list[dict[str, Any]],
    disease_names: list[str],
    categories: dict[str, dict[str, Any]],
    target: int,
) -> list[dict[str, Any]]:
    """Greedily select usable papers against disease/category quota deficits."""
    quotas = category_targets(target, categories)
    years = sorted({int(article["publication_year"]) for article in articles if article.get("publication_year") is not None})
    year_quotas = category_targets(
        target,
        {str(year): {"target_share": 1 / len(years)} for year in years},
    ) if years else {}
    disease_counts = defaultdict(int)
    category_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    year_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    remaining = list(articles)
    selected: list[dict[str, Any]] = []
    while remaining:
        eligible_for = [
            article for article in remaining
            if any(disease in disease_names and disease_counts[disease] < target for disease in article["disease_tags"])
        ]
        if not eligible_for:
            break

        def score(article: dict[str, Any]) -> float:
            total = 0.0
            for disease in article["disease_tags"]:
                if disease not in disease_names or disease_counts[disease] >= target:
                    continue
                total += 1.0 + sum(
                    max(0, quotas[category] - category_counts[disease][category]) / max(1, quotas[category])
                    for category in article["category_tags"]
                )
                year = int(article["publication_year"])
                total += 2.0 * max(0, year_quotas.get(year, 0) - year_counts[disease][year]) / max(1, year_quotas.get(year, 0))
            return total

        best = max(eligible_for, key=score)
        selected.append(best)
        for disease in best["disease_tags"]:
            if disease in disease_names and disease_counts[disease] < target:
                disease_counts[disease] += 1
                year_counts[disease][int(best["publication_year"])] += 1
                for category in best["category_tags"]:
                    category_counts[disease][category] += 1
        remaining.remove(best)
    return selected


def ingest(config_path: Path, target: int, disease: str | None, resume: bool) -> list[dict[str, Any]]:
    """Execute the configured PubMed download and export normalized records."""
    config = load_config(config_path)
    root = config_path.parent.parent
    raw_dir = root / config["output"]["raw_dir"]
    output_path = root / config["output"]["articles_jsonl"]
    metadata_dir = root / config["output"]["metadata_dir"]
    cache_dir = root / "cache" / "eutils"
    prior = read_jsonl(output_path) if resume else []
    client = NCBIClient(config, cache_dir)
    candidate_info = collect_candidates(config, client, raw_dir, target, disease)
    failures: list[dict[str, Any]] = []
    failures.extend(
        {"record_type": "search", "request": key.removeprefix("__failed__"), **value}
        for key, value in candidate_info.items()
        if key.startswith("__failed__")
    )
    candidate_info = {key: value for key, value in candidate_info.items() if not key.startswith("__failed__")}

    existing = {str(article.get("pmid")): article for article in prior if article.get("pmid")}
    by_pmid = dict(existing)
    missing_pmids = [pmid for pmid in candidate_info if pmid not in existing]
    batch_size = int(config.get("batch_size", 100))
    batch_starts = range(0, len(missing_pmids), batch_size)
    for batch_index in tqdm(batch_starts, desc="Fetching PubMed batches", unit="batch"):
        batch = missing_pmids[batch_index:batch_index + batch_size]
        key = f"fetch_{batch_index // batch_size:05d}_{batch[0]}_{batch[-1]}"
        try:
            response = client.fetch(batch, key)
            raw_path = raw_dir / "efetch" / f"{key}.xml"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(response)
            root_xml = ET.fromstring(response)
            for element in root_xml.findall(".//PubmedArticle"):
                pmid = element.findtext(".//PMID")
                if not pmid or pmid not in candidate_info:
                    continue
                info = candidate_info[pmid]
                provenance = {
                    "database": "pubmed",
                    "query_group": sorted(set(info["query_groups"])),
                    "search_query": sorted(set(info["queries"])),
                }
                article = parse_pubmed_article(
                    element, info["disease_tags"], info["category_tags"], provenance
                )
                if _eligible(article, config):
                    by_pmid[pmid] = article
                else:
                    failures.append({
                        "record_type": "article",
                        "pmid": pmid,
                        "reasons": _rejection_reasons(article, config),
                    })
        except Exception as error:
            LOGGER.exception("EFetch batch failed: %s", key)
            failures.append({"record_type": "fetch_batch", "batch": key, "pmids": batch, "error": str(error)})

    for pmid, info in candidate_info.items():
        if pmid in existing:
            article = existing[pmid]
            article["disease_tags"] = sorted(set(article.get("disease_tags", [])) | info["disease_tags"])
            article["category_tags"] = sorted(set(article.get("category_tags", [])) | info["category_tags"])
            provenance = article.setdefault("provenance", {})
            provenance["query_group"] = sorted(set(provenance.get("query_group", [])) | set(info["query_groups"]))
            provenance["search_query"] = sorted(set(provenance.get("search_query", [])) | set(info["queries"]))

    candidates = list(by_pmid.values())
    disease_names = [disease] if disease else list(config["diseases"])
    selected = _balanced_select(candidates, disease_names, config["categories"], target)
    # Keep records from diseases outside a disease-filtered invocation intact on resume.
    if resume and disease:
        selected_ids = {article.get("pmid") for article in selected}
        selected.extend(article for article in prior if article.get("pmid") not in selected_ids and disease not in article.get("disease_tags", []))
    merged, duplicate_rows = merge_articles(selected)
    write_jsonl(output_path, merged)
    write_jsonl(metadata_dir / "failed_records.jsonl", failures)
    duplicate_path = metadata_dir / "duplicate_report.csv"
    duplicate_path.parent.mkdir(parents=True, exist_ok=True)
    import csv

    with duplicate_path.open("w", encoding="utf-8", newline="") as handle:
        columns = ["canonical_article_id", "duplicate_identifier", "duplicate_type", "matched_on", "disease_tags_merged", "category_tags_merged"]
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(duplicate_rows)
    LOGGER.info("Saved %d unique PubMed records to %s", len(merged), output_path)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/literature_config.yaml"))
    parser.add_argument("--disease")
    parser.add_argument("--target-per-disease", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    target = args.target_per_disease or (5 if args.dry_run else None)
    if target is None:
        config = load_config(args.config)
        target = int(config.get("target_per_disease", 120))
    if target < 1:
        parser.error("--target-per-disease must be positive")
    ingest(args.config, target, args.disease, args.resume)


if __name__ == "__main__":
    main()
