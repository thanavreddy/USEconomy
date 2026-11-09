# Ask the US Economy — AI-Powered Snowflake Data Analyst

A small Streamlit application that answers natural-language questions about the
US economic data already stored in Snowflake. It uses **Groq** for text-to-SQL
and answer generation, and the **Snowflake Python Connector** to run the query.

The app reuses the existing project pipeline:

```
Snowflake Marketplace data
        ↓
ECONOMIC_DASHBOARD_LIVE (existing Dynamic Table)
        ↓
ECONOMIC_DASHBOARD_SEMANTIC (existing Semantic View)
        ↓
Groq text-to-SQL + Snowflake query + grounded answer
```

No data pipeline code was rebuilt, no Snowflake objects are modified, and
**Snowflake Cortex is not used**.

---

## What the app does

1. Reads the actual schema of the configured Dynamic Table from Snowflake.
2. Reads the Semantic View definition when it is available.
3. Sends the schema and question to Groq to generate **one read-only SELECT**.
4. Validates the SQL before execution:
   - only `SELECT` / `WITH ... SELECT`
   - one statement only
   - approved table allowlist only
   - blocks INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, etc.
   - blocks Snowflake Cortex/AI functions and dynamic `IDENTIFIER(...)`
5. Runs the query with the Snowflake Python Connector.
6. Fetches at most **200 rows**.
7. Sends the real query results back to Groq for a concise grounded answer.
8. Displays the answer, the generated SQL (expandable), and the result table.

---

## Project structure

| File | Purpose |
| --- | --- |
| `app.py` | Streamlit chat interface, metrics, history, errors |
| `config.py` | Loads and validates environment variables |
| `snowflake_client.py` | Connection, schema introspection, query execution |
| `groq_client.py` | Groq SDK wrapper and model selection |
| `sql_generator.py` | Prompt construction, SQL extraction, answer generation |
| `sql_validator.py` | Read-only SQL validation and table allowlist |
| `requirements.txt` | Python dependencies |
| `.env.example` | Template for local configuration |
| `.gitignore` | Keeps `.env` and caches out of git |
| `tests/` | Offline unit tests |

The original notebooks (`economy-agent-template.ipynb`, `modified.ipynb`) are
**not modified** by this application.

---

## Existing Snowflake objects used

These are the objects configured by default, based on the existing project:

- Database / schema: `ECON_AGENT_DB.ANALYTICS`
- Dynamic Table: `ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE`
- Semantic View: `ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_SEMANTIC`
- Warehouse: `COMPUTE_WH`

The app does **not** trust a hard-coded column list. At startup it reads the
actual columns from `INFORMATION_SCHEMA.COLUMNS` (with a `DESCRIBE` fallback).
If your object names differ, change `SNOWFLAKE_TABLE` and
`SNOWFLAKE_SEMANTIC_VIEW` in `.env`.

Existing metric definitions used by the prompts:

- `CPI`: Consumer Price Index, base 1982-84 = 100. This is an index level, not
  an inflation percentage.
- `UNEMPLOYMENT_RATE`: decimal fraction, e.g. `0.041 = 4.1%`.
- `MORTGAGE_RATE_30Y`: decimal fraction, e.g. `0.0626 = 6.26%`.

---

## Prerequisites

- Python 3.10 or newer
- A Snowflake account with access to the existing database, Dynamic Table, and
  warehouse
- A Groq API key: <https://console.groq.com/keys>

---

## Setup

### Windows PowerShell

```powershell
cd "C:\Users\Public\coding projects\Final year\snowflake tim"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env and fill in your values
python -m streamlit run app.py
```

### macOS / Linux

```bash
cd "/path/to/snowflake tim"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env and fill in your values
python -m streamlit run app.py
```

Open the URL Streamlit prints (normally <http://localhost:8501>).

---

## Environment variables

Fill in `.env` (never commit it):

```dotenv
GROQ_API_KEY=your_groq_key
GROQ_MODEL=llama-3.3-70b-versatile

SNOWFLAKE_ACCOUNT=your-org-your-account
SNOWFLAKE_USER=your_user
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=ECON_AGENT_DB
SNOWFLAKE_SCHEMA=ANALYTICS
SNOWFLAKE_ROLE=your_read_only_role
```

Optional values:

| Variable | Default | Notes |
| --- | --- | --- |
| `SNOWFLAKE_TABLE` | `ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE` | Fully-qualified table/Dynamic Table |
| `SNOWFLAKE_SEMANTIC_VIEW` | `ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_SEMANTIC` | Used for metric definitions |
| `SNOWFLAKE_AUTHENTICATOR` | empty | Set to `PROGRAMMATIC_ACCESS_TOKEN` if `SNOWFLAKE_PASSWORD` holds a PAT |
| `MAX_RESULT_ROWS` | `200` | Capped at 200 |
| `QUERY_TIMEOUT_SECONDS` | `60` | Snowflake statement timeout |

> If a value contains `#`, spaces, or quotes, wrap it in double quotes in
> `.env`. The account identifier is the Snowflake account identifier
> (`org-account`), **not** a full `https://...snowflakecomputing.com` URL.

---

## Read-only Snowflake role (recommended)

The application validates SQL before execution, but a read-only role is still
important defense in depth. Run this once in Snowsight as an administrator
(adjust names if needed):

```sql
CREATE ROLE IF NOT EXISTS ECON_AGENT_READONLY;

GRANT USAGE ON DATABASE ECON_AGENT_DB TO ROLE ECON_AGENT_READONLY;
GRANT USAGE ON SCHEMA ECON_AGENT_DB.ANALYTICS TO ROLE ECON_AGENT_READONLY;
GRANT SELECT ON TABLE ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_LIVE
  TO ROLE ECON_AGENT_READONLY;
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE ECON_AGENT_READONLY;

GRANT ROLE ECON_AGENT_READONLY TO USER <YOUR_SNOWFLAKE_USER>;
```

Then set:

```dotenv
SNOWFLAKE_ROLE=ECON_AGENT_READONLY
```

`ACCOUNTADMIN` works for a quick local test, but it is **not** read-only. The
sidebar shows a warning when `ACCOUNTADMIN` is configured.

If the role cannot read the Semantic View DDL, the app simply falls back to the
verified table columns and metric notes. Asking questions still works.

---

## How a question flows

```
User question
  → sql_generator.generate_sql()      (Groq)
  → sql_validator.validate_sql()      (security)
  → SnowflakeClient.execute_query()   (Snowflake, max 200 rows)
  → sql_generator.generate_answer()   (Groq, grounded in results)
  → Streamlit answer + SQL + table
```

Follow-up questions work because the last several chat messages (including
previous generated SQL) are included in the SQL-generation prompt.

---

## Testing

Offline tests (no Snowflake or Groq credentials required):

```powershell
python -m unittest discover -s tests -v
```

Syntax/import check:

```powershell
python -m py_compile app.py config.py snowflake_client.py groq_client.py sql_generator.py sql_validator.py
```

Run the app:

```powershell
python -m streamlit run app.py
```

Manual checks with real credentials:

1. Ask: *What is the latest unemployment rate?*
2. Ask: *How has CPI changed over the last 2 years?*
3. Ask: *Compare the 30-year mortgage rate now versus a year ago.*
4. Confirm the generated SQL expander only contains `SELECT` / `WITH`.
5. Confirm the answer matches the displayed result table.

---

## Security notes

- API keys and passwords come only from environment variables / `.env`.
- `.env` is ignored by git.
- Generated SQL must pass `sql_validator.py` before reaching Snowflake.
- Only the configured table is allowlisted.
- Writes, DDL, multiple statements, comments, dynamic identifiers, table
  functions, and Cortex/AI functions are rejected.
- Results are truncated to 200 rows by the Snowflake client.
- Snowflake errors are shown without exposing credentials.
- No Snowflake objects are created, modified, or deleted by the app.

---

## Troubleshooting

### `390432 (08001): ... Network policy is required`

Snowflake is rejecting the network location. Run the app from an allowed
network, or update the account network policy in Snowsight. This is an account
setting, not an application error.

### `Could not connect to Snowflake`

- Confirm `SNOWFLAKE_ACCOUNT` is the account identifier, not a URL.
- Confirm the user/password (or PAT) and role.
- If using a programmatic access token, set
  `SNOWFLAKE_AUTHENTICATOR=PROGRAMMATIC_ACCESS_TOKEN` and put the token in
  `SNOWFLAKE_PASSWORD`.

### `No columns were found for ...`

- Confirm `SNOWFLAKE_TABLE` is fully qualified and exists.
- Confirm the role has `USAGE` on the database/schema and `SELECT` on the table.

### `GROQ_MODEL ... is not available`

Set `GROQ_MODEL` to one of the models listed in the error, or leave it empty to
let the app choose an available preferred model. The **Test Groq connection**
button in the sidebar also lists the problem.

### The model generated an invalid SQL statement

The validator will refuse it and show the reason. Rephrase the question, or
make it more specific (date range, metric, aggregation). The generated SQL is
still shown in the expander so you can inspect it.

### No data returned

The Dynamic Table refreshes on its own schedule and the data may lag. Try a
wider date range or a different metric.
