"""User-scoping (specs/05 §5.5 blocking gap) is closed in two layers:

1. Structural - each user's data lives in a dedicated per-user table
   (`user_data_table_name`), which B3 creates and which only ever contains that
   user's rows.
2. Post-generation validation - `assert_user_scoped()` walks the AST and rejects
   any query that references a table outside the caller's namespace.

These tests cover layer 2 and the table-name scheme (layer 1's contract with
B3). The end-to-end "never another user's rows" guarantee is asserted in
test_executor.py.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.services.data.executor import assert_user_scoped, user_data_table_name

USER_TABLE = "user_abc123_data"


class TestUserDataTableName:
    def test_hex_uuid(self):
        uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        assert user_data_table_name(uid) == "user_12345678123456781234567812345678_data"

    def test_str_uuid(self):
        assert user_data_table_name("12345678-1234-5678-1234-567812345678").startswith(
            "user_1234567812345678"
        )

    def test_is_deterministic(self):
        assert user_data_table_name("abc") == user_data_table_name("abc")


class TestAssertUserScoped:
    def test_own_table_only(self):
        assert_user_scoped("SELECT id, name FROM user_abc123_data LIMIT 100", USER_TABLE)

    def test_own_table_with_semicolon(self):
        assert_user_scoped("SELECT id FROM user_abc123_data LIMIT 100;", USER_TABLE)

    def test_public_qualified_own_table(self):
        assert_user_scoped("SELECT id FROM public.user_abc123_data LIMIT 100", USER_TABLE)

    def test_cte_reference_allowed(self):
        assert_user_scoped(
            "WITH cte AS (SELECT id FROM user_abc123_data) "
            "SELECT id FROM cte WHERE id > 5 LIMIT 1",
            USER_TABLE,
        )

    def test_sentinel_has_no_tables(self):
        assert_user_scoped("SELECT 'INVALID_QUERY' LIMIT 1", USER_TABLE)

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT email FROM users LIMIT 1",
            "SELECT id FROM user_abc123_data JOIN users ON true LIMIT 1",
            "SELECT id FROM payments LIMIT 1",
            "SELECT id FROM query_logs LIMIT 1",
            "SELECT id FROM user_other_data LIMIT 1",
            "SELECT id FROM another_schema.user_abc123_data LIMIT 1",
            "SELECT id FROM \"USERS\" LIMIT 1",
        ],
    )
    def test_foreign_table_rejected(self, query):
        with pytest.raises(HTTPException) as exc:
            assert_user_scoped(query, USER_TABLE)
        assert exc.value.status_code == 403

    def test_case_insensitive_own_table(self):
        assert_user_scoped("SELECT id FROM USER_ABC123_DATA LIMIT 1", USER_TABLE)
