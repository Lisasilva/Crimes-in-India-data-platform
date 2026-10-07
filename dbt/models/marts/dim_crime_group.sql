-- One row per crime group, with whether it is a crime against women and the
-- years it is available.
select
    g.crime_group,
    g.crime_name,
    g.is_against_women,
    g.sort_order,
    g.definition_note,
    min(f.year)     as first_year,
    max(f.year)     as last_year
from {{ ref('crime_groups') }} as g
left join {{ ref('fct_crimes') }} as f using (crime_group)
group by all
