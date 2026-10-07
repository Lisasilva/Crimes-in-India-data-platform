# Data quality: what was found and what the pipeline does about it

Every problem below is detected by an automated check and handled by a
reviewed rule in the repository. Nothing is fixed by hand, and nothing is
guessed: a figure known to be wrong or missing stays missing.

## How problems are handled

| Layer | Where | What happens |
| --- | --- | --- |
| Download | `pipeline/fetch.py` | NCRB tables are downloaded from one fixed commit of reclaimchennai/NCRB and must match the sha256 in `data/raw/manifest.yml`. |
| Bronze | `pipeline/ingest.py` | Every cell is loaded as text, with its file, line and column name, so nothing is coerced or lost. |
| Checks | `pipeline/quality.py` | 75 checks. Critical checks stop the pipeline; warnings are reported in `reports/data_quality_report.md`. |
| Silver | `dbt/models/staging`, `dbt/models/intermediate` | Columns are mapped to crime groups and state names to analysis units, using the seed files in `dbt/seeds`. |
| Gold | `dbt/models/marts` | 118 dbt tests, including tests that reproduce NCRB's published totals and rates (see `dbt/tests`). |

## Checks against NCRB's own figures

These run on every build and are the main evidence that the numbers are right.

| Check | Result | Test |
| --- | --- | --- |
| State figures add up to NCRB's all-India row in every table | Pass | `totals_match_parts` in `pipeline/quality.py` |
| Total IPC/BNS cases per state match NCRB's summary table | 1 difference: Meghalaya 2004, 1,752 by crime head against 1,757 in the summary (warning) | `assert_total_ipc_matches_ncrb_summary.sql` |
| Total crimes against women match NCRB's summary table | Pass | `assert_women_total_matches_ncrb_summary.sql` |
| Rates computed here reproduce NCRB's printed rates | Pass | `assert_rates_match_ncrb_published.sql` |
| The women tables and the all-crimes tables agree where they overlap | 4,927 of 4,927 figures match | `gold.mart_crime_source_overlap` |
| The 2014 district file agrees with NCRB table 1.6 | 749 match, 21 differ, all West Bengal (warning) | `assert_2014_sources_agree.sql` |

## Problems found

### NCRB Crime in India tables, 2014-2024

| Problem | Evidence | Action | Rule |
| --- | --- | --- | --- |
| Crime heads are renamed and regrouped in 2014, 2017 and 2024, when the Bharatiya Nyaya Sanhita replaced the IPC | Column names differ between years | Each column in each year is mapped to one stable crime group; a mapped column that disappears stops the run | `seeds/crime_head_columns.csv`, check `mapped_columns_present` |
| Hurt is not comparable in 2024 | The BNS grievous hurt group also counts some simple hurt | Hurt has no 2024 figure | `seeds/crime_head_columns.csv` |
| Importation of girls is missing from the 2016 table | Column not present | Left missing | `seeds/crime_head_columns.csv` |
| State names carry footnote marks (`Delhi*`, `Gujarat@`) and older spellings (Orissa, Uttaranchal, Pondicherry) | Check `no_unrecognised_rows` | Trailing marks are stripped and old spellings mapped to one name | `macros/normalise_state.sql`, `seeds/state_names.csv` |
| Tables mix state rows with city rows and several kinds of total row (states, UTs, all-India) | Row labels | Rows are classified as state, total, city or other; only state and total rows are used and checked | `pipeline/quality.py` |
| 13 rows in the 2014 table hold column formulas ("Col. = Col. 43 + ...") instead of a state | Check `no_unrecognised_rows` (warning) | Skipped | none needed |
| Manipur's 2018 row in table 1A.4 is partly empty in the extracted file | Check `no_missing_measures` (warning, 22 cells) | Left missing | none needed |
| Population and rate columns contain blanks and dashes | Cast failures | Read with `try_cast`; a `not_null` test catches any real gap | `models/staging/stg_ncrb__population.sql` |

### District-wise IPC files, 2001-2014 (data.gov.in)

| Problem | Evidence | Action |
| --- | --- | --- |
| One district name appears twice in the same state and year | Check `rows_not_repeated` (warning) | No effect: the models use the state total rows |
| West Bengal 2014 differs from NCRB table 1.6 in 21 figures | `assert_2014_sources_agree.sql` | NCRB's table is used for 2014; the district file is kept only for this cross-check |

### Kaggle copy of the women data, 2001-2021 (backup only)

This file was the source of the original project. It is still loaded and
compared with NCRB's tables in `gold.mart_crime_source_overlap`: 4,509
figures match, 392 differ and 48 are missing from one of the two. It is not
used for any chart. The problems below explain most of the differences.

| Problem | Evidence | Rule |
| --- | --- | --- |
| 2020 and 2021 rows carry the wrong state labels from Jammu & Kashmir onward. For example, the row labelled "Rajasthan" held 12 rapes and the row labelled "Punjab" 5,310. | The figures follow NCRB's 2020 state order; matching each row to the closest 2019 profile gives the same mapping | `seeds/kaggle_relabel_2020_2021.csv`, tested by `tests/assert_relabelled_2020_follows_2019.sql` |
| Assault on women is 0 for every state in 2011 | Check `no_empty_measure_years` | `seeds/known_source_errors.csv` |
| West Bengal 2019 repeats 2018 exactly, and Jharkhand 2004 repeats 2002 | Check `no_copied_years` | `seeds/known_source_errors.csv` |
| Telangana has rows of zeros for 2011-2013, before it was formed | Check `no_all_zero_rows` | `seeds/known_source_errors.csv` |
| Delhi is missing for 2001-2010 | Check `every_state_every_year` | Not needed now: NCRB's tables include Delhi |

### NCRB 2001-2010 file from the original notebook (backup only)

| Problem | Evidence |
| --- | --- |
| "Dowry Prohibition Act" rows are filed under the group "Importation of Girls" | Check `labels_consistent` |
| 2008 has no total rows, and in other years the published total differs from the sum of crime heads | Check `totals_match_parts` |

For 2001-2010, all 2,380 figures it shares with the Kaggle file are
identical (`gold.mart_source_comparison`).

## Choices that affect the numbers

- **Areas that changed.** Ladakh is counted with Jammu & Kashmir, and Dadra &
  Nagar Haveli with Daman & Diu, so each unit covers the same area in every
  year. Telangana appears from 2014; before that it is part of Andhra Pradesh.
- **Population.** Rates use the mid-year population NCRB itself used for its
  rates that year (`gold.dim_population`), so they can be checked against
  NCRB's printed rates.
- **Female population.** NCRB publishes it from 2012 (tables 5.1 and 3A.1).
  Before 2012 it is estimated as NCRB's total population times the state's
  female share in the 2011 census; `female_population_basis` in
  `gold.fct_crimes_against_women` says which applies.
- **Clustering window.** States are grouped on their average rates for
  2017-2021 (`profile_start_year` and `profile_end_year` in `dbt/dbt_project.yml`),
  the same window for both views, so the two groupings can be compared.
