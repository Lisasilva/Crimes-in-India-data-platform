-- Every analysis unit should have every crime type in every year it existed,
-- except importation of girls in 2016, which NCRB did not publish separately.
with expected as (
    select s.analysis_unit, y.year, c.crime_code
    from {{ ref('dim_state') }} as s
    cross join (select distinct year from {{ ref('fct_crimes_against_women') }}) as y
    cross join {{ ref('dim_crime_type') }} as c
    where y.year between s.first_year and s.last_year
      and not (c.crime_code = 'importation_of_girls' and y.year = 2016)
)

select e.*
from expected as e
left join {{ ref('fct_crimes_against_women') }} as f using (analysis_unit, year, crime_code)
where f.fact_id is null
