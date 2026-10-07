-- All-India cases and rate per crime type and year. A year is left empty for a
-- crime type when any state's figure is missing, so totals are never undercounted.
select
    year,
    crime_code,
    case when count(cases) = count(*) then sum(cases) end                      as cases,
    round(
        case when count(cases) = count(*) then sum(cases) end * 100000.0
        / sum(female_population), 2
    )                                                                           as rate_per_100k_women,
    count(*)                                                                    as units_expected,
    count(cases)                                                                as units_reporting
from {{ ref('fct_crimes_against_women') }}
group by year, crime_code
