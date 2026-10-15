"""Offline tests for PubMed XML parsing and query-driven labels."""

from xml.etree import ElementTree as ET

from scripts.ingest_pubmed import build_query, category_targets
from scripts.literature_utils import (
    extract_doi,
    extract_pmid,
    parse_authors,
    parse_publication_year,
    parse_pubmed_article,
)

ARTICLE_XML = """<PubmedArticle>
  <MedlineCitation><PMID>12345678</PMID><Article>
    <Journal><Title>Journal of Neurology</Title><JournalIssue><PubDate><Year>2024</Year><Month>May</Month><Day>12</Day></PubDate></JournalIssue></Journal>
    <ArticleTitle>Neural biomarker study</ArticleTitle>
    <Abstract><AbstractText Label="BACKGROUND">First part.</AbstractText><AbstractText>Second part.</AbstractText></Abstract>
    <AuthorList><Author><LastName>Smith</LastName><ForeName>Jane</ForeName></Author></AuthorList>
    <Language>eng</Language><PublicationTypeList><PublicationType>Clinical Trial</PublicationType></PublicationTypeList>
    <MeshHeadingList><MeshHeading><DescriptorName>Epilepsy</DescriptorName></MeshHeading></MeshHeadingList>
    <KeywordList><Keyword>biomarker</Keyword></KeywordList>
  </Article></MedlineCitation>
  <PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/example</ArticleId><ArticleId IdType="pmc">PMC7654321</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle>"""


def test_parse_pubmed_xml_fields_and_doi_pmid() -> None:
    element = ET.fromstring(ARTICLE_XML)
    assert extract_pmid(element) == "12345678"
    assert extract_doi(element) == "10.1000/example"
    assert parse_publication_year(element) == 2024
    assert parse_authors(element) == [{"last_name": "Smith", "fore_name": "Jane"}]
    record = parse_pubmed_article(element, ["epilepsy"], ["clinical"], {"database": "pubmed"})
    assert record["abstract"] == "First part.\n\nSecond part."
    assert record["publication_date"] == "2024-05-12"
    assert record["pmcid"] == "PMC7654321"
    assert record["disease_tags"] == ["epilepsy"]
    assert record["category_tags"] == ["clinical"]


def test_query_and_category_quotas() -> None:
    query = build_query("disease", "review", 2018, 2026)
    assert query == "(disease) AND (review) AND 2018:2026[dp]"
    categories = {
        "review": {"target_share": 0.5},
        "clinical": {"target_share": 0.5},
    }
    assert category_targets(5, categories) == {"review": 3, "clinical": 2}


def test_missing_abstract_is_not_invented() -> None:
    element = ET.fromstring(ARTICLE_XML.replace(
        '<Abstract><AbstractText Label="BACKGROUND">First part.</AbstractText><AbstractText>Second part.</AbstractText></Abstract>',
        "",
    ))
    record = parse_pubmed_article(element, [], [], {})
    assert record["abstract"] is None
