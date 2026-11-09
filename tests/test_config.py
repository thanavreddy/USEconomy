import unittest
from unittest.mock import patch

from config import ConfigError, Settings


REQUIRED_ENV = {
    "GROQ_API_KEY": "test-key",
    "SNOWFLAKE_ACCOUNT": "org-account",
    "SNOWFLAKE_USER": "user",
    "SNOWFLAKE_PASSWORD": "password",
    "SNOWFLAKE_WAREHOUSE": "COMPUTE_WH",
    "SNOWFLAKE_DATABASE": "ECON_AGENT_DB",
    "SNOWFLAKE_SCHEMA": "ANALYTICS",
}


class SettingsTests(unittest.TestCase):
    def test_missing_required_variable_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ConfigError):
                Settings.from_env()

    def test_defaults_and_values(self):
        env = dict(REQUIRED_ENV)
        env["SNOWFLAKE_ROLE"] = "ECON_AGENT_READONLY"
        env["GROQ_MODEL"] = "test-model"
        with patch.dict("os.environ", env, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.groq_model, "test-model")
        self.assertEqual(settings.snowflake_role, "ECON_AGENT_READONLY")
        self.assertEqual(
            settings.snowflake_table,
            "ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE",
        )
        self.assertEqual(
            settings.snowflake_semantic_view,
            "ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_SEMANTIC",
        )
        self.assertEqual(settings.max_rows, 200)

    def test_row_limit_is_capped_at_200(self):
        env = dict(REQUIRED_ENV)
        env["MAX_RESULT_ROWS"] = "9999"
        with patch.dict("os.environ", env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.max_rows, 200)

    def test_invalid_row_limit_falls_back(self):
        env = dict(REQUIRED_ENV)
        env["MAX_RESULT_ROWS"] = "not-a-number"
        with patch.dict("os.environ", env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.max_rows, 200)

    def test_table_override(self):
        env = dict(REQUIRED_ENV)
        env["SNOWFLAKE_TABLE"] = "DB.SCH.MY_TABLE"
        env["SNOWFLAKE_SEMANTIC_VIEW"] = "DB.SCH.MY_VIEW"
        with patch.dict("os.environ", env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.snowflake_table, "DB.SCH.MY_TABLE")
        self.assertEqual(settings.snowflake_semantic_view, "DB.SCH.MY_VIEW")


if __name__ == "__main__":
    unittest.main()
