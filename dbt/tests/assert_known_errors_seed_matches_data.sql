-- Each correction in known_source_errors must refer to a state, year and crime
-- type that exist, so a typo cannot silently do nothing.
with errors as (
    select * from {{ ref('known_source_errors') }}
),

loaded as (
    select distinct s.analysis_unit, k.year
    from {{ ref('int_kaggle_relabelled') }} as k
    join {{ ref('state_names') }} as s on s.name_key = k.corrected_name_key
)

select e.*
from errors as e
where (e.analysis_unit <> '*' and not exists (
          select 1 from loaded as l where l.analysis_unit = e.analysis_unit and l.year = e.year))
   or (e.crime_code <> '*' and e.crime_code not in (select crime_code from {{ ref('crime_types') }}))
