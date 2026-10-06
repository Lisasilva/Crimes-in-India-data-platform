"""End-to-end run on the real raw files in data/raw."""

import json

from pipeline.run import main


def test_pipeline_runs_on_the_committed_data(tmp_path):
    exit_code = main(["--db", str(tmp_path / "crime.duckdb"), "--reports", str(tmp_path)])
    report = json.loads((tmp_path / "data_quality_report.json").read_text())

    assert exit_code == 0
    assert report["summary"]["critical_failures"] == 0

    by_check = {(r["source"], r["check"]): r for r in report["results"]}
    # Known problems in the sources must keep being detected.
    assert not by_check[("kaggle_state_crimes", "no_copied_years")]["passed"]
    assert not by_check[("kaggle_state_crimes", "no_empty_measure_years")]["passed"]
    assert not by_check[("ncrb_cases_2001_2010", "totals_match_parts")]["passed"]
    assert not by_check[("ncrb_cases_2001_2010", "labels_consistent")]["passed"]
