-- The 2014 district file and NCRB table 1.6 should report the same figures.
-- They differ for West Bengal (NCRB revised its figures after the district
-- file was published), so this test warns rather than fails.
{{ config(severity = 'warn') }}

select *
from {{ ref('mart_crime_source_overlap') }}
where comparison_type = '2014 district file vs NCRB table'
  and comparison <> 'match'
