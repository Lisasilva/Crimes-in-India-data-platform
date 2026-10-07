-- One row per state, year and crime type, from NCRB's crimes-against-women
-- tables. The crime_head_columns seed (source ncrb_women_heads) says which
-- column makes up each crime type in each year; a type made of several columns
-- (assault on women in 2024) is their sum, and is left empty if any is missing.
with heads as (
    select
        *,
        count(*) over (partition by year_from, crime_group) as columns_in_group
    from {{ ref('crime_head_columns') }}
    where source = 'ncrb_women_heads'
)

select
    s.state_name,
    s.analysis_unit,
    c.year,
    h.crime_group                                                               as crime_code,
    case when count(*) = any_value(h.columns_in_group) then sum(c.cases) end   as cases
from {{ ref('stg_ncrb__women_heads') }} as c
join heads as h
    on h.column_name = c.column_name
   and c.year between h.year_from and h.year_to
join {{ ref('state_names') }} as s on s.name_key = c.name_key
group by all
