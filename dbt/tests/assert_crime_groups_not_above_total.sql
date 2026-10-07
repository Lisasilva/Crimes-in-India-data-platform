-- The named crime groups never add up to more than total IPC crimes, which
-- would mean a column is mapped twice or to the wrong group.
select analysis_unit, year, cases as other_ipc
from {{ ref('fct_crimes') }}
where crime_group = 'other_ipc' and cases < 0
