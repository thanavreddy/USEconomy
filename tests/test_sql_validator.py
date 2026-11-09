import unittest

from sql_validator import validate_sql

ALLOWED = ["ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE"]


class SQLValidatorTests(unittest.TestCase):
    def assert_allowed(self, sql: str) -> None:
        result = validate_sql(sql, ALLOWED)
        self.assertTrue(result.ok, msg=result.reason)
        self.assertTrue(result.sql)

    def assert_blocked(self, sql: str) -> None:
        result = validate_sql(sql, ALLOWED)
        self.assertFalse(result.ok, msg=f"Expected SQL to be blocked: {sql}")

    def test_allows_fully_qualified_select(self):
        self.assert_allowed(
            "SELECT DATE, CPI FROM ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE"
        )

    def test_allows_unqualified_approved_table(self):
        self.assert_allowed(
            "SELECT * FROM ECONOMIC_DASHBOARD_LIVE ORDER BY DATE DESC LIMIT 1"
        )

    def test_allows_schema_qualified_table(self):
        self.assert_allowed(
            "SELECT * FROM ANALYTICS.ECONOMIC_DASHBOARD_LIVE LIMIT 5"
        )

    def test_allows_cte(self):
        self.assert_allowed(
            "WITH recent AS ("
            "SELECT * FROM ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE"
            ") SELECT * FROM recent"
        )

    def test_allows_trailing_semicolon(self):
        self.assert_allowed(
            "SELECT * FROM ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE;"
        )

    def test_allows_string_literal_containing_keyword(self):
        self.assert_allowed(
            "SELECT REPLACE('DROP', 'D', 'X') AS S "
            "FROM ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE"
        )

    def test_allows_table_less_cannot_answer_query(self):
        self.assert_allowed(
            "SELECT 'This question cannot be answered from the available data.' AS MESSAGE"
        )

    def test_blocks_insert(self):
        self.assert_blocked(
            "INSERT INTO ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE VALUES (1, 2)"
        )

    def test_blocks_update(self):
        self.assert_blocked(
            "UPDATE ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE SET CPI = 1"
        )

    def test_blocks_delete(self):
        self.assert_blocked(
            "DELETE FROM ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE"
        )

    def test_blocks_drop(self):
        self.assert_blocked(
            "DROP TABLE ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE"
        )

    def test_blocks_create(self):
        self.assert_blocked("CREATE TABLE X (A INT)")

    def test_blocks_multiple_statements(self):
        self.assert_blocked(
            "SELECT * FROM ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE; "
            "DROP TABLE X"
        )

    def test_blocks_comment_hidden_statement(self):
        self.assert_blocked(
            "SELECT * FROM ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE "
            "-- ; DROP TABLE X"
        )

    def test_blocks_unapproved_table(self):
        self.assert_blocked("SELECT * FROM OTHER_DB.OTHER_SCHEMA.SECRET")

    def test_blocks_unapproved_table_inside_cte(self):
        self.assert_blocked(
            "WITH x AS (SELECT * FROM OTHER_DB.S.T) SELECT * FROM x"
        )

    def test_blocks_dynamic_identifier(self):
        self.assert_blocked(
            "SELECT * FROM IDENTIFIER("
            "'ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE')"
        )

    def test_blocks_table_function(self):
        self.assert_blocked("SELECT * FROM TABLE(GENERATOR(ROWCOUNT => 10))")

    def test_blocks_cortex(self):
        self.assert_blocked("SELECT SNOWFLAKE.CORTEX.COMPLETE('llama', 'hello')")

    def test_blocks_show(self):
        self.assert_blocked("SHOW TABLES")


if __name__ == "__main__":
    unittest.main()
