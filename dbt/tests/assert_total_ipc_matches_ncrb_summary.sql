-- Total IPC crimes per state in fct_crimes (district files to 2013, NCRB's
-- crime-head tables from 2014) against NCRB's summary tables 1.6, 1.4 and 1A.1.
-- A few states differ slightly in the district files (for example Meghalaya
-- 2004: 1,752 against 1,757), so this test warns rather than fails.
{{ config(severity = 'warn') }}

with summary as (
    select s.analysis_unit, p.year, sum(p.value) as summary_cases
    from {{ ref('stg_ncrb__population') }} as p
    join {{ ref('state_names') }} as s on s.name_key = p.name_key
    where p.source = 'ncrb_ipc_totals' and p.measure = 'cases'
    group by all
)

select f.analysis_unit, f.year, f.cases, s.summary_cases, f.source
from {{ ref('fct_crimes') }} as f
join summary as s using (analysis_unit, year)
where f.crime_group = 'total_ipc'
  and f.cases <> s.summary_cases
