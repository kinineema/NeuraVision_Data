"""Fetch openly licensed PMC JATS packages and extract structured article text."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from urllib.parse import quote

import requests
from tqdm import tqdm

if __package__:
    from .ncbi_client import NCBIClient
    from .literature_utils import load_config, local_name, read_jsonl, write_jsonl
else:
    from ncbi_client import NCBIClient
    from literature_utils import load_config, local_name, read_jsonl, write_jsonl

LOGGER = logging.getLogger("neura_pmc")
PMC_DATASET_BASE = "https://pmc-oa-opendata.s3.amazonaws.com/"


class NoOpenAccessVersionError(ValueError):
    """The PMCID has no eligible version in PMC's Open Access Subset."""


def _text(element: ET.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _https_object_url(value: str) -> str:
    """Convert dataset S3 references to the documented public HTTPS endpoint."""
    prefix = "s3://pmc-oa-opendata/"
    if value.startswith(prefix):
        return PMC_DATASET_BASE + value[len(prefix):]
    return value


def extract_jats(content: bytes) -> tuple[str, list[dict[str, str]]]:
    """Extract article title, abstract, non-reference body sections, captions, and tables."""
    root = ET.fromstring(content)
    title = next((_text(node) for node in root.iter() if node.tag.endswith("article-title") and _text(node)), "")
    abstract_nodes = [node for node in root.iter() if node.tag.endswith("abstract")]
    abstract = "\n\n".join(filter(None, (_text(node) for node in abstract_nodes)))
    sections: list[dict[str, str]] = []
    body = next((node for node in root.iter() if node.tag.endswith("body")), None)
    if body is not None:
        for section in body:
            if not section.tag.endswith("sec"):
                continue
            heading_node = next((node for node in section if node.tag.endswith("title")), None)
            heading = _text(heading_node) or "Untitled section"
            paragraphs: list[str] = []
            for node in section.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                if tag == "p":
                    value = _text(node)
                    if value:
                        paragraphs.append(value)
                elif tag in {"caption", "table", "table-wrap-foot"}:
                    value = _text(node)
                    if value:
                        paragraphs.append(value)
            if paragraphs:
                sections.append({"heading": heading, "text": "\n\n".join(paragraphs)})
    pieces = [value for value in (title, abstract) if value]
    pieces.extend(f"{section['heading']}\n{section['text']}" for section in sections)
    return "\n\n".join(pieces), sections


def fetch_open_access_jats(client: NCBIClient, pmcid: str, cache_key: str) -> tuple[bytes, str | None]:
    """Resolve a licensed PMC article version through NCBI's official AWS dataset."""
    identifier = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
    listing = client.get_public_url(
        PMC_DATASET_BASE,
        {"list-type": "2", "prefix": f"{identifier}.", "delimiter": "/"},
        f"{cache_key}_versions",
    )
    listing_root = ET.fromstring(listing)
    prefixes = []
    for parent in listing_root.iter():
        if local_name(parent.tag) == "CommonPrefixes":
            prefixes.extend(
                node.text.rstrip("/")
                for node in parent
                if local_name(node.tag) == "Prefix" and node.text
            )
    if not prefixes:
        raise NoOpenAccessVersionError(f"PMC article dataset has no archived version for {identifier}")

    versions: list[tuple[bool, str, str | None]] = []
    for prefix in prefixes:
        metadata_key = f"metadata/{prefix.rsplit('/', 1)[-1]}.json"
        metadata_url = PMC_DATASET_BASE + quote(metadata_key, safe="/")
        try:
            metadata = json.loads(
                client.get_binary_url(metadata_url, f"{cache_key}_{prefix.rsplit('.', 1)[-1]}_metadata")
            )
        except (json.JSONDecodeError, RuntimeError) as error:
            LOGGER.warning("Unable to read PMC metadata for %s: %s", prefix, error)
            continue
        is_open_access = str(metadata.get("is_pmc_openaccess", "")).lower() in {"yes", "true", "1"}
        is_retracted = str(metadata.get("is_retracted", "")).lower() in {"yes", "true", "1"}
        if is_open_access and not is_retracted and metadata.get("xml_url"):
            versions.append((str(metadata.get("is_manuscript", "")).lower() not in {"yes", "true", "1"},
                             str(metadata["xml_url"]), metadata.get("license_code")))
    if not versions:
        raise NoOpenAccessVersionError(f"No non-retracted Open Access Subset version is available for {identifier}")

    # Prefer the published version over an author manuscript when both are licensed.
    _, xml_url, license_code = next(
        (version for version in versions if version[0]), versions[0]
    )
    xml_url = _https_object_url(xml_url)
    xml_content = client.get_binary_url(xml_url, f"{cache_key}_jats")
    ET.fromstring(xml_content)
    return xml_content, license_code


def fetch_all(config_path: Path, limit: int | None = None) -> tuple[int, int]:
    """Attempt full-text retrieval for every PMID that has a PMC identifier."""
    config = load_config(config_path)
    root_dir = config_path.parent.parent
    output_path = root_dir / config["output"]["articles_jsonl"]
    articles = read_jsonl(output_path)
    text_dir = root_dir / config["output"]["article_text_dir"]
    raw_dir = root_dir / config["output"]["raw_dir"] / "pmc"
    metadata_dir = root_dir / config["output"]["metadata_dir"]
    client = NCBIClient(config, root_dir / "cache" / "eutils")
    failures: list[dict[str, Any]] = []
    completed = 0
    attempted = 0
    for article in tqdm(articles, desc="Checking PMC full text", unit="article"):
        pmcid = article.get("pmcid")
        pmid = article.get("pmid")
        if (
            not pmcid
            or not pmid
            or article.get("full_text")
            or article.get("full_text_status") == "not_in_open_access_subset"
        ):
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        try:
            key = f"pmc_{pmid}"
            xml_content, license_code = fetch_open_access_jats(client, str(pmcid), key)
            text, sections = extract_jats(xml_content)
            if not text:
                raise ValueError(f"JATS XML contained no extractable text for PMID {pmid}")
            text_dir.mkdir(parents=True, exist_ok=True)
            (text_dir / f"{pmid}.txt").write_text(text, encoding="utf-8")
            article["full_text"] = text
            article["full_text_source"] = "PMC"
            article["full_text_license"] = license_code
            article["full_text_status"] = "available"
            article["full_text_sections"] = sections
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{pmid}.xml").write_bytes(xml_content)
            completed += 1
        except NoOpenAccessVersionError as error:
            LOGGER.info("No eligible PMC full text for PMID %s: %s", pmid, error)
            article["full_text_status"] = "not_in_open_access_subset"
        except (requests.RequestException, ET.ParseError, ValueError, RuntimeError) as error:
            LOGGER.warning("PMC full-text fetch failed for PMID %s: %s", pmid, error)
            failures.append({"record_type": "pmc_fulltext", "pmid": pmid, "pmcid": pmcid, "error": str(error)})
    write_jsonl(output_path, articles)
    existing_failures = read_jsonl(metadata_dir / "failed_records.jsonl")
    write_jsonl(metadata_dir / "failed_records.jsonl", existing_failures + failures)
    LOGGER.info("PMC full text: %d fetched; %d attempted", completed, attempted)
    return completed, attempted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/literature_config.yaml"))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fetch_all(args.config, args.limit)


if __name__ == "__main__":
    main()
