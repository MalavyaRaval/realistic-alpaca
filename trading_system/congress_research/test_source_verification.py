"""Tests for source_verification.py - confirms it fails closed on every
bad input and network condition, and applies the stricter House check
vs. the documented weaker Senate check. All network access is
monkeypatched; nothing here makes a real HTTP request.
"""

import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import source_verification as sv


class FakeResponse:
    def __init__(self, status=200, content_type=""):
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_none_url_is_rejected():
    verified, reason = sv.verify_source_document(None)
    assert verified is False
    assert "no source_document_url" in reason


def test_empty_url_is_rejected():
    verified, reason = sv.verify_source_document("")
    assert verified is False


def test_non_https_url_is_rejected():
    verified, reason = sv.verify_source_document("http://disclosures-clerk.house.gov/example.pdf")
    assert verified is False
    assert "not https" in reason


def test_non_allowlisted_domain_is_rejected():
    verified, reason = sv.verify_source_document("https://example.com/fake.pdf")
    assert verified is False
    assert "not a recognized official disclosure domain" in reason


def test_house_pdf_200_verifies(monkeypatch):
    monkeypatch.setattr(sv.urllib.request, "urlopen", lambda req, timeout: FakeResponse(200, "application/pdf"))
    verified, reason = sv.verify_source_document("https://disclosures-clerk.house.gov/example.pdf")
    assert verified is True
    assert "House PTR document verified" in reason


def test_house_wrong_content_type_fails(monkeypatch):
    monkeypatch.setattr(sv.urllib.request, "urlopen", lambda req, timeout: FakeResponse(200, "text/html"))
    verified, reason = sv.verify_source_document("https://disclosures-clerk.house.gov/example.pdf")
    assert verified is False
    assert "did not verify as expected" in reason


def test_house_404_fails(monkeypatch):
    def raise_404(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(sv.urllib.request, "urlopen", raise_404)
    verified, reason = sv.verify_source_document("https://disclosures-clerk.house.gov/missing.pdf")
    assert verified is False
    assert "404" in reason


def test_senate_200_verifies_with_weaker_check(monkeypatch):
    monkeypatch.setattr(sv.urllib.request, "urlopen", lambda req, timeout: FakeResponse(200, "text/html"))
    verified, reason = sv.verify_source_document("https://efdsearch.senate.gov/search/view/ptr/example/")
    assert verified is True
    assert "weaker check" in reason


def test_senate_non_200_fails(monkeypatch):
    def raise_500(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, None)
    monkeypatch.setattr(sv.urllib.request, "urlopen", raise_500)
    verified, reason = sv.verify_source_document("https://efdsearch.senate.gov/search/view/ptr/example/")
    assert verified is False


def test_network_failure_fails_closed(monkeypatch):
    def raise_conn_error(req, timeout):
        raise TimeoutError("connection timed out")
    monkeypatch.setattr(sv.urllib.request, "urlopen", raise_conn_error)
    verified, reason = sv.verify_source_document("https://disclosures-clerk.house.gov/example.pdf")
    assert verified is False
    assert "could not be reached" in reason
