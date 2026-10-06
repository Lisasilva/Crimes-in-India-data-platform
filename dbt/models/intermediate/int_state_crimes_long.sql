-- One row per state, year and crime type, with every correction applied:
--   1. 2020-2021 rows relabelled (int_kaggle_relabelled)
--   2. state-years missing from Kaggle filled from the NCRB 2001-2010 file,
--      which matches Kaggle exactly wherever both have data
--      (see mart_source_comparison)
--   3. known source errors set to missing or dropped (known_source_errors seed)
with kaggle as (
    unpivot {{ ref('int_kaggle_relabelled') }}
    on rape, kidnapping_abduction, dowry_deaths, assault_on_women,
       insult_to_modesty, cruelty_by_husband, importation_of_girls
    into name crime_code value cases
),

kaggle_named as (
    select
        s.state_name,
        s.analysis_unit,
        k.year,
        k.crime_code,
        k.cases,
        'kaggle'            as source,
        k.was_relabelled
    from kaggle as k
    join {{ ref('state_names') }} as s on s.name_key = k.corrected_name_key
),

ncrb_named as (
    select
        s.state_name,
        s.analysis_unit,
        n.year,
        c.crime_code,
        n.cases_reported    as cases,
        'ncrb_2001_2010'    as source,
        false               as was_relabelled
    from {{ ref('stg_ncrb__cases_2001_2010') }} as n
    join {{ ref('state_names') }} as s on s.name_key = n.name_key
    join {{ ref('crime_types') }} as c on c.ncrb_sub_group = n.sub_group_name
),

combined as (
    select * from kaggle_named
    union all
    -- Only state-years that Kaggle does not have (Delhi 2001-2010).
    select n.*
    from ncrb_named as n
    where not exists (
        select 1 from kaggle_named as k
        where k.state_name = n.state_name and k.year = n.year
    )
),

errors as (
    select * from {{ ref('known_source_errors') }}
),

matched as (
    -- A row can match more than one rule (for example a state-wide rule and an
    -- all-states rule), so rules are combined per row: any "drop" wins.
    select
        c.state_name, c.analysis_unit, c.year, c.crime_code, c.cases,
        c.source, c.was_relabelled,
        bool_or(e.action = 'drop')                                     as is_dropped,
        bool_or(e.action = 'set_missing')                              as is_missing,
        string_agg(e.reason, ' ' order by e.reason)                    as data_issue
    from combined as c
    left join errors as e
        on (e.analysis_unit = c.analysis_unit or e.analysis_unit = '*')
       and e.year = c.year
       and (e.crime_code = c.crime_code or e.crime_code = '*')
    group by all
)

select
    state_name,
    analysis_unit,
    year,
    crime_code,
    case when is_missing then null else cases end    as cases,
    source,
    was_relabelled,
    data_issue
from matched
where is_dropped is not true
