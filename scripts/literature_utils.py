"""Shared parsing, normalization, and export helpers for literature records."""

from __future__ import annotations

import csv
import json
import logging
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

LOGGER = logging.getLogger(__name__)


def local_name(tag: str) -> str:
    """Return an XML tag name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def element_text(element: ET.Element | None) -> str:
    """Extract normalized readable text from an XML element."""
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def first_text(element: ET.Element, path: str) -> str | None:
    """Get text for the first matching child path, if present."""
    found = element.find(path)
    value = element_text(found)
    return value or None


def extract_doi(article: ET.Element) -> str | None:
    """Extract the DOI article identifier from a PubMed record."""
    for identifier in article.findall(".//ArticleId"):
        if identifier.get("IdType", "").lower() == "doi":
            value = element_text(identifier).strip()
            return value or None
    return None


def extract_pmid(article: ET.Element) -> str | None:
    """Extract PMID from a PubMed record."""
    value = first_text(article, ".//PMID")
    return value.strip() if value else None


def parse_publication_year(article: ET.Element) -> int | None:
    """Return a plausible year using PubMed date fields, without inventing one."""
    year = first_text(article, ".//PubDate/Year") or first_text(article, ".//DateCompleted/Year")
    if year and year.isdigit() and len(year) == 4:
        return int(year)
    medline_date = first_text(article, ".//PubDate/MedlineDate")
    match = re.search(r"\b(19|20)\d{2}\b", medline_date or "")
    return int(match.group()) if match else None


def parse_publication_date(article: ET.Element) -> str | None:
    """Build an ISO-like publication date when the source supplies components."""
    date_element = article.find(".//Article/Journal/JournalIssue/PubDate")
    if date_element is None:
        date_element = article.find(".//PubDate")
    if date_element is None:
        return None
    year = first_text(date_element, "Year")
    month = first_text(date_element, "Month")
    day = first_text(date_element, "Day")
    if not year:
        return None
    month_num = _month_number(month) if month else None
    if month_num is None:
        return year
    if day and day.isdigit() and 1 <= int(day) <= 31:
        return f"{year}-{month_num:02d}-{int(day):02d}"
    return f"{year}-{month_num:02d}"


def _month_number(value: str) -> int | None:
    months = {
        name.lower(): number
        for number, name in enumerate(
            ("January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"),
            start=1,
        )
    }
    normalized = value.strip().lower()
    if normalized.isdigit() and 1 <= int(normalized) <= 12:
        return int(normalized)
    return next((number for name, number in months.items() if name.startswith(normalized[:3])), None)


def parse_authors(article: ET.Element) -> list[dict[str, str | None]]:
    """Extract author names from a PubMed article."""
    authors: list[dict[str, str | None]] = []
    for author in article.findall(".//AuthorList/Author"):
        collective = first_text(author, "CollectiveName")
        last_name = first_text(author, "LastName")
        fore_name = first_text(author, "ForeName") or first_text(author, "Initials")
        if last_name or fore_name or collective:
            item: dict[str, str | None] = {"last_name": last_name, "fore_name": fore_name}
            if collective:
                item["collective_name"] = collective
            authors.append(item)
    return authors


def parse_pubmed_article(
    article: ET.Element,
    disease_tags: Iterable[str],
    category_tags: Iterable[str],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Normalize one PubMed article element into the repository article schema."""
    pmid = extract_pmid(article)
    title = first_text(article, ".//ArticleTitle")
    abstract_parts = [
        element_text(part)
        for part in article.findall(".//Abstract/AbstractText")
        if element_text(part)
    ]
    abstract = "\n\n".join(abstract_parts) or None
    journal = first_text(article, ".//Journal/Title")
    pmcid = next(
        (
            element_text(identifier)
            for identifier in article.findall(".//ArticleId")
            if identifier.get("IdType", "").lower() == "pmc"
        ),
        None,
    )
    language = first_text(article, ".//Language")
    mesh_terms = [
        element_text(term)
        for term in article.findall(".//MeshHeading/DescriptorName")
        if element_text(term)
    ]
    keywords = [
        element_text(keyword)
        for keyword in article.findall(".//Keyword")
        if element_text(keyword)
    ]
    publication_types = [
        element_text(item)
        for item in article.findall(".//PublicationType")
        if element_text(item)
    ]
    return {
        "article_id": f"PMID_{pmid}" if pmid else None,
        "pmid": pmid,
        "pmcid": pmcid,
        "doi": extract_doi(article),
        "title": title,
        "abstract": abstract,
        "full_text": None,
        "authors": parse_authors(article),
        "journal": journal,
        "publication_year": parse_publication_year(article),
        "publication_date": parse_publication_date(article),
        "publication_types": publication_types,
        "mesh_terms": mesh_terms,
        "keywords": keywords,
        "language": language,
        "disease_tags": sorted(set(disease_tags)),
        "category_tags": sorted(set(category_tags)),
        "source": "PubMed",
        "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
        "full_text_source": None,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "provenance": provenance,
    }


def normalized_title(title: str | None) -> str:
    """Normalize title for conservative exact duplicate detection."""
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", title).casefold()
    return re.sub(r"[\W_]+", " ", text, flags=re.UNICODE).strip()


def merge_articles(articles: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deduplicate articles by DOI, PMID, then normalized title, merging labels."""
    canonical: list[dict[str, Any]] = []
    lookup: dict[tuple[str, str], int] = {}
    duplicates: list[dict[str, Any]] = []
    for incoming in articles:
        keys = [
            ("doi", str(incoming.get("doi", "")).strip().lower()),
            ("pmid", str(incoming.get("pmid", "")).strip()),
            ("title", normalized_title(incoming.get("title"))),
        ]
        matched_key = next((key for key in keys if key[1] and key in lookup), None)
        match = lookup[matched_key] if matched_key else None
        if match is None:
            index = len(canonical)
            canonical.append(incoming)
            for key in keys:
                if key[1]:
                    lookup[key] = index
            continue

        existing = canonical[match]
        existing["disease_tags"] = sorted(set(existing.get("disease_tags", [])) | set(incoming.get("disease_tags", [])))
        existing["category_tags"] = sorted(set(existing.get("category_tags", [])) | set(incoming.get("category_tags", [])))
        for field in ("pmcid", "doi", "abstract", "full_text", "journal", "publication_date", "publication_year"):
            if not existing.get(field) and incoming.get(field):
                existing[field] = incoming[field]
        for field in ("mesh_terms", "keywords", "publication_types", "authors"):
            existing[field] = _merge_unique(existing.get(field, []), incoming.get(field, []))
        for key in keys:
            if key[1]:
                lookup[key] = match
        duplicates.append(
            {
                "canonical_article_id": existing.get("article_id"),
                "duplicate_identifier": incoming.get("article_id") or incoming.get("pmid") or incoming.get("doi"),
                "duplicate_type": "duplicate",
                "matched_on": matched_key[0] if matched_key else "title",
                "disease_tags_merged": "|".join(existing["disease_tags"]),
                "category_tags_merged": "|".join(existing["category_tags"]),
            }
        )
    return canonical, duplicates


def _merge_unique(first: list[Any], second: list[Any]) -> list[Any]:
    output = list(first)
    for value in second:
        if value not in output:
            output.append(value)
    return output


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load valid JSON objects from a JSONL file."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        rows.append(item)
                except json.JSONDecodeError:
                    LOGGER.warning("Skipping malformed JSONL line %s:%d", path, line_number)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Write JSON objects as UTF-8 JSON Lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_catalog_csv(path: Path, articles: list[dict[str, Any]]) -> None:
    """Export the requested lightweight catalog columns."""
    columns = [
        "article_id", "pmid", "pmcid", "doi", "title", "journal",
        "publication_year", "publication_types", "disease_tags", "category_tags",
        "has_abstract", "has_full_text", "source", "source_url", "retrieved_at",
        "validation_status", "full_text_fetch_status", "full_text_path", "full_text_license",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for article in articles:
            row = {key: article.get(key) for key in columns}
            for key in ("publication_types", "disease_tags", "category_tags"):
                row[key] = "|".join(article.get(key) or [])
            row["has_abstract"] = bool(article.get("abstract"))
            row["has_full_text"] = bool(article.get("full_text"))
            row["full_text_fetch_status"] = article.get("full_text_fetch_status", article.get("full_text_status"))
            row["full_text_path"] = article.get("full_text_path")
            row["full_text_license"] = article.get("full_text_license")
            row["validation_status"] = (
                "valid" if article.get("title") and article.get("abstract") else "invalid"
            )
            writer.writerow(row)


def load_config(path: Path) -> dict[str, Any]:
    """Load YAML config and ensure its root is a mapping."""
    import yaml

    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"Configuration must be a YAML mapping: {path}")
    return config
