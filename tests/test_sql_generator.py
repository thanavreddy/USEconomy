import unittest

from snowflake_client import ColumnInfo, DataContext, QueryResult
from sql_generator import (
    SQLGenerationError,
    extract_sql,
    format_results,
    generate_answer,
    generate_sql,
)


class FakeGroq:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat(self, messages, temperature=0.0, max_tokens=900):
        self.calls.append(messages)
        return self.response


def make_context() -> DataContext:
    return DataContext(
        table="ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE",
        columns=(
            ColumnInfo("DATE", "DATE", "Y", "Observation date"),
            ColumnInfo("CPI", "FLOAT", "Y", "Consumer Price Index"),
            ColumnInfo("UNEMPLOYMENT_RATE", "FLOAT", "Y", ""),
            ColumnInfo("MORTGAGE_RATE_30Y", "FLOAT", "Y", ""),
        ),
        row_count=500,
        min_date="2024-01-01",
        max_date="2026-01-01",
        semantic_view="ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_SEMANTIC",
        semantic_view_ddl="",
    )


class SQLGeneratorTests(unittest.TestCase):
    def test_extract_sql_from_fenced_response(self):
        raw = "Here is the query:\n```sql\nSELECT DATE FROM T;\n```"
        self.assertEqual(extract_sql(raw), "SELECT DATE FROM T")

    def test_extract_sql_with_label(self):
        self.assertEqual(extract_sql("SQL: SELECT 1"), "SELECT 1")

    def test_extract_sql_without_sql(self):
        self.assertEqual(extract_sql("I cannot answer that."), "")

    def test_generate_sql_builds_schema_prompt(self):
        fake = FakeGroq(
            "```sql\nSELECT DATE, CPI "
            "FROM ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE "
            "ORDER BY DATE DESC LIMIT 12\n```"
        )
        sql = generate_sql(fake, "How has CPI changed?", make_context())
        self.assertTrue(sql.startswith("SELECT DATE, CPI"))
        system_prompt = fake.calls[0][0]["content"]
        self.assertIn("ECONOMIC_DASHBOARD_LIVE", system_prompt)
        self.assertIn("UNEMPLOYMENT_RATE", system_prompt)
        self.assertIn("1982-84", system_prompt)

    def test_generate_sql_raises_when_no_sql(self):
        fake = FakeGroq("I need more information.")
        with self.assertRaises(SQLGenerationError):
            generate_sql(fake, "Question?", make_context())

    def test_format_results(self):
        result = QueryResult(
            columns=("DATE", "CPI"),
            rows=(("2026-01-01", 324.5), ("2025-12-01", None)),
            row_count=2,
            truncated=False,
            elapsed_ms=12.0,
            sql="SELECT DATE, CPI FROM T",
        )
        text = format_results(result)
        self.assertIn("2026-01-01", text)
        self.assertIn("324.5", text)
        self.assertIn("NULL", text)
        self.assertIn("Returned 2 row(s)", text)

    def test_generate_answer_with_no_rows_is_grounded(self):
        fake = FakeGroq("should not be called")
        result = QueryResult(
            columns=("DATE", "CPI"),
            rows=(),
            row_count=0,
            truncated=False,
            elapsed_ms=1.0,
            sql="SELECT DATE, CPI FROM T",
        )
        answer = generate_answer(fake, "Question?", result.sql, result)
        self.assertIn("No data was returned", answer)
        self.assertEqual(fake.calls, [])

    def test_generate_answer_calls_groq(self):
        fake = FakeGroq("CPI ended at 324.5 on 2026-01-01.")
        result = QueryResult(
            columns=("DATE", "CPI"),
            rows=(("2026-01-01", 324.5),),
            row_count=1,
            truncated=False,
            elapsed_ms=1.0,
            sql="SELECT DATE, CPI FROM T",
        )
        answer = generate_answer(fake, "What is CPI?", result.sql, result)
        self.assertEqual(answer, "CPI ended at 324.5 on 2026-01-01.")
        self.assertIn("324.5", fake.calls[0][1]["content"])


if __name__ == "__main__":
    unittest.main()
