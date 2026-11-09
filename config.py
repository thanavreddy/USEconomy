"""Application configuration loaded from environment variables / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load a local .env file if one exists. Never commit that file.
load_dotenv()

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_TABLE = "ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE"
DEFAULT_SEMANTIC_VIEW = "ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_SEMANTIC"

REQUIRED_VARS = (
    "GROQ_API_KEY",
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PASSWORD",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_DATABASE",
    "SNOWFLAKE_SCHEMA",
)


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """All runtime settings for the application."""

    groq_api_key: str
    groq_model: str
    snowflake_account: str
    snowflake_user: str
    snowflake_password: str
    snowflake_warehouse: str
    snowflake_database: str
    snowflake_schema: str
    snowflake_role: str
    snowflake_table: str
    snowflake_semantic_view: str
    snowflake_authenticator: str = ""
    max_rows: int = 200
    query_timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        missing = [name for name in REQUIRED_VARS if not os.getenv(name, "").strip()]
        if missing:
            raise ConfigError(
                "Missing required environment variable(s): "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill in the values."
            )

        try:
            max_rows = int(os.getenv("MAX_RESULT_ROWS", "200"))
        except ValueError:
            max_rows = 200
        # The application must never display more than 200 rows.
        max_rows = max(1, min(max_rows, 200))

        try:
            query_timeout = int(os.getenv("QUERY_TIMEOUT_SECONDS", "60"))
        except ValueError:
            query_timeout = 60
        query_timeout = max(10, min(query_timeout, 600))

        return cls(
            groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
            groq_model=os.getenv("GROQ_MODEL", "").strip() or DEFAULT_GROQ_MODEL,
            snowflake_account=os.getenv("SNOWFLAKE_ACCOUNT", "").strip(),
            snowflake_user=os.getenv("SNOWFLAKE_USER", "").strip(),
            snowflake_password=os.getenv("SNOWFLAKE_PASSWORD", "").strip(),
            snowflake_warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "").strip(),
            snowflake_database=os.getenv("SNOWFLAKE_DATABASE", "").strip(),
            snowflake_schema=os.getenv("SNOWFLAKE_SCHEMA", "").strip(),
            snowflake_role=os.getenv("SNOWFLAKE_ROLE", "").strip(),
            snowflake_table=os.getenv("SNOWFLAKE_TABLE", "").strip() or DEFAULT_TABLE,
            snowflake_semantic_view=(
                os.getenv("SNOWFLAKE_SEMANTIC_VIEW", "").strip() or DEFAULT_SEMANTIC_VIEW
            ),
            snowflake_authenticator=os.getenv("SNOWFLAKE_AUTHENTICATOR", "").strip(),
            max_rows=max_rows,
            query_timeout_seconds=query_timeout,
        )
