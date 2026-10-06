# Data quality: what was found and what the pipeline does about it

Every problem below is detected by an automated check and handled by a
reviewed rule in the repository. Nothing is fixed by hand, and nothing is
guessed: a figure known to be wrong becomes missing, with the reason stored
next to it in `gold.fct_crimes_against_women.data_issue`.

## How problems are handled

| Layer | Where | What happens |
| --- | --- | --- |
| Bronze | `pipeline/ingest.py` | Raw files are loaded as text. Checksums are verified, rows broken by unquoted commas are repaired by rule, and rows that cannot be repaired are kept in `bronze.rejected_rows`. |
| Checks | `pipeline/quality.py` | Critical checks stop the pipeline; warnings are reported in `reports/data_quality_report.md`. |
| Silver | `dbt/models/intermediate` | Corrections from the seed files in `dbt/seeds` are applied. |
| Gold | `dbt/models/marts` | dbt tests confirm the corrections worked (see `dbt/tests`). |

## Problems found

### Kaggle 2001-2021 file

| Problem | Evidence | Action | Rule |
| --- | --- | --- | --- |
| 2020 and 2021 rows carry the wrong state labels from Jammu & Kashmir onward | The figures follow NCRB's 2020 state order, which moved Jammu & Kashmir to the union territories, merged Dadra & Nagar Haveli with Daman & Diu and added Ladakh. The labels kept the old order. For example, the row labelled "Rajasthan" held 12 rapes and the row labelled "Punjab" 5,310. Matching each row to the closest 2019 profile gives the same mapping. | Relabelled | `seeds/kaggle_relabel_2020_2021.csv`, tested by `tests/assert_relabelled_2020_follows_2019.sql` |
| Assault on women is 0 for every state in 2011 | `no_empty_measure_years` check | Set to missing | `seeds/known_source_errors.csv` |
| West Bengal 2019 repeats 2018 exactly | `no_copied_years` check | Set to missing | `seeds/known_source_errors.csv` |
| Jharkhand 2004 repeats 2002 exactly | `no_copied_years` check | Set to missing | `seeds/known_source_errors.csv` |
| Telangana has rows of zeros for 2011-2013, before it was formed | `no_all_zero_rows` check | Rows dropped; those years are inside Andhra Pradesh | `seeds/known_source_errors.csv` |
| Delhi is missing for 2001-2010 | `every_state_every_year` check | Filled from the NCRB 2001-2010 file | `models/intermediate/int_state_crimes_long.sql` |
| State names change case and spelling between years | `keys_are_unique` normalises names | Mapped to one name | `seeds/state_names.csv` |

### NCRB 2001-2010 file

| Problem | Evidence | Action |
| --- | --- | --- |
| 1,400 rows split by unquoted commas in crime-head names | Rows with 23 fields instead of 22 | Repaired at ingestion and counted in `bronze.load_log` |
| "Dowry Prohibition Act" rows are filed under the group "Importation of Girls" | `labels_consistent` check | Groups are not used; crime heads are matched on the detailed label |
| 2008 has no total rows, and in other years the published total is higher than the sum of crime heads in about 20 states a year | `totals_match_parts` check | Totals are not used; totals are recomputed from the crime heads |

### Cross-check between the two files

For 2001-2010, all 2,380 figures that appear in both files (34 states, 10
years, 7 crime heads) are identical. This is checked on every run by
`gold.mart_source_comparison` and its `accepted_values` test, and it is why the
NCRB file can safely fill Delhi's missing years.

## Choices that affect the numbers

- **Areas that changed.** Ladakh is counted with Jammu & Kashmir, and Dadra &
  Nagar Haveli with Daman & Diu, so each unit covers the same area in every
  year. Telangana appears from 2014; before that it is part of Andhra Pradesh.
- **Rates.** Rates per 100,000 women use the Census 2011 female population,
  the latest census. Populations have grown since, so later rates are slightly
  overstated, by the same proportion within a state.
- **"WT" column.** In the Kaggle file it matches NCRB's "Importation of Girls"
  exactly, so it is labelled that way here rather than as general trafficking.
