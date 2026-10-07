-- Grain: one row per analysis unit, year and crime group, for all IPC/BNS crimes.
-- other_ipc is total_ipc minus the named groups. Rates use the Census 2011
-- population, the latest census available.
with by_unit as (
    select
        analysis_unit,
        year,
        crime_group,
        -- A merged unit is only complete if every part reported a figure.
        case when count(cases) = count(*) then sum(cases) end      as cases,
        string_agg(distinct source, ', ')                           as source
    from {{ ref('int_crimes_by_group') }}
    where is_primary
    group by analysis_unit, year, crime_group
),

with_other as (
    select * from by_unit
    union all
    select
        analysis_unit,
        year,
        'other_ipc'                                                     as crime_group,
        any_value(cases) filter (where crime_group = 'total_ipc')
            - sum(cases) filter (where crime_group <> 'total_ipc')
            -- empty if any named group is empty, rather than overstated
            + case when count(cases) = count(*) then 0 end              as cases,
        any_value(source)                                               as source
    from by_unit
    group by analysis_unit, year
)

select
    o.analysis_unit || '|' || o.year || '|' || o.crime_group       as fact_id,
    o.analysis_unit,
    o.year,
    o.crime_group,
    o.cases,
    p.population                                                    as population_2011,
    round(o.cases * 100000.0 / p.population, 2)                     as rate_per_100k,
    o.source
from with_other as o
left join {{ ref('population_2011') }} as p
    on p.analysis_unit = o.analysis_unit
   and o.year between p.valid_from_year and p.valid_to_year
