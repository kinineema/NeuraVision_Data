"""Offline tests for canonical article normalization and deduplication."""

from pathlib import Path
from xml.etree import ElementTree as ET

from scripts.fetch_pmc_fulltext import extract_jats, fetch_open_access_jats
from scripts.literature_utils import merge_articles, normalized_title, parse_pubmed_article
from scripts.normalize_articles import normalize
from tests.test_pubmed_ingestion import ARTICLE_XML


def test_duplicate_merge_keeps_all_tags_and_identifiers() -> None:
    first = {
        "article_id": "PMID_1", "pmid": "1", "doi": "10.1/a", "title": "Same title",
        "disease_tags": ["epilepsy"], "category_tags": ["clinical"],
        "publication_types": ["Trial"], "mesh_terms": [], "keywords": [], "authors": [],
    }
    second = {
        "article_id": "PMID_2", "pmid": "2", "doi": "10.1/a", "title": "Same title",
        "disease_tags": ["stroke"], "category_tags": ["imaging"],
        "publication_types": ["Review"], "mesh_terms": ["Stroke"], "keywords": [], "authors": [],
    }
    merged, report = merge_articles([first, second])
    assert len(merged) == 1
    assert merged[0]["disease_tags"] == ["epilepsy", "stroke"]
    assert merged[0]["category_tags"] == ["clinical", "imaging"]
    assert report[0]["matched_on"] == "doi"
    assert report[0]["disease_tags_merged"] == "epilepsy|stroke"


def test_normalized_schema_defaults_and_title_normalization(tmp_path: Path) -> None:
    article = parse_pubmed_article(ET.fromstring(ARTICLE_XML), [], [], {})
    normalized = normalize(article, tmp_path)
    assert normalized["article_id"] == "PMID_12345678"
    assert normalized["full_text"] is None
    assert normalized_title("Neural—Biomarker Study!") == "neural biomarker study"


def test_extract_jats_sections_and_captions() -> None:
    xml = b"""<article><front><article-meta><title-group><article-title>Study title</article-title></title-group>
      <abstract><p>Abstract text.</p></abstract></article-meta></front>
      <body><sec><title>Results</title><p>Main finding.</p><fig><caption><p>Figure caption.</p></caption></fig>
      <table-wrap><caption><p>Table caption.</p></caption><table><tr><td>Table value</td></tr></table></table-wrap>
      </sec></body><back><ref-list><ref><mixed-citation>Omitted reference.</mixed-citation></ref></ref-list></back></article>"""
    text, sections = extract_jats(xml)
    assert "Main finding." in text
    assert "Figure caption." in text
    assert "Table value" in text
    assert "Omitted reference." not in text
    assert sections[0]["heading"] == "Results"


def test_pmc_dataset_access_requires_open_license() -> None:
    class Client:
        def get_public_url(self, url: str, params: dict[str, str], cache_key: str) -> bytes:
            return b"""<ListBucketResult><CommonPrefixes><Prefix>PMC123.1/</Prefix></CommonPrefixes></ListBucketResult>"""

        def get_binary_url(self, url: str, cache_key: str) -> bytes:
            if "/metadata/" in url:
                return b'{"is_pmc_openaccess":"yes","is_manuscript":"no","is_retracted":"no","license_code":"CC BY","xml_url":"https://example.org/PMC123.1.xml"}'
            return b"<article><body><sec><title>Methods</title><p>Study text.</p></sec></body></article>"

    xml, license_code = fetch_open_access_jats(Client(), "PMC123", "test")
    assert b"Study text" in xml
    assert license_code == "CC BY"
