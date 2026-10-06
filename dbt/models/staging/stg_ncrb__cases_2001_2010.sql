-- Typed copy of the NCRB 2001-2010 file, one row per state, year and crime head.
select
    _line_number                                     as source_line_number,
    trim("Area_Name")                                as state_label,
    {{ normalise_state('"Area_Name"') }}             as name_key,
    cast("Year" as integer)                          as year,
    trim("Group_Name")                               as group_name,
    trim("Sub_Group_Name")                           as sub_group_name,
    cast("Cases_Reported" as bigint)                 as cases_reported,
    cast("Cases_Chargesheeted" as bigint)            as cases_chargesheeted,
    cast("Cases_Convicted" as bigint)                as cases_convicted,
    cast("Cases_Trials_Completed" as bigint)         as cases_trials_completed
from {{ source('bronze', 'ncrb_cases_2001_2010') }}
