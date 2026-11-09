import os
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"

# Clear only the app's own variables. Clearing the entire environment would
# remove USERPROFILE on Windows and break Streamlit's own config loading.
BLANK_ENV = {
    "GROQ_API_KEY": "",
    "SNOWFLAKE_ACCOUNT": "",
    "SNOWFLAKE_USER": "",
    "SNOWFLAKE_PASSWORD": "",
    "SNOWFLAKE_WAREHOUSE": "",
    "SNOWFLAKE_DATABASE": "",
    "SNOWFLAKE_SCHEMA": "",
    "SNOWFLAKE_ROLE": "",
}


class AppSmokeTests(unittest.TestCase):
    def test_missing_configuration_shows_setup_message(self):
        with patch.dict(os.environ, BLANK_ENV):
            app = AppTest.from_file(str(APP_PATH), default_timeout=30)
            app.run()

        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertIn("Configuration required", app.error[0].value)


if __name__ == "__main__":
    unittest.main()
