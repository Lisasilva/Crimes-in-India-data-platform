-- Average yearly crime rate per 100,000 people for each state over the profile
-- window, one column per crime group. This is the input to the all-crimes
-- clustering. The groups are the larger IPC/BNS heads that every state reports
-- in every year of the window.
{% set groups = ['murder', 'attempt_to_murder', 'rape', 'kidnapping_abduction', 'robbery',
                 'burglary', 'theft', 'riots', 'cheating', 'hurt', 'causing_death_by_negligence'] %}
with window_rates as (
    select *
    from {{ ref('fct_crimes') }}
    where year between {{ var('profile_start_year') }} and {{ var('profile_end_year') }}
)

select
    analysis_unit,
    {% for group in groups %}
    round(avg(rate_per_100k) filter (where crime_group = '{{ group }}'), 2) as {{ group }},
    {% endfor %}
    count(distinct year) filter (where cases is not null)   as years_with_data,
    sum(cases) filter (where crime_group = 'total_ipc')     as total_cases
from window_rates
group by analysis_unit
