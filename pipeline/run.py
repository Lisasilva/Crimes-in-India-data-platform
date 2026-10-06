"""Run the pipeline: load raw files, check them, write the quality report.

Usage:
    python -m pipeline.run [--db PATH] [--reports DIR]

Exits with status 1 when a critical data quality check fails, so CI stops
before bad data reaches later layers.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import duckdb

from pipeline.ingest import ingest
from pipeline.quality import has_critical_failures, run_checks, write_report
from pipeline.sources import DEFAULT_DB_PATH, REPORTS_DIR

log = logging.getLogger("pipeline")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB file to build")
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR, help="where to write reports")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args.db.parent.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(args.db)) as con:
        ingest(con)
        results = run_checks(con)

    json_path, md_path = write_report(results, args.reports)
    log.info("Report written to %s and %s", json_path, md_path)

    # In GitHub Actions, show the report on the run's summary page.
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as f:
            f.write(md_path.read_text())

    if has_critical_failures(results):
        failed = [f"{r.source}.{r.check}" for r in results if r.severity == "critical" and not r.passed]
        log.error("Critical data quality checks failed: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
