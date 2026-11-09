import unittest

from config import Settings
from snowflake_client import SnowflakeClient, SnowflakeClientError, parse_table_name


def make_settings(**overrides) -> Settings:
    values = {
        "groq_api_key": "test-key",
        "groq_model": "test-model",
        "snowflake_account": "account",
        "snowflake_user": "user",
        "snowflake_password": "password",
        "snowflake_warehouse": "warehouse",
        "snowflake_database": "ECON_AGENT_DB",
        "snowflake_schema": "ANALYTICS",
        "snowflake_role": "READONLY",
        "snowflake_table": "ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE",
        "snowflake_semantic_view": "ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_SEMANTIC",
    }
    values.update(overrides)
    return Settings(**values)


class FakeCursor:
    def __init__(self, columns, rows):
        self.description = tuple((column,) for column in columns) if columns else None
        self._rows = list(rows)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self._rows)

    def fetchmany(self, count):
        return list(self._rows[:count])

    def close(self):
        pass


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def is_closed(self):
        return False

    def close(self):
        pass


class SnowflakeClientTests(unittest.TestCase):
    def test_parse_valid_table_name(self):
        self.assertEqual(
            parse_table_name("ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE"),
            ("ECON_AGENT_DB", "ANALYTICS", "ECONOMIC_DASHBOARD_LIVE"),
        )

    def test_parse_invalid_table_name(self):
        with self.assertRaises(SnowflakeClientError):
            parse_table_name("ECONOMIC_DASHBOARD_LIVE")
        with self.assertRaises(SnowflakeClientError):
            parse_table_name("DB.SCHEMA.TABLE; DROP TABLE X")

    def test_schema_mapping(self):
        cursor = FakeCursor(
            ("COLUMN_NAME", "DATA_TYPE", "IS_NULLABLE", "COMMENT"),
            [
                ("DATE", "DATE", "Y", "Observation date"),
                ("CPI", "FLOAT", "Y", ""),
            ],
        )
        client = SnowflakeClient(make_settings())
        client._connection = FakeConnection(cursor)
        columns = client.get_table_schema("ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE")
        self.assertEqual([column.name for column in columns], ["DATE", "CPI"])
        self.assertEqual(columns[0].comment, "Observation date")

    def test_execute_query_truncates_to_max_rows(self):
        cursor = FakeCursor(("N",), [(index,) for index in range(6)])
        client = SnowflakeClient(make_settings())
        client._connection = FakeConnection(cursor)
        result = client.execute_query("SELECT 1", max_rows=5)
        self.assertEqual(result.row_count, 5)
        self.assertEqual(len(result.rows), 5)
        self.assertTrue(result.truncated)
        self.assertEqual(result.columns, ("N",))


if __name__ == "__main__":
    unittest.main()
