-- In the 2020 and 2021 rows, the figures follow NCRB's 2020 state order but the
-- state labels were copied from the older list, so from Jammu & Kashmir onward
-- each row holds another state's figures. The reviewed mapping in the
-- kaggle_relabel_2020_2021 seed puts each row back under the right state.
select
    k.*,
    coalesce(r.actual_state, k.name_key)    as corrected_name_key,
    r.actual_state is not null              as was_relabelled
from {{ ref('stg_kaggle__state_crimes') }} as k
left join {{ ref('kaggle_relabel_2020_2021') }} as r
    on r.year = k.year
   and r.labelled_as = k.name_key
