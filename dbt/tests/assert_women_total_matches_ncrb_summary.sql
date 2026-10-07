-- The total crimes against women in NCRB's crime-head tables should equal the
-- total in its summary table of the same year (5.1 for 2012-2015, 3A.1 from
-- 2016), state by state.
with summary as (
    select s.state_name, p.year, p.value as summary_cases
    from {{ ref('stg_ncrb__population') }} as p
    join {{ ref('state_names') }} as s on s.name_key = p.name_key
    where p.source = 'ncrb_women_totals' and p.measure = 'cases'
)

select w.state_name, w.year, w.cases, s.summary_cases
from {{ ref('int_women_crimes_by_type') }} as w
join summary as s using (state_name, year)
where w.crime_code = 'total_against_women'
  and w.cases <> s.summary_cases
