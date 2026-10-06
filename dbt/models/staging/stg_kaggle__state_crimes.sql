-- Typed copy of the Kaggle file, one row per state label and year, as published.
select
    cast(source_row_id as integer)              as source_row_id,
    _line_number                                as source_line_number,
    trim("State")                               as state_label,
    {{ normalise_state('"State"') }}            as name_key,
    cast("Year" as integer)                     as year,
    cast("Rape" as bigint)                      as rape,
    cast("K&A" as bigint)                       as kidnapping_abduction,
    cast("DD" as bigint)                        as dowry_deaths,
    cast("AoW" as bigint)                       as assault_on_women,
    cast("AoM" as bigint)                       as insult_to_modesty,
    cast("DV" as bigint)                        as cruelty_by_husband,
    cast("WT" as bigint)                        as importation_of_girls
from {{ source('bronze', 'kaggle_state_crimes') }}
