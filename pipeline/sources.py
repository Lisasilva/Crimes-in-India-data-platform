"""Source contracts: what each raw file is expected to look like.

The ingestion step loads files as they are, and the data quality step
compares what was loaded against these contracts. Changing a contract is a
deliberate decision, so it lives in code and goes through review.
"""

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "manifest.yml"
DEFAULT_DB_PATH = PROJECT_ROOT / "warehouse" / "crime.duckdb"
REPORTS_DIR = PROJECT_ROOT / "reports"
SEEDS_DIR = PROJECT_ROOT / "dbt" / "seeds"


@dataclass(frozen=True)
class SourceContract:
    name: str
    table: str
    columns: list[str]
    key_columns: list[str]
    measure_columns: list[str]
    state_column: str
    year_column: str
    # Columns that, together with the state, identify one yearly time series.
    series_columns: list[str]
    # Measures compared year over year by the trend checks.
    trend_columns: list[str]
    # Column that may legitimately contain commas without quoting in the raw
    # file. Rows with one extra field are repaired by re-joining this column.
    unquoted_comma_column: str | None = None
    label_columns: tuple[str, str] | None = None
    total_label: str | None = None
    # Header expected in the raw file, when it differs from `columns`.
    raw_header: list[str] | None = None

    @property
    def expected_header(self) -> list[str]:
        return self.raw_header if self.raw_header is not None else self.columns


KAGGLE_STATE_CRIMES = SourceContract(
    name="kaggle_state_crimes",
    table="bronze.kaggle_state_crimes",
    columns=["source_row_id", "State", "Year", "Rape", "K&A", "DD", "AoW", "AoM", "DV", "WT"],
    # The first column is an unnamed pandas index in the raw file.
    raw_header=["", "State", "Year", "Rape", "K&A", "DD", "AoW", "AoM", "DV", "WT"],
    key_columns=["State", "Year"],
    measure_columns=["Rape", "K&A", "DD", "AoW", "AoM", "DV", "WT"],
    state_column="State",
    year_column="Year",
    series_columns=["State"],
    trend_columns=["Rape", "K&A", "DD", "AoW", "AoM", "DV", "WT"],
)

NCRB_COLUMNS = [
    "Area_Name",
    "Year",
    "Group_Name",
    "Sub_Group_Name",
    "Cases_Acquitted_or_Discharged",
    "Cases_charge_sheets_were_not_laid_but_Final_Report_submitted",
    "Cases_Chargesheeted",
    "Cases_Compounded_or_Withdrawn",
    "Cases_Convicted",
    "Cases_Declared_False_on_Account_of_Mistake_of_Fact_or_of_Law",
    "Cases_Investigated_Chargesheets+FR_Submitted",
    "Cases_not_Investigated_or_in_which_investigation_was_refused",
    "Cases_Pending_Investigation_at_Year_End",
    "Cases_Pending_Investigation_from_previous_year",
    "Cases_Pending_Trial_at_Year_End",
    "Cases_Pending_Trial_from_the_previous_year",
    "Cases_Reported",
    "Cases_Sent_for_Trial",
    "Cases_Trials_Completed",
    "Cases_Withdrawn_by_the_Govt",
    "Cases_withdrawn_by_the_Govt_during_investigation",
    "Total_Cases_for_Trial",
]

NCRB_CASES_2001_2010 = SourceContract(
    name="ncrb_cases_2001_2010",
    table="bronze.ncrb_cases_2001_2010",
    columns=NCRB_COLUMNS,
    key_columns=["Area_Name", "Year", "Sub_Group_Name"],
    # Every column after the four descriptive ones is a case count.
    measure_columns=NCRB_COLUMNS[4:],
    state_column="Area_Name",
    year_column="Year",
    series_columns=["Area_Name", "Sub_Group_Name"],
    trend_columns=["Cases_Reported"],
    unquoted_comma_column="Sub_Group_Name",
    label_columns=("Group_Name", "Sub_Group_Name"),
    total_label="Total Crime Against Women",
)

@dataclass(frozen=True)
class TableSourceContract:
    """A wide table with one column per crime head, loaded as one row per cell.

    The crime-head columns change from year to year (new laws, renamed heads),
    so bronze stores them as (column_name, value) pairs instead of fixed
    columns. Which columns are used is decided by the reviewed seed
    dbt/seeds/crime_head_columns.csv.
    """

    name: str
    table: str
    # Names given to the leading descriptive columns, by position. Their raw
    # names differ between years ("STATE/UT" in 2013, "States/UTs" in 2014).
    id_columns: list[str]
    state_column: str
    # The district files have a YEAR column; the NCRB tables are one file per
    # year, so the year comes from the manifest.
    year_in_manifest: bool
    # Labels that mark a total row, compared in upper case. The district files
    # mark them in the district column, the NCRB tables in the state column.
    total_column: str
    total_labels: tuple[str, ...]
    # Column whose label identifies a row within one state and year.
    row_column: str | None = None
    # Reviewed seed (in dbt/seeds) that says which columns are used, and the
    # seed column holding what each one is mapped to.
    mapping_seed: str = "crime_head_columns.csv"
    mapped_to_column: str = "crime_group"
    # Mapped values that are decimals (populations in lakhs, published rates)
    # rather than case counts. They are not summed by the totals check.
    decimal_measures: tuple[str, ...] = ()


IPC_DISTRICT = TableSourceContract(
    name="ipc_district",
    table="bronze.ipc_district_cells",
    id_columns=["state", "district", "year"],
    state_column="state",
    year_in_manifest=False,
    total_column="district",
    total_labels=("TOTAL", "DELHI UT TOTAL", "ZZ TOTAL"),
    row_column="district",
)

# Total rows in NCRB's state tables. The spelling changes from year to year.
NCRB_TOTAL_LABELS = (
    "TOTAL STATE(S)", "TOTAL UT(S)", "TOTAL (ALL INDIA)", "TOTAL ALL INDIA",
    "TOTAL (STATES)", "TOTAL STATES", "TOTAL (UTS)", "TOTAL UT", "TOTAL (ALL-INDIA)",
    "TOTAL (CITIES)", "TOTAL STATE",
)

NCRB_STATE_HEADS = TableSourceContract(
    name="ncrb_state_heads",
    table="bronze.ncrb_state_cells",
    id_columns=["section", "sl_no", "state"],
    state_column="state",
    year_in_manifest=True,
    total_column="state",
    total_labels=NCRB_TOTAL_LABELS,
)

# Crimes against women by crime head: NCRB's consolidated 2001-2015 table and
# tables 3A.2(i) (IPC/BNS) and 3A.2(ii) (special and local laws) for 2016-2024.
NCRB_WOMEN_HEADS = TableSourceContract(
    name="ncrb_women_heads",
    table="bronze.ncrb_women_cells",
    id_columns=["section", "sl_no", "state"],
    state_column="state",
    year_in_manifest=True,
    total_column="state",
    total_labels=NCRB_TOTAL_LABELS,
)

# Total IPC/BNS crimes with NCRB's mid-year population and published crime
# rate: tables 1.6 (2001-2013), 1.4 (2014-2015) and 1A.1 (2016-2024).
NCRB_IPC_TOTALS = TableSourceContract(
    name="ncrb_ipc_totals",
    table="bronze.ncrb_ipc_total_cells",
    id_columns=["section", "sl_no", "state"],
    state_column="state",
    year_in_manifest=True,
    total_column="state",
    total_labels=NCRB_TOTAL_LABELS,
    mapping_seed="population_columns.csv",
    mapped_to_column="measure",
    decimal_measures=("population_lakhs", "published_rate"),
)

# Total crimes against women with NCRB's female population and published rate:
# table 5.1 (2012-2015) and 3A.1 (2016-2024). Before 2012 NCRB published the
# women's rate against the total population, so those years are not loaded.
NCRB_WOMEN_TOTALS = TableSourceContract(
    name="ncrb_women_totals",
    table="bronze.ncrb_women_total_cells",
    id_columns=["section", "sl_no", "state"],
    state_column="state",
    year_in_manifest=True,
    total_column="state",
    total_labels=NCRB_TOTAL_LABELS,
    mapping_seed="population_columns.csv",
    mapped_to_column="measure",
    decimal_measures=("population_lakhs", "published_rate"),
)

CONTRACTS = {c.name: c for c in (KAGGLE_STATE_CRIMES, NCRB_CASES_2001_2010)}
TABLE_CONTRACTS = {
    c.name: c
    for c in (IPC_DISTRICT, NCRB_STATE_HEADS, NCRB_WOMEN_HEADS, NCRB_IPC_TOTALS, NCRB_WOMEN_TOTALS)
}
