"""Bronze layer: load raw files into DuckDB exactly as they arrive.

Values are kept as text so nothing is silently coerced or dropped; typing
and cleaning happen in later layers. The only change made here is repairing
rows that a CSV parser cannot read at all, and every repair is logged.
"""

import csv
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from pipeline.sources import CONTRACTS, MANIFEST_PATH, RAW_DIR, SourceContract

log = logging.getLogger(__name__)


class ChecksumMismatchError(Exception):
    """A raw file no longer matches the checksum recorded in the manifest."""


@dataclass
class LoadResult:
    source: str
    file: str
    sha256: str
    rows_read: int
    rows_repaired: int
    rows_rejected: int
    header: list[str]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(manifest_path: Path = MANIFEST_PATH) -> list[dict]:
    with manifest_path.open() as f:
        return yaml.safe_load(f)["sources"]


def verify_checksum(path: Path, expected: str) -> str:
    actual = sha256_of(path)
    if actual != expected:
        raise ChecksumMismatchError(
            f"{path.name}: expected sha256 {expected}, found {actual}. "
            "If the file was replaced on purpose, update data/raw/manifest.yml."
        )
    return actual


def repair_row(row: list[str], contract: SourceContract) -> list[str] | None:
    """Return a row with the expected number of fields, or None if it cannot be repaired.

    The NCRB file has crime-head names such as "Immoral Traffic Prevention Act, 1956"
    written without quotes, so the comma splits one value into two fields.
    """
    expected = len(contract.columns)
    if len(row) == expected:
        return row
    if contract.unquoted_comma_column and len(row) == expected + 1:
        i = contract.columns.index(contract.unquoted_comma_column)
        return row[:i] + [f"{row[i]},{row[i + 1]}"] + row[i + 2 :]
    return None


def read_raw_csv(path: Path, contract: SourceContract) -> tuple[pd.DataFrame, pd.DataFrame, int, list[str]]:
    """Read a raw CSV as text.

    Returns (rows, rejected rows, number of repaired rows, header as found in the file).
    Columns are named from the contract; the file's own header is returned so the
    quality step can detect schema drift.
    """
    rows, rejected = [], []
    repaired = 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        for line_number, row in enumerate(reader, start=2):
            if not row:
                continue
            fixed = repair_row(row, contract)
            if fixed is None:
                rejected.append(
                    {"line_number": line_number, "field_count": len(row), "raw_line": ",".join(row)}
                )
                continue
            if fixed is not row:
                repaired += 1
            rows.append([*fixed, line_number])

    data = pd.DataFrame(rows, columns=[*contract.columns, "_line_number"], dtype="string")
    data["_line_number"] = data["_line_number"].astype("int64")
    rejects = pd.DataFrame(rejected, columns=["line_number", "field_count", "raw_line"])
    return data, rejects, repaired, header


def ingest(con: duckdb.DuckDBPyConnection, raw_dir: Path = RAW_DIR, manifest: list[dict] | None = None) -> list[LoadResult]:
    """Load every manifest source into the bronze schema. Re-running replaces the tables."""
    manifest = manifest if manifest is not None else load_manifest()
    loaded_at = datetime.now(timezone.utc)

    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.load_log (
            source VARCHAR, file VARCHAR, sha256 VARCHAR, rows_read BIGINT,
            rows_repaired BIGINT, rows_rejected BIGINT, header VARCHAR[], loaded_at TIMESTAMPTZ
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bronze.rejected_rows (
            source VARCHAR, line_number BIGINT, field_count BIGINT, raw_line VARCHAR,
            loaded_at TIMESTAMPTZ
        )
        """
    )

    results = []
    for entry in manifest:
        contract = CONTRACTS[entry["name"]]
        path = raw_dir / entry["file"]
        checksum = verify_checksum(path, entry["sha256"])
        data, rejects, repaired, header = read_raw_csv(path, contract)
        data["_source_file"] = entry["file"]
        data["_loaded_at"] = loaded_at

        con.register("incoming", data)
        con.execute(f"CREATE OR REPLACE TABLE {contract.table} AS SELECT * FROM incoming")
        con.unregister("incoming")

        con.execute("DELETE FROM bronze.rejected_rows WHERE source = ?", [contract.name])
        if not rejects.empty:
            rejects.insert(0, "source", contract.name)
            rejects["loaded_at"] = loaded_at
            con.register("incoming_rejects", rejects)
            con.execute("INSERT INTO bronze.rejected_rows SELECT * FROM incoming_rejects")
            con.unregister("incoming_rejects")

        result = LoadResult(
            contract.name, entry["file"], checksum, len(data), repaired, len(rejects), header
        )
        con.execute(
            "INSERT INTO bronze.load_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [result.source, result.file, result.sha256, result.rows_read,
             result.rows_repaired, result.rows_rejected, result.header, loaded_at],
        )
        log.info(
            "%s: %d rows loaded, %d repaired, %d rejected",
            result.source, result.rows_read, result.rows_repaired, result.rows_rejected,
        )
        results.append(result)
    return results
