{# Use the schema names from dbt_project.yml (bronze, silver, gold) as they are,
   instead of dbt's default of prefixing them with the target schema. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name else target.schema }}
{%- endmacro %}
