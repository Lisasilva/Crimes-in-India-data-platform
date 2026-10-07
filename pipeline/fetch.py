"""Download raw files that are not kept in the repository.

Manifest entries with a `url` are downloaded into data/raw when missing, and
every download must match the sha256 recorded in the manifest. The URLs point
at one fixed commit of the source repository, so the same files come back on
every run.

Usage:
    python -m pipeline.fetch
"""

import logging
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from pipeline.ingest import ChecksumMismatchError, load_manifest, sha256_of
from pipeline.sources import RAW_DIR

log = logging.getLogger(__name__)


def fetch(manifest: list[dict] | None = None, raw_dir: Path = RAW_DIR) -> list[Path]:
    """Download every manifest file that has a url and is missing locally.

    Returns the paths that were downloaded. A file that is already present is
    left alone; the ingestion step verifies its checksum.
    """
    manifest = manifest if manifest is not None else load_manifest()
    downloaded = []
    for entry in manifest:
        if "url" not in entry:
            continue
        path = raw_dir / entry["file"]
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                with urllib.request.urlopen(entry["url"], timeout=60) as response:
                    shutil.copyfileobj(response, tmp)
            except Exception:
                tmp_path.unlink()
                raise
        actual = sha256_of(tmp_path)
        if actual != entry["sha256"]:
            tmp_path.unlink()
            raise ChecksumMismatchError(
                f"{entry['url']}: expected sha256 {entry['sha256']}, downloaded {actual}."
            )
        tmp_path.replace(path)
        log.info("Downloaded %s", entry["file"])
        downloaded.append(path)
    return downloaded


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    fetch()
    sys.exit(0)
