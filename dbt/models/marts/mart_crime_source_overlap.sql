-- Cross-checks between sources that cover the same figures:
--   1. 2014: the data.gov.in district file against NCRB table 1.6
--   2. NCRB's crimes-against-women tables (fct_crimes_against_women) against
--      NCRB's all-crimes tables (fct_crimes), for the crime types both report.
--      Kidnapping is left out: the women tables count only women and girls.
--   3. The Kaggle copy of the women data (int_state_crimes_long, after its
--      corrections) against NCRB's crimes-against-women tables
with district_2014 as (
    select analysis_unit, year, crime_group, sum(cases) as cases
    from {{ ref('int_crimes_by_group') }}
    where source = 'ipc_district' and year = {{ var('ncrb_tables_from_year') }}
    group by all
),

ncrb_2014 as (
    select analysis_unit, year, crime_group, sum(cases) as cases
    from {{ ref('int_crimes_by_group') }}
    where source = 'ncrb_state_heads' and year = {{ var('ncrb_tables_from_year') }}
    group by all
),

kaggle_by_unit as (
    select
        analysis_unit,
        year,
        crime_code,
        case when count(cases) = count(*) then sum(cases) end      as cases
    from {{ ref('int_state_crimes_long') }}
    where source = 'kaggle'
    group by all
),

pairs as (
    select
        '2014 district file vs NCRB table'      as comparison_type,
        coalesce(n.analysis_unit, d.analysis_unit) as analysis_unit,
        coalesce(n.year, d.year)                as year,
        coalesce(n.crime_group, d.crime_group)  as crime_group,
        n.cases                                 as official_cases,
        d.cases                                 as other_cases
    from ncrb_2014 as n
    full join district_2014 as d using (analysis_unit, year, crime_group)

    union all

    select
        'NCRB women tables vs NCRB crime-head tables',
        w.analysis_unit,
        w.year,
        w.crime_code,
        w.cases,
        f.cases
    from {{ ref('fct_crimes_against_women') }} as w
    join {{ ref('fct_crimes') }} as f
        on f.analysis_unit = w.analysis_unit
       and f.year = w.year
       and f.crime_group = w.crime_code
    where w.crime_code <> 'kidnapping_abduction'

    union all

    select
        'Kaggle women file vs NCRB women tables',
        w.analysis_unit,
        w.year,
        w.crime_code,
        w.cases,
        k.cases
    from {{ ref('fct_crimes_against_women') }} as w
    join kaggle_by_unit as k using (analysis_unit, year, crime_code)
)

select
    *,
    other_cases - official_cases                as difference,
    case
        when official_cases is null or other_cases is null then 'missing in one source'
        when official_cases = other_cases then 'match'
        else 'different'
    end                                         as comparison
from pairs
