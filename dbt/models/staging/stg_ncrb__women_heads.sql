-- State and union territory rows of NCRB's crimes-against-women tables, one row
-- per state, year and source column: the consolidated 2001-2015 table and
-- tables 3A.2(i) and 3A.2(ii) for 2016-2024. As in stg_ncrb__state_heads, only
-- known states and the columns the crime_head_columns seed uses are kept.
select
    _source_file                            as source_file,
    _line_number                            as source_line_number,
    trim(state)                             as state_label,
    {{ normalise_state('state') }}          as name_key,
    cast(year as integer)                   as year,
    column_name,
    cast(value as bigint)                   as cases
from {{ source('bronze', 'ncrb_women_cells') }}
where {{ normalise_state('state') }} in (select name_key from {{ ref('state_names') }})
  and value <> ''
  and column_name in (
      select column_name from {{ ref('crime_head_columns') }} where source = 'ncrb_women_heads'
  )
