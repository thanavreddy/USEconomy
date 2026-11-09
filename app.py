"""Streamlit chat interface for the AI-powered Snowflake economic analyst."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config import ConfigError, Settings
from groq_client import GroqClient, GroqClientError
from snowflake_client import SnowflakeClient, SnowflakeClientError
from sql_generator import (
    AnswerGenerationError,
    SQLGenerationError,
    generate_answer,
    generate_sql,
)
from sql_validator import validate_sql


st.set_page_config(
    page_title="Ask the US Economy",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; padding-bottom: 3rem; max-width: 1050px; }
    [data-testid="stMetricValue"] { font-size: 1.8rem; }
    [data-testid="stChatMessage"] { border-radius: 0.8rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def get_snowflake_client(settings: Settings) -> SnowflakeClient:
    client = SnowflakeClient(settings)
    client.connect()
    return client


@st.cache_resource(show_spinner=False)
def get_groq_client(settings: Settings) -> GroqClient:
    return GroqClient(settings.groq_api_key, settings.groq_model)


@st.cache_data(ttl=300, show_spinner=False)
def load_data_context(_client: SnowflakeClient, settings: Settings):
    return _client.get_data_context()


@st.cache_data(ttl=300, show_spinner=False)
def load_snapshot(_client: SnowflakeClient, settings: Settings):
    return _client.get_latest_snapshot()


def _format_index(value: float) -> str:
    return f"{value:,.1f}"


def _format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def main() -> None:
    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        st.title("📊 Ask the US Economy")
        st.error("Configuration required")
        st.markdown(str(exc))
        st.markdown(
            "1. Copy `.env.example` to `.env`.\n"
            "2. Fill in your Snowflake credentials and Groq API key.\n"
            "3. Restart the app."
        )
        st.code(
            "Copy-Item .env.example .env\npython -m streamlit run app.py",
            language="powershell",
        )
        return

    # ------------------------------------------------------------------
    # Connections
    # ------------------------------------------------------------------
    try:
        sf = get_snowflake_client(settings)
    except SnowflakeClientError as exc:
        st.title("📊 Ask the US Economy")
        st.error("Could not connect to Snowflake.")
        st.markdown(
            "Check your `.env` values, role permissions, and Snowflake network "
            "access."
        )
        with st.expander("Error details"):
            st.code(str(exc))
        if "Network policy" in str(exc) or "390432" in str(exc):
            st.info(
                "Snowflake rejected this network location because of its "
                "network policy. Run the app from an allowed network or update "
                "the account network policy in Snowsight."
            )
        return

    try:
        groq = get_groq_client(settings)
    except GroqClientError as exc:
        st.title("📊 Ask the US Economy")
        st.error("Groq is not configured correctly.")
        with st.expander("Error details"):
            st.code(str(exc))
        return

    try:
        with st.spinner("Reading the Snowflake schema..."):
            data_context = load_data_context(sf, settings)
    except SnowflakeClientError as exc:
        st.title("📊 Ask the US Economy")
        st.error("Could not read the Snowflake data schema.")
        st.markdown(
            "Confirm that the configured table exists and that the role can "
            "read it."
        )
        with st.expander("Error details"):
            st.code(str(exc))
        return

    try:
        snapshot = load_snapshot(sf, settings)
    except SnowflakeClientError:
        snapshot = {}

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------
    with st.sidebar:
        st.header("Settings")
        st.caption("Data table")
        st.code(settings.snowflake_table, language="sql")
        st.caption("Semantic view")
        st.code(settings.snowflake_semantic_view or "(not configured)", language="sql")
        st.caption("Groq model")
        st.code(groq.model, language="text")
        st.caption("Snowflake role")
        st.code(settings.snowflake_role or "(default)", language="text")

        if settings.snowflake_role.upper() == "ACCOUNTADMIN":
            st.warning(
                "ACCOUNTADMIN is not read-only. SQL validation blocks writes, "
                "but switch to a SELECT-only role for production."
            )

        st.divider()
        if st.button("Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        if st.button("Reconnect Snowflake", use_container_width=True):
            sf.close()
            get_snowflake_client.clear()
            load_data_context.clear()
            load_snapshot.clear()
            st.rerun()

        if st.button("Test Groq connection", use_container_width=True):
            try:
                with st.spinner("Testing Groq..."):
                    model = groq.test_connection()
                st.success(f"Groq OK: {model}")
            except GroqClientError as exc:
                st.error(str(exc))

        st.divider()
        st.caption("Writes blocked • 200-row cap • No Snowflake Cortex")
        if data_context.max_date:
            st.caption(f"Data through {data_context.max_date}")

    # ------------------------------------------------------------------
    # Header and snapshot
    # ------------------------------------------------------------------
    st.title("📊 Ask the US Economy")
    st.caption(
        "AI-powered economic data analyst • Groq + Snowflake Dynamic Tables • "
        "Data: BLS & Freddie Mac"
    )

    metric_columns = st.columns(3)
    unemployment = snapshot.get("UNEMPLOYMENT_RATE")
    cpi = snapshot.get("CPI")
    mortgage = snapshot.get("MORTGAGE_RATE_30Y")

    metric_columns[0].metric(
        "Unemployment rate",
        _format_percent(unemployment.value) if unemployment else "—",
        help=f"As of {unemployment.date}" if unemployment else "No data available",
    )
    metric_columns[1].metric(
        "CPI index",
        _format_index(cpi.value) if cpi else "—",
        help=f"As of {cpi.date}" if cpi else "No data available",
    )
    metric_columns[2].metric(
        "30-year mortgage",
        _format_percent(mortgage.value) if mortgage else "—",
        help=f"As of {mortgage.date}" if mortgage else "No data available",
    )

    st.divider()

    # ------------------------------------------------------------------
    # Chat history
    # ------------------------------------------------------------------
    if "messages" not in st.session_state:
        st.session_state.messages = []

    def render_message(message: dict) -> None:
        with st.chat_message(message["role"]):
            st.markdown(message.get("content") or "")
            if message.get("error"):
                st.error(message["error"])
            if message.get("sql"):
                with st.expander("View generated SQL"):
                    st.code(message["sql"], language="sql")
            if message.get("columns"):
                frame = pd.DataFrame(
                    message.get("rows") or [],
                    columns=message["columns"],
                )
                with st.expander(f"View query results ({len(frame)} rows)"):
                    st.dataframe(frame, use_container_width=True, hide_index=True)
                    if message.get("truncated"):
                        st.caption(f"Showing the first {settings.max_rows} rows.")

    if not st.session_state.messages:
        with st.chat_message("assistant"):
            st.markdown(
                "Ask me about US inflation, unemployment, or mortgage rates. "
                "Try:\n"
                "- What is the latest unemployment rate?\n"
                "- How has CPI changed over the last 2 years?\n"
                "- Compare the 30-year mortgage rate now versus a year ago."
            )
    else:
        for message in st.session_state.messages:
            render_message(message)

    # ------------------------------------------------------------------
    # Chat input and question -> SQL -> Snowflake -> answer flow
    # ------------------------------------------------------------------
    prompt = st.chat_input("Ask about inflation, unemployment, or mortgage rates...")

    if prompt:
        history = st.session_state.messages[-8:]
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        sql: str | None = None
        result = None
        answer: str | None = None
        error: str | None = None

        with st.chat_message("assistant"):
            with st.status("Working...", expanded=True) as status:
                try:
                    status.write("Generating SQL with Groq...")
                    sql = generate_sql(groq, prompt, data_context, history)

                    status.write("Validating the SQL...")
                    validation = validate_sql(
                        sql,
                        allowed_tables=[settings.snowflake_table],
                        max_rows=settings.max_rows,
                    )
                    if not validation.ok:
                        raise SQLGenerationError(validation.reason)
                    sql = validation.sql

                    status.write("Running the query in Snowflake...")
                    result = sf.execute_query(sql, max_rows=settings.max_rows)

                    status.write("Writing the answer...")
                    answer = generate_answer(groq, prompt, sql, result)
                    status.update(label="Done", state="complete", expanded=False)
                except (
                    SQLGenerationError,
                    SnowflakeClientError,
                    AnswerGenerationError,
                ) as exc:
                    error = str(exc)
                    status.update(
                        label="Could not complete the request",
                        state="error",
                        expanded=True,
                    )
                    st.error(error)
                except Exception as exc:  # defensive: never crash the chat
                    error = f"Unexpected error: {exc}"
                    status.update(
                        label="Could not complete the request",
                        state="error",
                        expanded=True,
                    )
                    st.error(error)

            if answer:
                st.markdown(answer)
            if sql:
                with st.expander("View generated SQL"):
                    st.code(sql, language="sql")
            if result is not None:
                frame = pd.DataFrame(result.rows, columns=result.columns)
                with st.expander(f"View query results ({result.row_count} rows)"):
                    st.dataframe(frame, use_container_width=True, hide_index=True)
                    if result.truncated:
                        st.caption(f"Showing the first {settings.max_rows} rows.")

        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": answer
                or "I couldn't complete that request. See the error details below.",
                "sql": sql,
                "columns": list(result.columns) if result is not None else None,
                "rows": list(result.rows) if result is not None else None,
                "truncated": result.truncated if result is not None else False,
                "error": error,
            }
        )


main()
