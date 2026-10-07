-- The populations in dim_population should reproduce the rates NCRB printed:
-- NCRB's own case count divided by our population, against NCRB's rate.
-- NCRB rounds the population to 0.01 lakh (to 2013) or 0.1 lakh (from 2014),
-- which moves a small territory's rate a little, so the allowed gap is that
-- rounding plus 0.06 for the printed rate's own rounding. Units made of
-- several parts that year (Jammu & Kashmir with Ladakh from 2020) are left
-- out, as NCRB prints a rate for each part.
with published as (
    select
        p.source,
        s.analysis_unit,
        p.year,
        count(distinct p.name_key)                                          as parts,
        any_value(p.value) filter (where p.measure = 'cases')              as cases,
        any_value(p.value) filter (where p.measure = 'population_lakhs')   as population_lakhs,
        any_value(p.value) filter (where p.measure = 'published_rate')     as published_rate
    from {{ ref('stg_ncrb__population') }} as p
    join {{ ref('state_names') }} as s on s.name_key = p.name_key
    group by all
),

compared as (
    select
        p.*,
        round(p.cases * 100000.0 / case when p.source = 'ncrb_ipc_totals'
                                        then d.population else d.female_population end, 2) as our_rate
    from published as p
    join {{ ref('dim_population') }} as d using (analysis_unit, year)
    where p.parts = 1
)

select *
from compared
where abs(our_rate - published_rate)
      > published_rate * (case when year >= 2014 then 0.05 else 0.005 end) / population_lakhs + 0.06
