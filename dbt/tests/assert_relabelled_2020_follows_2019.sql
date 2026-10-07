-- After relabelling the Kaggle backup, each sizeable state's 2020 total should be
-- close to its own 2018-2019 level. Before the fix, Rajasthan went from about
-- 40,000 cases to 33.
with totals as (
    select analysis_unit, year, sum(cases) as total
    from {{ ref('int_state_crimes_long') }}
    where year between 2018 and 2020
    group by analysis_unit, year
),

compared as (
    select
        analysis_unit,
        max(total) filter (where year = 2020)               as total_2020,
        avg(total) filter (where year in (2018, 2019))      as baseline
    from totals
    group by analysis_unit
)

select *
from compared
where baseline >= 500
  and (total_2020 < 0.5 * baseline or total_2020 > 2 * baseline)
