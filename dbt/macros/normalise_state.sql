{# One spelling per state name: upper case, trimmed, single spaces around "&". #}
{% macro normalise_state(column) -%}
    upper(regexp_replace(trim({{ column }}), '\s*&\s*', ' & ', 'g'))
{%- endmacro %}
