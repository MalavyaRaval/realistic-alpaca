"""Fetches raw congressional stock-transaction disclosure data.

Source: kadoa-org/congress-trading-monitor (MIT licensed), a GitHub-hosted,
actively-maintained dataset that parses the OFFICIAL public disclosure
portals - the U.S. House Clerk's Financial Disclosure system and the
Senate's Electronic Financial Disclosure (eFD) system - into structured
JSON, per the STOCK Act's public-disclosure requirement. Every record it
provides retains a `doc_url` pointing to the specific original government
filing, which this module treats as the authoritative source citation for
each transaction, not the aggregator itself.

This module only ever fetches and caches raw data - it does no parsing,
filtering, or interpretation. See ingest.py for that.
"""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "kadoa-org/congress-trading-monitor"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/main/public/data"
API_BASE = f"https://api.github.com/repos/{REPO}"

DATA_DIR = Path(__file__).resolve().parent / "data"
RAW_DIR = DATA_DIR / "raw"
FILER_DIR = RAW_DIR / "filer"


def _get(url: str, retries: int = 3) -> bytes:
    last_error = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 403:
                time.sleep(2 * (attempt + 1))  # likely rate-limited
                continue
            raise
    raise last_error


def list_filer_files() -> list:
    """Returns the list of per-filer data file paths in the source repo -
    each contains one politician's complete disclosed trade history."""
    tree_url = f"{API_BASE}/git/trees/main?recursive=1"
    tree = json.loads(_get(tree_url))
    return sorted(
        item["path"] for item in tree["tree"]
        if item["path"].startswith("public/data/filer/") and item["path"].endswith(".json")
    )


def fetch_filer(path: str) -> dict:
    """path is e.g. 'public/data/filer/house_nancy_pelosi.json'."""
    filename = path.rsplit("/", 1)[-1]
    url = f"{RAW_BASE}/filer/{filename}"
    return json.loads(_get(url))


def fetch_stats() -> dict:
    """The source's own summary stats - fetched for cross-reference only,
    never presented as this module's own claim."""
    return json.loads(_get(f"{RAW_BASE}/stats.json"))


def fetch_and_cache_all_filers(force: bool = False) -> list:
    """Downloads every filer's full trade history to data/raw/filer/,
    skipping files already cached unless force=True. Returns the list of
    local file paths."""
    FILER_DIR.mkdir(parents=True, exist_ok=True)
    filer_paths = list_filer_files()
    print(f"Found {len(filer_paths)} filer files in source repo")

    local_paths = []
    for i, path in enumerate(filer_paths, start=1):
        filename = path.rsplit("/", 1)[-1]
        local_path = FILER_DIR / filename
        if local_path.exists() and not force:
            local_paths.append(local_path)
            continue
        data = fetch_filer(path)
        local_path.write_text(json.dumps(data), encoding="utf-8")
        local_paths.append(local_path)
        if i % 50 == 0:
            print(f"  fetched {i}/{len(filer_paths)}")
    print(f"Cached {len(local_paths)} filer files to {FILER_DIR}")
    return local_paths


if __name__ == "__main__":
    stats = fetch_stats()
    print("Source's own summary stats (for cross-reference only):")
    print(json.dumps(stats, indent=2))
    fetch_and_cache_all_filers()
