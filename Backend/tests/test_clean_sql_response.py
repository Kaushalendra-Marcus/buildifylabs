"""clean_sql_response() must reduce every output shape the model produces down
to a single bare SQL string (specs/05 §6 first checkbox).

Shapes covered: plain SQL, fenced SQL (```sql / ``` / inline), SQL with
trailing prose, prose before the SQL, multi-line SQL, and the INVALID_QUERY
sentinel. Text-cleanup only - no safety decisions live here.
"""

from app.services.llm.sql_generator import (
    INVALID_QUERY_SENTINEL,
    clean_sql_response,
    is_invalid_query,
    build_data_schema,
    build_sql_prompt,
)


class TestCleanPlainSQL:
    def test_single_line(self):
        assert clean_sql_response("SELECT id FROM customers") == "SELECT id FROM customers"

    def test_trailing_semicolon(self):
        assert clean_sql_response("SELECT id FROM customers;") == "SELECT id FROM customers;"

    def test_whitespace_stripped(self):
        assert clean_sql_response("  \n  SELECT id FROM customers  ") == "SELECT id FROM customers"

    def test_multi_line_preserved(self):
        sql = "SELECT id,\nname\nFROM customers\nWHERE region = 'east'"
        assert clean_sql_response(sql) == sql


class TestCleanFencedSQL:
    def test_sql_fence(self):
        assert clean_sql_response("```sql\nSELECT id FROM customers\n```") == "SELECT id FROM customers"

    def test_language_less_fence(self):
        assert clean_sql_response("```\nSELECT id FROM customers\n```") == "SELECT id FROM customers"

    def test_uppercase_language_tag(self):
        assert clean_sql_response("```SQL\nSELECT id FROM customers\n```") == "SELECT id FROM customers"

    def test_inline_fence(self):
        assert clean_sql_response("```sql SELECT id FROM customers ```") == "SELECT id FROM customers"

    def test_fence_with_trailing_prose(self):
        assert (
            clean_sql_response("```sql\nSELECT id FROM customers\n```\nHere is the answer.")
            == "SELECT id FROM customers"
        )


class TestCleanSQLWithProse:
    def test_trailing_prose_on_next_line(self):
        assert (
            clean_sql_response("SELECT id FROM customers\nThis query returns the total revenue.")
            == "SELECT id FROM customers"
        )

    def test_trailing_prose_after_semicolon(self):
        assert (
            clean_sql_response("SELECT id FROM customers;\nHere's the result.")
            == "SELECT id FROM customers;"
        )

    def test_prose_before_sql(self):
        assert (
            clean_sql_response("Here is the query:\nSELECT id FROM customers")
            == "SELECT id FROM customers"
        )

    def test_multiline_prose_after_sql(self):
        assert (
            clean_sql_response(
                "SELECT region, SUM(revenue) AS total FROM sales GROUP BY region LIMIT 100\n"
                "This shows revenue by region. Some more explanation."
            )
            == "SELECT region, SUM(revenue) AS total FROM sales GROUP BY region LIMIT 100"
        )


class TestCleanEdgeCases:
    def test_empty_string(self):
        assert clean_sql_response("") == ""

    def test_only_whitespace(self):
        assert clean_sql_response("   \n  ") == ""

    def test_garbage_never_parses(self):
        assert clean_sql_response("hello world, this is not SQL") == ""

    def test_sentinel_survives_cleaning(self):
        assert clean_sql_response(INVALID_QUERY_SENTINEL) == INVALID_QUERY_SENTINEL

    def test_sentinel_through_fence(self):
        assert (
            clean_sql_response(f"```sql\n{INVALID_QUERY_SENTINEL}\n```")
            == INVALID_QUERY_SENTINEL
        )


class TestIsInvalidQuery:
    def test_exact_sentinel(self):
        assert is_invalid_query(INVALID_QUERY_SENTINEL)

    def test_trailing_semicolon(self):
        assert is_invalid_query("SELECT 'INVALID_QUERY' LIMIT 1;")

    def test_extra_whitespace(self):
        assert is_invalid_query("  SELECT   'INVALID_QUERY'   LIMIT 1  ")

    def test_case_insensitive(self):
        assert is_invalid_query("select 'INVALID_QUERY' limit 1")

    def test_real_query_is_not_sentinel(self):
        assert not is_invalid_query("SELECT id FROM customers LIMIT 100")

    def test_variant_is_not_mistaken(self):
        # Best-effort exact-match detection: a reshaped sentinel is not
        # special-cased (it would just run and return a normal row).
        assert not is_invalid_query("SELECT 'INVALID_QUERY' AS result LIMIT 1")


class TestDynamicSchema:
    def test_build_data_schema_shape(self):
        schema = build_data_schema("user_abc_data", ["date", "revenue", "region"])
        assert "Tables:" in schema
        assert "user_abc_data" in schema
        assert "- revenue" in schema

    def test_build_data_schema_empty_columns(self):
        schema = build_data_schema("user_abc_data", [])
        assert "user_abc_data" in schema

    def test_build_sql_prompt_uses_custom_schema(self):
        schema = build_data_schema("user_abc_data", ["date"])
        prompt = build_sql_prompt("Total revenue?", schema=schema)
        assert "user_abc_data" in prompt
        assert "Total revenue?" in prompt

    def test_build_sql_prompt_defaults_to_placeholder(self):
        prompt = build_sql_prompt("Total revenue?")
        assert "sales" in prompt
