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

CONTRACTS = {c.name: c for c in (KAGGLE_STATE_CRIMES, NCRB_CASES_2001_2010)}
