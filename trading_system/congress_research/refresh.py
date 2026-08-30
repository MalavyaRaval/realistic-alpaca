"""Convenience entry point: re-fetch the latest disclosures and re-ingest.

    python refresh.py            # only fetch filers not already cached
    python refresh.py --force    # re-fetch every filer file from scratch

Safe to re-run any time - fetch_source.py skips already-cached files
unless --force is given, and ingest.py upserts by transaction id, so
running this repeatedly never creates duplicate rows.
"""

import sys

from fetch_source import fetch_and_cache_all_filers
from ingest import run_ingest


def main():
    force = "--force" in sys.argv
    fetch_and_cache_all_filers(force=force)
    run_ingest()


if __name__ == "__main__":
    main()
