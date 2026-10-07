-- One row per source, state, year and crime group. The crime_head_columns seed
-- says which source columns make up each group in each year; a group made of
-- several columns (for example assault on women in 2024) is their sum, and is
-- left empty if any of those columns is missing for the state.
with cells as (
    select 'ipc_district' as source, year, name_key, column_name, cases
    from {{ ref('stg_ipc__state_totals') }}
    union all
    select 'ncrb_state_heads' as source, year, name_key, column_name, cases
    from {{ ref('stg_ncrb__state_heads') }}
),

heads as (
    select
        *,
        count(*) over (partition by source, year_from, crime_group) as columns_in_group
    from {{ ref('crime_head_columns') }}
)

select
    c.source,
    s.state_name,
    s.analysis_unit,
    c.year,
    h.crime_group,
    case when count(*) = any_value(h.columns_in_group) then sum(c.cases) end   as cases,
    -- NCRB's own tables are used from the first year they cover; the 2014
    -- district file is kept for the cross-check in mart_crime_source_overlap.
    c.source = 'ncrb_state_heads' or c.year < {{ var('ncrb_tables_from_year') }} as is_primary
from cells as c
join heads as h
    on h.source = c.source
   and h.column_name = c.column_name
   and c.year between h.year_from and h.year_to
join {{ ref('state_names') }} as s on s.name_key = c.name_key
group by all
