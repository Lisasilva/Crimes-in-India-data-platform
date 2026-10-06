import pytest

from pipeline.ingest import ChecksumMismatchError, ingest, repair_row
from pipeline.sources import KAGGLE_STATE_CRIMES, NCRB_CASES_2001_2010
from tests.conftest import kaggle_csv


def test_loads_values_as_text_and_logs_the_load(con, tmp_path, write_source):
    entry = write_source(
        "kaggle_state_crimes", "k.csv",
        kaggle_csv("0,Kerala,2001,10,5,1,20,3,40,0", "1,Goa,2001,007,0,0,1,0,2,0"),
    )
    [result] = ingest(con, raw_dir=tmp_path, manifest=[entry])

    assert result.rows_read == 2
    assert result.header == KAGGLE_STATE_CRIMES.expected_header
    # Leading zeros survive because bronze keeps the raw text.
    assert con.sql('SELECT "Rape" FROM bronze.kaggle_state_crimes WHERE "State" = \'Goa\'').fetchone()[0] == "007"
    assert con.sql("SELECT count(*) FROM bronze.load_log").fetchone()[0] == 1


def test_rerun_replaces_tables_instead_of_appending(con, tmp_path, write_source):
    entry = write_source("kaggle_state_crimes", "k.csv", kaggle_csv("0,Kerala,2001,1,1,1,1,1,1,1"))
    ingest(con, raw_dir=tmp_path, manifest=[entry])
    ingest(con, raw_dir=tmp_path, manifest=[entry])
    assert con.sql("SELECT count(*) FROM bronze.kaggle_state_crimes").fetchone()[0] == 1


def test_refuses_a_file_that_changed_since_the_manifest(con, tmp_path, write_source):
    entry = write_source("kaggle_state_crimes", "k.csv", kaggle_csv("0,Kerala,2001,1,1,1,1,1,1,1"))
    (tmp_path / "k.csv").write_text(kaggle_csv("0,Kerala,2001,9,9,9,9,9,9,9"))
    with pytest.raises(ChecksumMismatchError):
        ingest(con, raw_dir=tmp_path, manifest=[entry])


def test_repairs_an_unquoted_comma_in_the_crime_head():
    row = ["Goa", "2001", "Immoral Traffic", "08. Immoral Traffic Prevention Act", " 1956", *["0"] * 18]
    fixed = repair_row(row, NCRB_CASES_2001_2010)
    assert len(fixed) == len(NCRB_CASES_2001_2010.columns)
    assert fixed[3] == "08. Immoral Traffic Prevention Act, 1956"


def test_unrepairable_rows_are_rejected_and_kept(con, tmp_path, write_source):
    entry = write_source(
        "kaggle_state_crimes", "k.csv",
        kaggle_csv("0,Kerala,2001,1,1,1,1,1,1,1", "1,Goa,2001,1,1"),
    )
    [result] = ingest(con, raw_dir=tmp_path, manifest=[entry])
    assert (result.rows_read, result.rows_rejected) == (1, 1)
    line, raw = con.sql("SELECT line_number, raw_line FROM bronze.rejected_rows").fetchone()
    assert (line, raw) == (3, "1,Goa,2001,1,1")
