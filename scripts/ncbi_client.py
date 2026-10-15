"""Rate-limited, retrying client for the official NCBI E-utilities API."""

from __future__ import annotations

import os
import time
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests


class NCBIClient:
    """Minimal E-utilities client respecting NCBI request-rate guidance."""

    def __init__(self, config: dict[str, Any], cache_dir: Path) -> None:
        api_config = config.get("api", {})
        self.base_url = str(api_config.get("base_url", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils")).rstrip("/")
        self.timeout = int(config.get("request_timeout_seconds", 30))
        self.tool = str(config.get("tool_name", "NeuraVision_Data"))
        self.email = os.getenv("NCBI_EMAIL")
        self.api_key = os.getenv("NCBI_API_KEY")
        self.minimum_interval = 1 / (10 if self.api_key else 3)
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": f"{self.tool}/1.0"})
        self._last_request = 0.0

    def _request(
        self,
        url: str,
        params: dict[str, Any],
        cache_key: str | None = None,
        include_ncbi_identity: bool = False,
    ) -> bytes:
        """Fetch XML with a disk cache and bounded exponential-backoff retries."""
        fingerprint = hashlib.sha256(
            json.dumps({"url": url, "params": params}, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        cache_path = self.cache_dir / f"{cache_key}_{fingerprint}.bin" if cache_key else None
        if cache_path and cache_path.exists():
            return cache_path.read_bytes()
        request_params = dict(params)
        if include_ncbi_identity:
            request_params.update({
                "tool": self.tool,
                **({"email": self.email} if self.email else {}),
                **({"api_key": self.api_key} if self.api_key else {}),
            })
        last_error: Exception | None = None
        for attempt in range(5):
            wait = self.minimum_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                response = self.session.get(url, params=request_params, timeout=self.timeout)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                content = response.content
                if cache_path:
                    cache_path.write_bytes(content)
                return content
            except (requests.RequestException, ET.ParseError) as error:
                last_error = error
                status = getattr(getattr(error, "response", None), "status_code", None)
                transient = status == 429 or (status is not None and status >= 500) or status is None
                if not transient or attempt == 4:
                    raise RuntimeError(f"NCBI request failed for {url}: {error}") from error
                time.sleep(2**attempt)
        raise RuntimeError(f"NCBI request failed for {url}: {last_error}")

    def request_xml(self, endpoint: str, params: dict[str, Any], cache_key: str | None = None) -> bytes:
        """Fetch and validate an XML response."""
        url = f"{self.base_url}/{endpoint}"
        content = self._request(url, params, cache_key, include_ncbi_identity=True)
        try:
            ET.fromstring(content)
        except ET.ParseError as error:
            raise RuntimeError(f"NCBI returned malformed XML from {endpoint}: {error}") from error
        return content

    def get_public_url(self, url: str, params: dict[str, Any], cache_key: str) -> bytes:
        """Fetch an unauthenticated public PMC cloud object or listing."""
        content = self._request(url, params, cache_key)
        return content

    def get_binary_url(self, url: str, cache_key: str) -> bytes:
        """Fetch a public binary object without adding E-utilities identity params."""
        return self._request(url, {}, cache_key)

    def search(self, term: str, limit: int, cache_key: str, raw_path: Path | None = None) -> list[str]:
        """Run PubMed ESearch and return PMID strings."""
        content = self.request_xml(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": term,
                "retmax": limit,
                "retmode": "xml",
                "usehistory": "n",
            },
            cache_key,
        )
        if raw_path:
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(content)
        root = ET.fromstring(content)
        return [node.text for node in root.findall("./IdList/Id") if node.text]

    def fetch(self, pmids: list[str], cache_key: str) -> bytes:
        """Fetch one PubMed XML batch using EFetch."""
        if not pmids:
            return b""
        return self.request_xml(
            "efetch.fcgi",
            {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"},
            cache_key,
        )

    def fetch_pmc(self, pmcid: str, cache_key: str) -> bytes:
        """Fetch PMC JATS XML through EFetch; caller handles non-OA responses."""
        identifier = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
        return self.request_xml(
            "efetch.fcgi",
            {"db": "pmc", "id": quote(identifier), "retmode": "xml"},
            cache_key,
        )
