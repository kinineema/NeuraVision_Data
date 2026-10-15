"""Offline tests for the computed literature validation summary."""

from scripts.validate_literature import validate_articles


def test_validation_summary_counts_and_detects_duplicates() -> None:
    config = {
        "target_per_disease": 1,
        "date_range": {"start_year": 2018, "end_year": 2026},
        "diseases": {"epilepsy": {"label": "Epilepsy"}},
        "categories": {"clinical": {"target_share": 1.0}},
    }
    article = {
        "article_id": "PMID_1",
        "pmid": "1",
        "doi": "10.1/example",
        "title": "A paper",
        "abstract": "Abstract",
        "publication_year": 2024,
        "language": "eng",
        "full_text": None,
        "disease_tags": ["epilepsy"],
        "category_tags": ["clinical"],
        "authors": [],
    }
    summary = validate_articles([article, dict(article)], config)
    assert summary["total_unique_articles"] == 2
    assert summary["disease_counts"] == {"epilepsy": 2}
    assert summary["category_counts"] == {"clinical": 2}
    assert summary["year_distribution"] == {"2024": 2}
    assert summary["abstract_only"] == 2
    assert summary["duplicate_checks"]["duplicate_pmids"] == 1
    assert summary["validation_passed"] is False


def test_valid_one_article_summary_passes() -> None:
    config = {
        "target_per_disease": 1,
        "date_range": {"start_year": 2018, "end_year": 2026},
        "diseases": {"stroke": {"label": "Stroke"}},
        "categories": {"review": {"target_share": 1.0}},
    }
    article = {
        "article_id": "PMID_9", "pmid": "9", "doi": None, "title": "A review",
        "abstract": "Abstract", "publication_year": 2022, "language": "eng",
        "full_text": "Full text", "disease_tags": ["stroke"], "category_tags": ["review"],
        "authors": [],
    }
    summary = validate_articles([article], config, target_override=1)
    assert summary["validation_passed"] is True
    assert summary["disease_coverage_met"] is True
    assert summary["with_pmc_full_text"] == 1
