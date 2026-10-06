-- Average yearly crime rate per 100,000 women for each state over the profile
-- window, one column per crime type. This is the input to clustering.
{% set crimes = ['rape', 'kidnapping_abduction', 'dowry_deaths', 'assault_on_women',
                 'insult_to_modesty', 'cruelty_by_husband', 'importation_of_girls'] %}
with window_rates as (
    select *
    from {{ ref('fct_crimes_against_women') }}
    where year between {{ var('profile_start_year') }} and {{ var('profile_end_year') }}
)

select
    analysis_unit,
    {% for crime in crimes %}
    round(avg(rate_per_100k_women) filter (where crime_code = '{{ crime }}'), 2) as {{ crime }},
    {% endfor %}
    count(distinct year) filter (where cases is not null)   as years_with_data,
    sum(cases)                                              as total_cases
from window_rates
group by analysis_unit
