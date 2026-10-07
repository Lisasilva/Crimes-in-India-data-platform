select crime_code, crime_name, legal_section, definition_note, kaggle_column, ncrb_sub_group
from {{ ref('crime_types') }}
