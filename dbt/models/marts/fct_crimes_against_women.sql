-- Grain: one row per analysis unit, year and crime type, from NCRB's own
-- crimes-against-women tables (2001-2024). Rates use the female population NCRB
-- used that year (dim_population); female_population_basis says where it is an
-- estimate. The Kaggle copy is kept only as a cross-check
-- (mart_crime_source_overlap).
with by_unit as (
    select
        analysis_unit,
        year,
        crime_code,
        -- A merged unit is only complete if every part reported a figure.
        case when count(cases) = count(*) then sum(cases) end      as cases
    from {{ ref('int_women_crimes_by_type') }}
    group by analysis_unit, year, crime_code
)

select
    b.analysis_unit || '|' || b.year || '|' || b.crime_code        as fact_id,
    b.analysis_unit,
    b.year,
    b.crime_code,
    b.cases,
    p.female_population,
    p.female_population_basis,
    round(b.cases * 100000.0 / p.female_population, 2)              as rate_per_100k_women,
    'ncrb_women_heads'                                              as source
from by_unit as b
left join {{ ref('dim_population') }} as p
    on p.analysis_unit = b.analysis_unit
   and p.year = b.year
