"""Data quality checks on the bronze layer.

Each check returns a CheckResult. Critical failures stop the pipeline, because
later layers cannot be trusted if they fail (missing columns, unreadable
numbers, duplicate keys). Warnings are real issues that are reported and then
handled by the cleaning layer.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from pipeline.sources import CONTRACTS, SEEDS_DIR, TABLE_CONTRACTS, SourceContract, TableSourceContract

CRITICAL = "critical"
WARNING = "warning"
MAX_EXAMPLES = 5

# A value is flagged when it grows or shrinks at least this many times in one
# year and the larger of the two values is at least OUTLIER_MIN_VALUE.
OUTLIER_RATIO = 5
OUTLIER_MIN_VALUE = 100


@dataclass
class CheckResult:
    check: str
    source: str
    severity: str
    passed: bool
    failing_rows: int
    message: str
    examples: list[dict] = field(default_factory=list)


def q(column: str) -> str:
    """Quote a column name for SQL (some raw names contain '&')."""
    return '"' + column.replace('"', '""') + '"'


def state_key(column: str) -> str:
    """SQL expression that makes state names comparable across years.

    The raw files mix 'ANDHRA PRADESH' and 'Andhra Pradesh', 'D & N HAVELI' and
    'D&N Haveli', and 'Delhi UT' and 'Delhi'.
    """
    upper = f"upper(trim({q(column)}))"
    spaced = rf"regexp_replace({upper}, '\s*&\s*', ' & ', 'g')"
    return rf"regexp_replace({spaced}, '\s+UT$', '')"


def as_int(column: str) -> str:
    return f"TRY_CAST(nullif(trim({q(column)}), '') AS BIGINT)"


def fetch_dicts(con: duckdb.DuckDBPyConnection, sql: str, params=None) -> list[dict]:
    cursor = con.execute(sql, params or [])
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def result(check, contract, severity, failing, message, examples=None) -> CheckResult:
    return CheckResult(
        check=check,
        source=contract.name,
        severity=severity,
        passed=failing == 0,
        failing_rows=failing,
        message=message,
        examples=(examples or [])[:MAX_EXAMPLES],
    )


# --- structural checks (critical) ------------------------------------------


def check_schema(con, c: SourceContract) -> CheckResult:
    row = con.execute(
        "SELECT header FROM bronze.load_log WHERE source = ? ORDER BY loaded_at DESC LIMIT 1",
        [c.name],
    ).fetchone()
    found = list(row[0]) if row else []
    expected = c.expected_header
    missing = [col for col in expected if col not in found]
    unexpected = [col for col in found if col not in expected]
    reordered = not missing and not unexpected and found != expected
    problems = len(missing) + len(unexpected) + int(reordered)
    if problems == 0:
        message = f"Header matches the contract ({len(expected)} columns)."
    else:
        message = f"Schema drift: missing {missing}, unexpected {unexpected}, reordered={reordered}."
    return result("schema_matches_contract", c, CRITICAL, problems, message)


def check_not_empty(con, c: SourceContract) -> CheckResult:
    n = con.execute(f"SELECT count(*) FROM {c.table}").fetchone()[0]
    return result("table_not_empty", c, CRITICAL, int(n == 0), f"{n} rows loaded.")


def check_rejected_rows(con, c: SourceContract) -> CheckResult:
    examples = fetch_dicts(
        con,
        "SELECT line_number, field_count, raw_line FROM bronze.rejected_rows WHERE source = ? ORDER BY line_number",
        [c.name],
    )
    return result(
        "no_rejected_rows", c, CRITICAL, len(examples),
        f"{len(examples)} raw lines could not be parsed or repaired.", examples,
    )


def check_missing_keys(con, c: SourceContract) -> CheckResult:
    condition = " OR ".join(f"nullif(trim({q(k)}), '') IS NULL" for k in c.key_columns)
    examples = fetch_dicts(con, f"SELECT _line_number, * EXCLUDE (_line_number, _source_file, _loaded_at) FROM {c.table} WHERE {condition}")
    return result(
        "no_missing_keys", c, CRITICAL, len(examples),
        f"{len(examples)} rows have an empty key column ({', '.join(c.key_columns)}).", examples,
    )


def check_invalid_numbers(con, c: SourceContract) -> CheckResult:
    numeric = [c.year_column, *c.measure_columns]
    parts = [
        f"SELECT _line_number, '{col}' AS column_name, {q(col)} AS value FROM {c.table} "
        f"WHERE nullif(trim({q(col)}), '') IS NOT NULL AND {as_int(col)} IS NULL"
        for col in numeric
    ]
    examples = fetch_dicts(con, " UNION ALL ".join(parts) + " ORDER BY _line_number")
    return result(
        "numbers_are_valid", c, CRITICAL, len(examples),
        f"{len(examples)} values in numeric columns are not whole numbers.", examples,
    )


def check_negative_counts(con, c: SourceContract) -> CheckResult:
    parts = [
        f"SELECT _line_number, '{col}' AS column_name, {as_int(col)} AS value FROM {c.table} "
        f"WHERE {as_int(col)} < 0"
        for col in c.measure_columns
    ]
    examples = fetch_dicts(con, " UNION ALL ".join(parts) + " ORDER BY _line_number")
    return result(
        "counts_not_negative", c, CRITICAL, len(examples),
        f"{len(examples)} case counts are negative.", examples,
    )


def check_duplicate_keys(con, c: SourceContract) -> CheckResult:
    keys = [state_key(k) if k == c.state_column else q(k) for k in c.key_columns]
    examples = fetch_dicts(
        con,
        f"""
        SELECT {", ".join(f"{expr} AS {q(k)}" for expr, k in zip(keys, c.key_columns))},
               count(*) AS copies, list(_line_number ORDER BY _line_number) AS line_numbers
        FROM {c.table}
        GROUP BY ALL
        HAVING count(*) > 1
        ORDER BY copies DESC
        """,
    )
    failing = sum(e["copies"] for e in examples)
    return result(
        "keys_are_unique", c, CRITICAL, failing,
        f"{len(examples)} key values appear more than once ({failing} rows).", examples,
    )


# --- content checks (warnings) ---------------------------------------------


def check_missing_measures(con, c: SourceContract) -> CheckResult:
    selects = ", ".join(
        f"count(*) FILTER (WHERE nullif(trim({q(col)}), '') IS NULL) AS {q(col)}"
        for col in c.measure_columns
    )
    counts = fetch_dicts(con, f"SELECT {selects} FROM {c.table}")[0]
    missing = {col: n for col, n in counts.items() if n}
    examples = [{"column": col, "missing": n} for col, n in missing.items()]
    total = sum(missing.values())
    return result(
        "no_missing_measures", c, WARNING, total,
        f"{total} empty values across {len(missing)} of {len(c.measure_columns)} measure columns.",
        examples,
    )


def check_label_consistency(con, c: SourceContract) -> CheckResult:
    if not c.label_columns:
        return result("labels_consistent", c, WARNING, 0, "Not applicable.")
    group, detail = (q(col) for col in c.label_columns)
    examples = fetch_dicts(
        con,
        f"""
        SELECT {group} AS group_name, list(DISTINCT {detail} ORDER BY {detail}) AS detail_labels,
               count(*) AS rows
        FROM {c.table}
        GROUP BY 1
        HAVING count(DISTINCT {detail}) > 1
        """,
    )
    failing = sum(e["rows"] for e in examples)
    return result(
        "labels_consistent", c, WARNING, failing,
        f"{len(examples)} {c.label_columns[0]} values cover more than one "
        f"{c.label_columns[1]} ({failing} rows).",
        examples,
    )


def check_totals(con, c: SourceContract) -> CheckResult:
    if not c.total_label:
        return result("totals_match_parts", c, WARNING, 0, "Not applicable.")
    group = q(c.label_columns[0])
    reported = as_int("Cases_Reported")
    examples = fetch_dicts(
        con,
        f"""
        WITH parts AS (
            SELECT {q(c.state_column)} AS state, {q(c.year_column)} AS year,
                   sum({reported}) FILTER (WHERE {group} <> ?) AS sum_of_parts,
                   sum({reported}) FILTER (WHERE {group} = ?) AS reported_total,
                   count(*) FILTER (WHERE {group} = ?) AS total_rows
            FROM {c.table}
            GROUP BY 1, 2
        )
        SELECT state, year, reported_total, sum_of_parts,
               CASE WHEN total_rows = 0 THEN 'total row missing' ELSE 'total differs' END AS issue
        FROM parts
        WHERE total_rows = 0 OR reported_total <> sum_of_parts
        ORDER BY abs(coalesce(reported_total, 0) - sum_of_parts) DESC
        """,
        [c.total_label] * 3,
    )
    missing = sum(e["issue"] == "total row missing" for e in examples)
    return result(
        "totals_match_parts", c, WARNING, len(examples),
        f"{len(examples) - missing} state-years where the reported total differs from the "
        f"sum of crime heads; {missing} state-years with no total row.",
        examples,
    )


def check_year_coverage(con, c: SourceContract) -> CheckResult:
    year = as_int(c.year_column)
    examples = fetch_dicts(
        con,
        f"""
        WITH present AS (SELECT DISTINCT {state_key(c.state_column)} AS state, {year} AS year FROM {c.table}),
             years AS (SELECT range AS year FROM range((SELECT min(year) FROM present), (SELECT max(year) FROM present) + 1)),
             states AS (SELECT DISTINCT state FROM present)
        SELECT s.state, list(y.year ORDER BY y.year) AS missing_years, count(*) AS missing_count
        FROM states s CROSS JOIN years y
        LEFT JOIN present p ON p.state = s.state AND p.year = y.year
        WHERE p.state IS NULL
        GROUP BY s.state
        ORDER BY missing_count DESC
        """,
    )
    failing = sum(e["missing_count"] for e in examples)
    return result(
        "every_state_every_year", c, WARNING, failing,
        f"{len(examples)} states are missing {failing} state-years in total.", examples,
    )


def check_all_zero_rows(con, c: SourceContract) -> CheckResult:
    condition = " AND ".join(f"coalesce({as_int(col)}, 0) = 0" for col in c.trend_columns)
    examples = fetch_dicts(
        con,
        f"SELECT _line_number, {q(c.state_column)} AS state, {q(c.year_column)} AS year "
        f"FROM {c.table} WHERE {condition} ORDER BY _line_number",
    )
    return result(
        "no_all_zero_rows", c, WARNING, len(examples),
        f"{len(examples)} rows report zero for every measure (often a state that did not exist "
        "yet, or data that was never filled in).",
        examples,
    )


def check_zero_measure_years(con, c: SourceContract) -> CheckResult:
    year = as_int(c.year_column)
    parts = [
        f"SELECT {year} AS year, '{col}' AS column_name FROM {c.table} "
        f"GROUP BY 1 HAVING sum({as_int(col)}) = 0 AND (SELECT sum({as_int(col)}) FROM {c.table}) > 0"
        for col in c.trend_columns
    ]
    examples = fetch_dicts(con, " UNION ALL ".join(parts) + " ORDER BY year")
    n_states = con.execute(f"SELECT count(DISTINCT {state_key(c.state_column)}) FROM {c.table}").fetchone()[0]
    return result(
        "no_empty_measure_years", c, WARNING, len(examples),
        f"{len(examples)} year/measure combinations are zero for every state "
        f"(up to {n_states} rows each), which points to missing data rather than no crime.",
        examples,
    )


def series_key(c: SourceContract) -> str:
    parts = [state_key(col) if col == c.state_column else q(col) for col in c.series_columns]
    return " || ' | ' || ".join(parts)


def check_repeated_years(con, c: SourceContract) -> CheckResult:
    if len(c.trend_columns) < 3:
        return result("no_copied_years", c, WARNING, 0, "Not applicable (too few measures to compare).")
    vector = "[" + ", ".join(as_int(col) for col in c.trend_columns) + "]"
    total = " + ".join(f"coalesce({as_int(col)}, 0)" for col in c.trend_columns)
    examples = fetch_dicts(
        con,
        f"""
        SELECT {series_key(c)} AS series, {vector} AS values,
               list({as_int(c.year_column)} ORDER BY {as_int(c.year_column)}) AS years
        FROM {c.table}
        WHERE {total} > 0
        GROUP BY 1, 2
        HAVING count(*) > 1
        """,
    )
    failing = sum(len(e["years"]) - 1 for e in examples)
    return result(
        "no_copied_years", c, WARNING, failing,
        f"{len(examples)} cases where a state reports exactly the same figures in different "
        "years, which usually means a copy-paste error in the source.",
        examples,
    )


def check_year_over_year_outliers(con, c: SourceContract) -> CheckResult:
    year = as_int(c.year_column)
    parts = [
        f"""
        SELECT series, year, '{col}' AS column_name, previous, value
        FROM (
            SELECT {series_key(c)} AS series, {year} AS year, {as_int(col)} AS value,
                   lag({as_int(col)}) OVER (PARTITION BY {series_key(c)} ORDER BY {year}) AS previous,
                   lag({year}) OVER (PARTITION BY {series_key(c)} ORDER BY {year}) AS previous_year
            FROM {c.table}
        )
        WHERE previous_year = year - 1
          AND greatest(value, previous) >= {OUTLIER_MIN_VALUE}
          AND (value >= {OUTLIER_RATIO} * previous OR previous >= {OUTLIER_RATIO} * value)
        """
        for col in c.trend_columns
    ]
    examples = fetch_dicts(
        con,
        "SELECT * FROM (" + " UNION ALL ".join(parts) + ") ORDER BY abs(value - previous) DESC",
    )
    return result(
        "no_sudden_jumps", c, WARNING, len(examples),
        f"{len(examples)} values changed by {OUTLIER_RATIO}x or more from the previous year.",
        examples,
    )


CHECKS = [
    check_schema,
    check_not_empty,
    check_rejected_rows,
    check_missing_keys,
    check_invalid_numbers,
    check_negative_counts,
    check_duplicate_keys,
    check_missing_measures,
    check_label_consistency,
    check_totals,
    check_year_coverage,
    check_all_zero_rows,
    check_zero_measure_years,
    check_repeated_years,
    check_year_over_year_outliers,
]


# --- checks for the wide crime-head tables -----------------------------------
#
# These sources are stored as one row per cell. The checks look at the cells
# the models actually use, as listed in the crime_head_columns seed, on rows
# that are a known state, a district or a total row.


def sql_text(value) -> str:
    """A string literal for SQL statements that cannot take parameters (views)."""
    return "'" + str(value).replace("'", "''") + "'"


def used_cells(con, c: TableSourceContract, seeds_dir: Path) -> str:
    """Create temporary views of the mapping seed and the used cells; return the cells view.

    row_type is 'city' for the city section of the older NCRB tables (it
    repeats some state names, such as Delhi), 'total' for total rows, 'state'
    for rows whose state name is in the state_names seed (districts, in the
    district files) and 'other' for anything else, such as footnotes extracted
    from the PDFs.
    """
    city = "t.section ILIKE 'CIT%'" if "section" in c.id_columns else "false"
    view = f"used_{c.name}"
    totals = ", ".join(sql_text(label) for label in c.total_labels)
    # Same as the normalise_state dbt macro.
    key = (
        rf"upper(regexp_replace(regexp_replace(trim(t.{c.state_column}), '[\s*@+#‐-]+$', ''), "
        rf"'\s*&\s*', ' & ', 'g'))"
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW heads_{c.name} AS
        SELECT *, {c.mapped_to_column} AS mapped_to
        FROM read_csv({sql_text(seeds_dir / c.mapping_seed)}, all_varchar = true)
        WHERE source = {sql_text(c.name)}
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW {view} AS
        WITH heads AS (SELECT * FROM heads_{c.name}),
        states AS (SELECT name_key FROM read_csv({sql_text(seeds_dir / "state_names.csv")}, all_varchar = true))
        SELECT t.*, {key} AS name_key, h.mapped_to,
               CASE
                   WHEN {city} THEN 'city'
                   WHEN upper(trim(t.{c.total_column})) IN ({totals}) THEN 'total'
                   WHEN {key} IN (SELECT name_key FROM states) THEN 'state'
                   ELSE 'other'
               END AS row_type,
               regexp_matches(upper(t.{c.total_column}), 'ALL.INDIA') AS is_all_india
        FROM {c.table} AS t
        JOIN heads AS h
          ON h.column_name = t.column_name
         AND TRY_CAST(t.year AS INTEGER) BETWEEN CAST(h.year_from AS INTEGER) AND CAST(h.year_to AS INTEGER)
        """
    )
    return view


def check_table_not_empty(con, c: TableSourceContract, view: str) -> CheckResult:
    n = con.execute(f"SELECT count(*) FROM {c.table}").fetchone()[0]
    return result("table_not_empty", c, CRITICAL, int(n == 0), f"{n} cells loaded.")


def check_table_rejected_rows(con, c: TableSourceContract, view: str) -> CheckResult:
    return check_rejected_rows(con, c)


def check_mapped_columns_present(con, c: TableSourceContract, view: str) -> CheckResult:
    """Every column named in the mapping seed exists in the file for that year."""
    examples = fetch_dicts(
        con,
        f"""
        WITH expected AS (
            SELECT y.year, h.mapped_to, h.column_name
            FROM heads_{c.name} AS h,
                 range(CAST(h.year_from AS INTEGER), CAST(h.year_to AS INTEGER) + 1) AS y(year)
        ),
        found AS (SELECT DISTINCT TRY_CAST(year AS INTEGER) AS year, column_name FROM {c.table})
        SELECT e.year, e.mapped_to, e.column_name
        FROM expected AS e
        LEFT JOIN found AS f USING (year, column_name)
        WHERE f.column_name IS NULL
        ORDER BY e.year, e.mapped_to
        """,
    )
    return result(
        "mapped_columns_present", c, CRITICAL, len(examples),
        f"{len(examples)} year/column pairs in the crime-head mapping are missing from the files.",
        examples,
    )


def check_table_numbers(con, c: TableSourceContract, view: str) -> CheckResult:
    """Case counts are whole numbers; populations and rates are decimals.

    A decimal may carry one of NCRB's footnote marks (for example "509.2*"),
    which the staging models strip.
    """
    decimals = ", ".join(sql_text(m) for m in c.decimal_measures) or "''"
    examples = fetch_dicts(
        con,
        f"""
        SELECT _source_file, _line_number, {c.state_column} AS state, column_name, value
        FROM {view}
        WHERE row_type IN ('state', 'total') AND value <> ''
          AND CASE WHEN mapped_to IN ({decimals})
                   THEN NOT regexp_full_match(value, '[0-9]+(\\.[0-9]+)? ?[*#+@]*')
                   ELSE NOT regexp_full_match(value, '[0-9]+')
              END
        ORDER BY _source_file, _line_number
        """,
    )
    return result(
        "numbers_are_valid", c, CRITICAL, len(examples),
        f"{len(examples)} used values on state or total rows are not valid numbers "
        "(whole numbers for cases, decimals for populations and rates).", examples,
    )


def check_table_duplicates(con, c: TableSourceContract, view: str) -> CheckResult:
    """The rows the models read (state totals, or state rows) hold one value per column."""
    used = "total" if c.row_column else "state"
    examples = fetch_dicts(
        con,
        f"""
        SELECT year, name_key, column_name, count(*) AS copies,
               list(_line_number ORDER BY _line_number) AS line_numbers
        FROM {view}
        WHERE row_type = '{used}' AND value <> ''
        GROUP BY ALL
        HAVING count(*) > 1
        ORDER BY copies DESC
        """,
    )
    failing = sum(e["copies"] for e in examples)
    return result(
        "keys_are_unique", c, CRITICAL, failing,
        f"{len(examples)} {used} rows have more than one value for the same year and column "
        f"({failing} cells).",
        examples,
    )


def check_repeated_rows(con, c: TableSourceContract, view: str) -> CheckResult:
    """Rows the models do not read directly (districts) appear once per state and year."""
    if not c.row_column:
        return result("rows_not_repeated", c, WARNING, 0, "Not applicable (state rows are checked by keys_are_unique).")
    examples = fetch_dicts(
        con,
        f"""
        SELECT year, name_key, {c.row_column}, list(DISTINCT _line_number ORDER BY _line_number) AS line_numbers
        FROM {view}
        WHERE row_type = 'state'
        GROUP BY ALL
        HAVING count(DISTINCT _line_number) > 1
        ORDER BY year, name_key
        """,
    )
    return result(
        "rows_not_repeated", c, WARNING, len(examples),
        f"{len(examples)} {c.row_column} names appear on more than one row in the same state and year. "
        "The models use the state total rows, so this does not change any figure.",
        examples,
    )


def check_table_missing_values(con, c: TableSourceContract, view: str) -> CheckResult:
    examples = fetch_dicts(
        con,
        f"""
        SELECT year, mapped_to, column_name, count(*) AS empty_cells
        FROM {view}
        WHERE row_type IN ('state', 'total') AND value = ''
        GROUP BY ALL
        ORDER BY year, mapped_to
        """,
    )
    failing = sum(e["empty_cells"] for e in examples)
    return result(
        "no_missing_measures", c, WARNING, failing,
        f"{failing} used values are empty on state or total rows.", examples,
    )


def check_unrecognised_rows(con, c: TableSourceContract, view: str) -> CheckResult:
    examples = fetch_dicts(
        con,
        f"""
        SELECT _source_file, _line_number, {c.state_column} AS state, count(*) AS numbers
        FROM {view}
        WHERE row_type = 'other' AND regexp_full_match(value, '[0-9]+')
        GROUP BY ALL
        ORDER BY _source_file, _line_number
        """,
    )
    return result(
        "no_unrecognised_rows", c, WARNING, len(examples),
        f"{len(examples)} rows carry figures but are neither a known state nor a total row, "
        "so the models skip them. Add the state name to the state_names seed if it is real.",
        examples,
    )


def check_table_totals(con, c: TableSourceContract, view: str) -> CheckResult:
    """District rows add up to the state total row; state rows add up to the all-India row."""
    if c.row_column:
        parts, total, group = "row_type = 'state'", "row_type = 'total'", "year, name_key, column_name"
        what = "state-year columns where the districts do not add up to the state total row"
    else:
        parts, total, group = "row_type = 'state'", "is_all_india", "year, column_name"
        what = "year/columns where the states and union territories do not add up to the all-India row"
    decimals = ", ".join(sql_text(m) for m in c.decimal_measures) or "''"
    examples = fetch_dicts(
        con,
        f"""
        WITH sums AS (
            SELECT {group}, any_value(mapped_to) AS mapped_to,
                   sum(TRY_CAST(value AS BIGINT)) FILTER (WHERE {parts}) AS sum_of_parts,
                   sum(TRY_CAST(value AS BIGINT)) FILTER (WHERE {total}) AS reported_total
            FROM {view}
            WHERE mapped_to NOT IN ({decimals})
            GROUP BY ALL
        )
        SELECT * FROM sums
        WHERE reported_total IS DISTINCT FROM sum_of_parts
        ORDER BY abs(coalesce(reported_total, 0) - coalesce(sum_of_parts, 0)) DESC
        """,
    )
    return result("totals_match_parts", c, WARNING, len(examples), f"{len(examples)} {what}.", examples)


TABLE_CHECKS = [
    check_table_not_empty,
    check_table_rejected_rows,
    check_mapped_columns_present,
    check_table_numbers,
    check_table_duplicates,
    check_repeated_rows,
    check_table_missing_values,
    check_unrecognised_rows,
    check_table_totals,
]


def run_checks(con: duckdb.DuckDBPyConnection, contracts=None, table_contracts=None,
               seeds_dir: Path = SEEDS_DIR) -> list[CheckResult]:
    """Run every check. Passing only `contracts` (as the unit tests do) skips the table sources."""
    if contracts is None and table_contracts is None:
        contracts, table_contracts = CONTRACTS.values(), TABLE_CONTRACTS.values()
    results = [check(con, contract) for contract in contracts or [] for check in CHECKS]
    for contract in table_contracts or []:
        view = used_cells(con, contract, seeds_dir)
        results += [check(con, contract, view) for check in TABLE_CHECKS]
    return results


def has_critical_failures(results: list[CheckResult]) -> bool:
    return any(r.severity == CRITICAL and not r.passed for r in results)


# --- reporting ---------------------------------------------------------------


def _json_default(value):
    return str(value)


def write_report(results: list[CheckResult], out_dir: Path) -> tuple[Path, Path]:
    """Write the report as JSON (for machines) and Markdown (for people)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    failed = [r for r in results if not r.passed]
    summary = {
        "generated_at": generated_at,
        "checks_run": len(results),
        "checks_passed": len(results) - len(failed),
        "critical_failures": sum(r.severity == CRITICAL for r in failed),
        "warnings": sum(r.severity == WARNING for r in failed),
    }

    json_path = out_dir / "data_quality_report.json"
    json_path.write_text(
        json.dumps({"summary": summary, "results": [asdict(r) for r in results]},
                   indent=2, default=_json_default)
    )

    lines = [
        "# Data quality report",
        "",
        f"Generated {generated_at}. {summary['checks_passed']} of {summary['checks_run']} checks passed; "
        f"{summary['critical_failures']} critical failures, {summary['warnings']} warnings.",
        "",
        "| Source | Check | Severity | Result | Rows affected | Detail |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        status = "pass" if r.passed else "FAIL"
        lines.append(
            f"| {r.source} | {r.check} | {r.severity} | {status} | {r.failing_rows} | {r.message} |"
        )
    for r in failed:
        if not r.examples:
            continue
        lines += ["", f"## {r.source}: {r.check}", "", r.message, "", "Examples:", "", "```"]
        lines += [json.dumps(e, default=_json_default) for e in r.examples]
        lines.append("```")
    md_path = out_dir / "data_quality_report.md"
    md_path.write_text("\n".join(lines) + "\n")
    return json_path, md_path
