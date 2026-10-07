-- Grain: one row per analysis unit and year. The population NCRB itself used for
-- its crime rates that year, so rates here can be checked against NCRB's.
--   population:        NCRB's mid-year projected population (tables 1.6, 1.4, 1A.1)
--   female_population: NCRB's female population (tables 5.1 and 3A.1) from 2012.
--                      Before 2012 NCRB published women's rates against the total
--                      population, so the female population is estimated as the
--                      total times the unit's female share in Census 2011.
-- Merged units (Jammu & Kashmir with Ladakh, Dadra & Nagar Haveli with Daman &
-- Diu) add up their parts.
with parts as (
    select
        p.source,
        s.analysis_unit,
        p.year,
        sum(p.value * 100000)   as persons
    from {{ ref('stg_ncrb__population') }} as p
    join {{ ref('state_names') }} as s on s.name_key = p.name_key
    where p.measure = 'population_lakhs'
    group by all
),

census_share as (
    select
        f.analysis_unit,
        f.valid_from_year,
        f.valid_to_year,
        f.female_population * 1.0 / t.population   as female_share
    from {{ ref('female_population_2011') }} as f
    join {{ ref('population_2011') }} as t
        on t.analysis_unit = f.analysis_unit
       and t.valid_from_year = f.valid_from_year
)

select
    t.analysis_unit,
    t.year,
    round(t.persons)::bigint                                                as population,
    round(coalesce(w.persons, t.persons * c.female_share))::bigint          as female_population,
    case when w.persons is not null then 'NCRB female population'
         else 'Estimated: NCRB population x Census 2011 female share' end   as female_population_basis
from parts as t
left join parts as w
    on w.source = 'ncrb_women_totals'
   and w.analysis_unit = t.analysis_unit
   and w.year = t.year
left join census_share as c
    on c.analysis_unit = t.analysis_unit
   and t.year between c.valid_from_year and c.valid_to_year
where t.source = 'ncrb_ipc_totals'
