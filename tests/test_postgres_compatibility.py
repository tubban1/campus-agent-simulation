import unittest
from unittest.mock import Mock

from app.db import PostgresConnection


class PostgresCompatibilityTest(unittest.TestCase):
    def test_parameterless_query_does_not_pass_empty_parameter_tuple(self):
        raw_connection = Mock()
        cursor = Mock()
        cursor.rowcount = 0
        raw_connection.execute.return_value = cursor
        connection = PostgresConnection(raw_connection)
        statement = "SELECT id FROM residents WHERE role LIKE '%学生%'"

        connection.execute(statement)

        # Literal percent signs in LIKE patterns are escaped to `%%` so that
        # psycopg never mistakes them for placeholders.
        raw_connection.execute.assert_called_once_with(
            "SELECT id FROM residents WHERE role LIKE '%%学生%%'"
        )

    def test_parameterized_query_still_passes_translated_parameters(self):
        raw_connection = Mock()
        cursor = Mock()
        cursor.rowcount = 0
        raw_connection.execute.return_value = cursor
        connection = PostgresConnection(raw_connection)

        connection.execute("SELECT id FROM residents WHERE name = ?", ("林小夏",))

        raw_connection.execute.assert_called_once_with(
            "SELECT id FROM residents WHERE name = %s",
            ("林小夏",),
        )

    def test_primary_key_operation_table_does_not_request_missing_id(self):
        raw_connection = Mock()
        cursor = Mock()
        cursor.rowcount = 0
        raw_connection.execute.return_value = cursor
        connection = PostgresConnection(raw_connection)

        connection.execute(
            """
            INSERT OR IGNORE INTO ledger_authorized_operations
            (transaction_id, authorization_rule_id, authority_actor_key,
             operation_type)
            VALUES (?, ?, ?, ?)
            """,
            (1, 2, "system:credit-union", "issue"),
        )

        statement, params = raw_connection.execute.call_args.args
        self.assertNotIn("RETURNING id", statement)
        self.assertIn("ON CONFLICT DO NOTHING", statement)
        self.assertEqual(params, (1, 2, "system:credit-union", "issue"))

    def test_parameterized_query_with_literal_percent_escapes_like_pattern(self):
        raw_connection = Mock()
        cursor = Mock()
        cursor.rowcount = 0
        raw_connection.execute.return_value = cursor
        connection = PostgresConnection(raw_connection)

        connection.execute(
            "SELECT COUNT(*) AS count FROM spatial_edges WHERE name LIKE '%图书馆%' AND weather_factor > ?",
            (2.0,),
        )

        statement, params = raw_connection.execute.call_args.args
        self.assertIn("LIKE '%%图书馆%%'", statement)
        self.assertIn("weather_factor > %s", statement)
        self.assertEqual(params, (2.0,))


if __name__ == "__main__":
    unittest.main()
