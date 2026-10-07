"""Wide crime-head tables: download, loading as cells, and their quality checks."""

import pytest

from pipeline.fetch import fetch
from pipeline.ingest import ChecksumMismatchError, ingest, sha256_of
from pipeline.quality import CRITICAL, run_checks
from pipeline.sources import IPC_DISTRICT, NCRB_STATE_HEADS

DISTRICT_HEADER = "STATE/UT,DISTRICT,YEAR,MURDER,TOTAL IPC CRIMES"
NCRB_HEADER = "section,sl_no,State/UT,Murder | I,Murder | R,Total Cognizable IPC crimes | I"


@pytest.fixture
def seeds(tmp_path):
    """A small mapping seed and state list, standing in for dbt/seeds."""
    seeds_dir = tmp_path / "seeds"
    seeds_dir.mkdir()
    (seeds_dir / "crime_head_columns.csv").write_text(
        "source,year_from,year_to,crime_group,column_name\n"
        "ipc_district,2001,2002,murder,MURDER\n"
        "ipc_district,2001,2002,total_ipc,TOTAL IPC CRIMES\n"
        "ncrb_state_heads,2016,2016,murder,Murder | I\n"
        "ncrb_state_heads,2016,2016,total_ipc,Total Cognizable IPC crimes | I\n"
    )
    (seeds_dir / "state_names.csv").write_text(
        "name_key,state_name,analysis_unit,unit_type\n"
        "GOA,Goa,Goa,State\n"
        "KERALA,Kerala,Kerala,State\n"
    )
    return seeds_dir


def district_csv(*rows):
    return "\n".join([DISTRICT_HEADER, *rows]) + "\n"


def ncrb_csv(*rows):
    return "\n".join([NCRB_HEADER, *rows]) + "\n"


CLEAN_DISTRICTS = [
    "GOA,NORTH GOA,2001,3,30", "GOA,SOUTH GOA,2001,2,20", "GOA,TOTAL,2001,5,50",
    "GOA,NORTH GOA,2002,1,10", "GOA,SOUTH GOA,2002,1,10", "GOA,TOTAL,2002,2,20",
]
CLEAN_NCRB = [
    "STATES:,1,Goa,5,0.3,50", "STATES:,2,Kerala,7,0.2,70",
    "STATES:,,TOTAL STATE(S),12,0.2,120", "UNION TERRITORIES:,,TOTAL (ALL INDIA),12,0.2,120",
]


def load(con, tmp_path, write_source, seeds, districts=CLEAN_DISTRICTS, ncrb=CLEAN_NCRB):
    manifest = [
        write_source("ipc_district", "d.csv", district_csv(*districts)),
        {**write_source("ncrb_state_heads", "n.csv", ncrb_csv(*ncrb)), "year": 2016},
    ]
    results = ingest(con, raw_dir=tmp_path, manifest=manifest)
    checks = run_checks(con, [], [IPC_DISTRICT, NCRB_STATE_HEADS], seeds_dir=seeds)
    return results, {(r.source, r.check): r for r in checks}


def test_tables_are_loaded_as_text_cells(con, tmp_path, write_source, seeds):
    results, _ = load(con, tmp_path, write_source, seeds)
    assert [r.rows_read for r in results] == [6, 4]
    # 4 rows x 3 measure columns, with the year taken from the manifest
    assert con.sql("SELECT count(*), any_value(year) FROM bronze.ncrb_state_cells").fetchone() == (12, "2016")
    value = con.sql(
        "SELECT value FROM bronze.ipc_district_cells WHERE district = 'TOTAL' AND year = '2001' "
        "AND column_name = 'MURDER'"
    ).fetchone()[0]
    assert value == "5"


def test_clean_tables_pass_every_check(con, tmp_path, write_source, seeds):
    _, checks = load(con, tmp_path, write_source, seeds)
    assert [key for key, r in checks.items() if not r.passed] == []


def test_districts_that_do_not_add_up_are_reported(con, tmp_path, write_source, seeds):
    districts = [*CLEAN_DISTRICTS[:2], "GOA,TOTAL,2001,6,50", *CLEAN_DISTRICTS[3:]]
    _, checks = load(con, tmp_path, write_source, seeds, districts=districts)
    totals = checks[("ipc_district", "totals_match_parts")]
    assert totals.failing_rows == 1
    assert totals.examples[0]["sum_of_parts"] == 5 and totals.examples[0]["reported_total"] == 6


def test_states_must_add_up_to_all_india(con, tmp_path, write_source, seeds):
    ncrb = [*CLEAN_NCRB[:3], "UNION TERRITORIES:,,TOTAL (ALL INDIA),13,0.2,120"]
    _, checks = load(con, tmp_path, write_source, seeds, ncrb=ncrb)
    assert checks[("ncrb_state_heads", "totals_match_parts")].failing_rows == 1


def test_missing_mapped_column_is_critical(con, tmp_path, write_source, seeds):
    with (seeds / "crime_head_columns.csv").open("a") as f:
        f.write("ncrb_state_heads,2016,2016,rape,Rape | I\n")
    _, checks = load(con, tmp_path, write_source, seeds)
    missing = checks[("ncrb_state_heads", "mapped_columns_present")]
    assert missing.severity == CRITICAL and missing.examples == [
        {"year": 2016, "crime_group": "rape", "column_name": "Rape | I"}
    ]


def test_bad_numbers_and_repeated_states_are_critical(con, tmp_path, write_source, seeds):
    ncrb = ["STATES:,1,Goa,5,0.3,5O", "STATES:,1,Goa,5,0.3,50", *CLEAN_NCRB[1:]]
    _, checks = load(con, tmp_path, write_source, seeds, ncrb=ncrb)
    assert checks[("ncrb_state_heads", "numbers_are_valid")].failing_rows == 1
    assert checks[("ncrb_state_heads", "keys_are_unique")].failing_rows == 4


def test_footnotes_with_figures_are_flagged_but_not_critical(con, tmp_path, write_source, seeds):
    ncrb = [*CLEAN_NCRB, "UNION TERRITORIES:,16,Col. 17 = Col. 20 + Col. 29,17,,"]
    _, checks = load(con, tmp_path, write_source, seeds, ncrb=ncrb)
    footnotes = checks[("ncrb_state_heads", "no_unrecognised_rows")]
    assert footnotes.failing_rows == 1 and footnotes.severity != CRITICAL
    assert checks[("ncrb_state_heads", "numbers_are_valid")].passed


def test_rows_with_the_wrong_field_count_are_rejected(con, tmp_path, write_source, seeds):
    results, checks = load(con, tmp_path, write_source, seeds, ncrb=[*CLEAN_NCRB, "STATES:,3,Goa,1"])
    assert results[1].rows_rejected == 1
    assert not checks[("ncrb_state_heads", "no_rejected_rows")].passed


def test_fetch_downloads_missing_files_and_checks_them(tmp_path):
    origin = tmp_path / "origin.csv"
    origin.write_text(ncrb_csv(*CLEAN_NCRB))
    entry = {"file": "ncrb/n.csv", "url": origin.as_uri(), "sha256": sha256_of(origin)}
    raw = tmp_path / "raw"

    assert fetch([entry], raw) == [raw / "ncrb" / "n.csv"]
    assert fetch([entry], raw) == []  # already there

    bad = {**entry, "file": "ncrb/other.csv", "sha256": "0" * 64}
    with pytest.raises(ChecksumMismatchError):
        fetch([bad], raw)
    assert not (raw / "ncrb" / "other.csv").exists()
