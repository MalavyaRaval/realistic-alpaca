"""Verifies a disclosed transaction against its original source document
before it's ever allowed to become a trade candidate - the local
database is a convenience cache, not something to trust blindly right
before generating a real (paper) order.

Scope, stated plainly: this confirms the source URL is well-formed,
points to a recognized official disclosure domain, and actually
resolves. It does NOT parse the PDF/HTML content to cross-check the
disclosed ticker/amount/date against the document text - that would need
OCR/PDF-text-extraction beyond what this module attempts. A failed or
unreachable check fails closed: reject the candidate, never assume it's
fine because the local database says so.

House Clerk links verify fully (a direct 200 + application/pdf response,
confirmed reachable during an earlier review of this exact source).
Senate eFD links verify more weakly: an unauthenticated request lands on
the site's mandatory disclaimer page rather than the document itself (no
session cookie), so all this can confirm for Senate is that the domain
and path resolve without erroring - not that the specific PTR content is
reachable. That gap is a real reason (on top of the statutory use
restriction already documented elsewhere) this strategy defaults to
House-only.
"""

import urllib.error
import urllib.request
from urllib.parse import urlparse

ALLOWED_DOMAINS = {
    "disclosures-clerk.house.gov",
    "efdsearch.senate.gov",
}


def verify_source_document(url: str, timeout: float = 10.0) -> tuple:
    """Returns (verified: bool, reason: str). Never raises - any network
    failure is folded into a (False, reason) result so a candidate that
    can't be verified is rejected, not silently allowed through."""
    if not url:
        return False, "transaction has no source_document_url to verify"

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False, f"source URL is not https: {url!r}"
    if parsed.netloc not in ALLOWED_DOMAINS:
        return False, f"source URL domain {parsed.netloc!r} is not a recognized official disclosure domain"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        status, content_type = e.code, ""
    except Exception as e:
        return False, f"source document could not be reached: {e}"

    if parsed.netloc == "disclosures-clerk.house.gov":
        if status == 200 and "pdf" in content_type.lower():
            return True, f"House PTR document verified (HTTP {status}, {content_type})"
        return False, f"House PTR document did not verify as expected (HTTP {status}, content-type {content_type!r})"

    # efdsearch.senate.gov - see module docstring: this can only confirm
    # the domain/path resolves, since the actual PTR content sits behind
    # a disclaimer-acceptance session this check doesn't establish.
    if status == 200:
        return True, f"Senate eFD source path resolved (HTTP {status}) - weaker check, see module docstring"
    return False, f"Senate eFD source did not resolve as expected (HTTP {status})"
