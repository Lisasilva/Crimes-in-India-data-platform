-- Grain: one row per analysis unit, year and crime type.
-- Rates use the Census 2011 female population, the latest census available.
with by_unit as (
    select
        analysis_unit,
        year,
        crime_code,
        -- A merged unit is only complete if every part reported a figure.
        case when count(cases) = count(*) then sum(cases) end      as cases,
        string_agg(distinct source, ', ')                           as source,
        bool_or(was_relabelled)                                     as was_relabelled,
        string_agg(distinct data_issue, ' ')                        as data_issue
    from {{ ref('int_state_crimes_long') }}
    group by analysis_unit, year, crime_code
)

select
    b.analysis_unit || '|' || b.year || '|' || b.crime_code        as fact_id,
    b.analysis_unit,
    b.year,
    b.crime_code,
    b.cases,
    p.female_population                                             as female_population_2011,
    round(b.cases * 100000.0 / p.female_population, 2)              as rate_per_100k_women,
    b.source,
    b.was_relabelled,
    b.data_issue
from by_unit as b
left join {{ ref('female_population_2011') }} as p
    on p.analysis_unit = b.analysis_unit
   and b.year between p.valid_from_year and p.valid_to_year
