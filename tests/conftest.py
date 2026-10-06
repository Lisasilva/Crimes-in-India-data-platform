import duckdb
import pytest

from pipeline.ingest import sha256_of

KAGGLE_HEADER = ",State,Year,Rape,K&A,DD,AoW,AoM,DV,WT"


@pytest.fixture
def con():
    with duckdb.connect(":memory:") as connection:
        yield connection


@pytest.fixture
def write_source(tmp_path):
    """Write a raw CSV and return a manifest entry for it with a correct checksum."""

    def _write(name: str, filename: str, text: str) -> dict:
        path = tmp_path / filename
        path.write_text(text, encoding="utf-8")
        return {"name": name, "file": filename, "sha256": sha256_of(path)}

    return _write


def kaggle_csv(*rows: str) -> str:
    return "\n".join([KAGGLE_HEADER, *rows]) + "\n"
