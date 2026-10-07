-- NCRB's state totals with the population and rate NCRB used, one row per
-- source, state, year and measure (cases, population_lakhs, published_rate).
--   ncrb_ipc_totals:   total IPC/BNS crimes and the mid-year population
--   ncrb_women_totals: total crimes against women and the female population
-- The population_columns seed says which column holds each measure. Footnote
-- marks such as "509.2*" are removed; the city section of the older tables is
-- left out.
with cells as (
    select 'ncrb_ipc_totals' as source, * from {{ source('bronze', 'ncrb_ipc_total_cells') }}
    union all by name
    select 'ncrb_women_totals' as source, * from {{ source('bronze', 'ncrb_women_total_cells') }}
)

select
    c.source,
    c._source_file                                                  as source_file,
    c._line_number                                                  as source_line_number,
    trim(c.state)                                                   as state_label,
    {{ normalise_state('c.state') }}                                as name_key,
    cast(c.year as integer)                                         as year,
    m.measure,
    -- try_cast: the city rows filtered out below hold "-" and "NR". Values on
    -- state rows are checked by pipeline/quality.py, and not_null here.
    try_cast(regexp_replace(c.value, '[^0-9.]', '', 'g') as decimal(14, 2)) as value
from cells as c
join {{ ref('population_columns') }} as m
    on m.source = c.source
   and m.column_name = c.column_name
   and cast(c.year as integer) between m.year_from and m.year_to
where c.section not ilike 'CIT%'
  and {{ normalise_state('c.state') }} in (select name_key from {{ ref('state_names') }})
  and c.value <> ''
