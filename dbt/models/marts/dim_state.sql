-- One row per analysis unit. Units follow today's boundaries where the data
-- allows it, and merge areas that were split or joined during 2001-2021 so
-- every unit means the same area in every year.
with units as (
    select
        analysis_unit,
        min(unit_type) filter (where state_name = analysis_unit)   as unit_type,
        string_agg(distinct state_name, ', ' order by state_name)   as includes
    from {{ ref('state_names') }}
    group by analysis_unit
),

coverage as (
    select analysis_unit, min(year) as first_year, max(year) as last_year
    from {{ ref('int_state_crimes_long') }}
    group by analysis_unit
)

select
    u.analysis_unit,
    u.unit_type,
    u.includes,
    c.first_year,
    c.last_year,
    p.female_population     as female_population_2011
from units as u
join coverage as c using (analysis_unit)
left join {{ ref('female_population_2011') }} as p
    on p.analysis_unit = u.analysis_unit
   and c.last_year between p.valid_from_year and p.valid_to_year
