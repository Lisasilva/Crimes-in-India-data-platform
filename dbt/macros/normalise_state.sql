{# One spelling per state name: upper case, trimmed, single spaces around "&",
   and without the footnote marks NCRB puts after some names ("Jammu & Kashmir*",
   "Ladakh @"). Keep in step with pipeline/quality.py (used_cells). #}
{% macro normalise_state(column) -%}
    upper(regexp_replace(regexp_replace(trim({{ column }}), '[\s*@+#‐-]+$', ''), '\s*&\s*', ' & ', 'g'))
{%- endmacro %}
