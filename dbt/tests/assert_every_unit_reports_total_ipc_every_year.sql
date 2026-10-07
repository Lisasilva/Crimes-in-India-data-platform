-- Every analysis unit has a total IPC figure in every year from its first
-- year to the last year loaded. Telangana starts in 2014.
with spans as (
    select analysis_unit, min(year) as first_year
    from {{ ref('fct_crimes') }}
    group by analysis_unit
),

expected as (
    select s.analysis_unit, y.year
    from spans as s
    cross join (select distinct year from {{ ref('fct_crimes') }}) as y
    where y.year >= s.first_year
)

select e.*
from expected as e
left join {{ ref('fct_crimes') }} as f
    on f.analysis_unit = e.analysis_unit and f.year = e.year and f.crime_group = 'total_ipc'
where f.cases is null
