-- From 2014 (when Telangana was formed) every analysis unit should have all
-- seven crime types in every year.
with expected as (
    select s.analysis_unit, y.year, c.crime_code
    from {{ ref('dim_state') }} as s
    cross join (select distinct year from {{ ref('fct_crimes_against_women') }} where year >= 2014) as y
    cross join {{ ref('dim_crime_type') }} as c
)

select e.*
from expected as e
left join {{ ref('fct_crimes_against_women') }} as f using (analysis_unit, year, crime_code)
where f.fact_id is null
