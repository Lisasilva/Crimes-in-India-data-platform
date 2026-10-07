"""Run the pipeline end to end.

    fetch   download pinned raw files not kept in git (pipeline/fetch.py)
    bronze  load raw files into DuckDB (pipeline/ingest.py)
    checks  data quality checks on bronze (pipeline/quality.py)
    dbt     silver and gold models, with their tests (dbt/)
    ml      state clustering on gold (pipeline/clustering.py)

Usage:
    python -m pipeline.run [--db PATH] [--reports DIR]

Exits with status 1 if a critical data quality check or a dbt test fails, so
CI stops before bad data reaches the website.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import duckdb
from dbt.cli.main import dbtRunner

from pipeline import clustering
from pipeline.fetch import fetch
from pipeline.ingest import ingest
from pipeline.quality import has_critical_failures, run_checks, write_report
from pipeline.sources import DEFAULT_DB_PATH, PROJECT_ROOT, REPORTS_DIR

log = logging.getLogger("pipeline")
DBT_DIR = PROJECT_ROOT / "dbt"


def run_dbt(db_path: Path) -> bool:
    os.environ["DUCKDB_PATH"] = str(db_path.resolve())
    result = dbtRunner().invoke(
        ["build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
    )
    return result.success


def append_to_step_summary(*paths: Path) -> None:
    """In GitHub Actions, show the reports on the run's summary page."""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as f:
            for path in paths:
                f.write(path.read_text() + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB file to build")
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR, help="where to write reports")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args.db.parent.mkdir(parents=True, exist_ok=True)

    fetch()
    with duckdb.connect(str(args.db)) as con:
        ingest(con)
        results = run_checks(con)

    _, quality_md = write_report(results, args.reports)
    append_to_step_summary(quality_md)
    if has_critical_failures(results):
        failed = [f"{r.source}.{r.check}" for r in results if r.severity == "critical" and not r.passed]
        log.error("Critical data quality checks failed: %s", ", ".join(failed))
        return 1

    if not run_dbt(args.db):
        log.error("dbt build failed; see the output above")
        return 1

    with duckdb.connect(str(args.db)) as con:
        results = clustering.run(con, args.reports)
    for view, result in results.items():
        log.info(
            "Clustering (%s): %d clusters (silhouette %.3f), unusual states: %s",
            view, result.best_k, result.best_silhouette, ", ".join(result.unusual_states) or "none",
        )
    append_to_step_summary(args.reports / "clustering_report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
