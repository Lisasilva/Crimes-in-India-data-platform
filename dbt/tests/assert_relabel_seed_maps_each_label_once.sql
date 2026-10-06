-- Every label and every target state appears at most once per year in the mapping.
select year, labelled_as as name_key, 'labelled_as' as side, count(*) as n
from {{ ref('kaggle_relabel_2020_2021') }}
group by 1, 2
having count(*) > 1
union all
select year, actual_state, 'actual_state', count(*)
from {{ ref('kaggle_relabel_2020_2021') }}
group by 1, 2
having count(*) > 1
