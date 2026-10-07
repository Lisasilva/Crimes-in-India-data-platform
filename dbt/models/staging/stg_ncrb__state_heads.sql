-- State and union territory rows of the NCRB crime-head tables, one row per
-- state, year and source column. Total rows and footnote text extracted from
-- the PDFs are left out: a row is kept only when its name is a known state.
select
    _source_file                            as source_file,
    _line_number                            as source_line_number,
    trim(state)                             as state_label,
    {{ normalise_state('state') }}          as name_key,
    cast(year as integer)                   as year,
    column_name,
    cast(value as bigint)                   as cases
from {{ source('bronze', 'ncrb_state_cells') }}
where {{ normalise_state('state') }} in (select name_key from {{ ref('state_names') }})
  and value <> ''
  -- Only the columns the crime_head_columns seed uses; other columns hold
  -- rates and sub-heads with values such as "-" or "0.4".
  and column_name in (
      select column_name from {{ ref('crime_head_columns') }} where source = 'ncrb_state_heads'
  )
