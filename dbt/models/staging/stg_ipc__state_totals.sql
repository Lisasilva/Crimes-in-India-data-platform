-- State total rows of the district-wise IPC files, one row per state, year and
-- source column. The districts add up to these rows in every state and year
-- (checked by pipeline/quality.py), so the models read the totals directly.
-- Total rows are labelled differently in each file (see pipeline/sources.py).
select
    _source_file                            as source_file,
    _line_number                            as source_line_number,
    trim(state)                             as state_label,
    {{ normalise_state('state') }}          as name_key,
    cast(year as integer)                   as year,
    column_name,
    cast(value as bigint)                   as cases
from {{ source('bronze', 'ipc_district_cells') }}
where upper(trim(district)) in ('TOTAL', 'DELHI UT TOTAL', 'ZZ TOTAL')
  and value <> ''
  -- Only the columns the crime_head_columns seed uses; other columns hold
  -- rates and sub-heads with values such as "-" or "0.4".
  and column_name in (
      select column_name from {{ ref('crime_head_columns') }} where source = 'ipc_district'
  )
