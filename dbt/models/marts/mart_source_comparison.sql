-- Cross-check: Kaggle against the NCRB 2001-2010 file, figure by figure, before
-- any correction. Both are copies of NCRB data, so they should agree exactly.
with kaggle as (
    unpivot {{ ref('stg_kaggle__state_crimes') }}
    on rape, kidnapping_abduction, dowry_deaths, assault_on_women,
       insult_to_modesty, cruelty_by_husband, importation_of_girls
    into name crime_code value kaggle_cases
),

kaggle_named as (
    select s.state_name, k.year, k.crime_code, k.kaggle_cases
    from kaggle as k
    join {{ ref('state_names') }} as s using (name_key)
    where k.year between 2001 and 2010
),

ncrb_named as (
    select s.state_name, n.year, c.crime_code, n.cases_reported as ncrb_cases
    from {{ ref('stg_ncrb__cases_2001_2010') }} as n
    join {{ ref('state_names') }} as s using (name_key)
    join {{ ref('crime_types') }} as c on c.ncrb_sub_group = n.sub_group_name
)

select
    coalesce(k.state_name, n.state_name)    as state_name,
    coalesce(k.year, n.year)                as year,
    coalesce(k.crime_code, n.crime_code)    as crime_code,
    k.kaggle_cases,
    n.ncrb_cases,
    case
        when k.kaggle_cases is null then 'only in NCRB file'
        when n.ncrb_cases is null then 'only in Kaggle file'
        when k.kaggle_cases = n.ncrb_cases then 'match'
        else 'different'
    end                                     as comparison
from kaggle_named as k
full join ncrb_named as n
    on n.state_name = k.state_name and n.year = k.year and n.crime_code = k.crime_code
