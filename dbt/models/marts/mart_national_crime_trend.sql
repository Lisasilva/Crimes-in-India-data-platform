-- All-India cases and rate per 100,000 people for each crime group and year.
-- The states and union territories add up to NCRB's all-India row (checked by
-- pipeline/quality.py), so the national figure is their sum.
select
    f.year,
    f.crime_group,
    g.is_against_women,
    sum(f.cases)                                                    as cases,
    count(f.cases)                                                  as units_reporting,
    sum(f.population_2011)                                          as population_2011,
    round(sum(f.cases) * 100000.0 / sum(f.population_2011), 2)      as rate_per_100k
from {{ ref('fct_crimes') }} as f
join {{ ref('crime_groups') }} as g using (crime_group)
group by all
