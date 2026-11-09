"""Ask Snowflake Cortex Analyst a question, using credentials from .env."""

import os

import requests
import snowflake.connector
from dotenv import load_dotenv

# Load credentials from .env instead of hard-coding them in this file.
load_dotenv()

ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT", "").strip()
USER = os.getenv("SNOWFLAKE_USER", "").strip()
PASSWORD = os.getenv("SNOWFLAKE_PASSWORD", "").strip()
WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE", "").strip()
DATABASE = os.getenv("SNOWFLAKE_DATABASE", "").strip()
SCHEMA = os.getenv("SNOWFLAKE_SCHEMA", "").strip()
SEMANTIC_VIEW = os.getenv(
    "SNOWFLAKE_SEMANTIC_VIEW",
    "ECON_AGENT_DB.ANALYTICS.ECONOMIC_DASHBOARD_SEMANTIC",
).strip()
AUTHENTICATOR = os.getenv("SNOWFLAKE_AUTHENTICATOR", "").strip().upper()

missing = [
    name
    for name in (
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
    )
    if not os.getenv(name, "").strip()
]
if missing:
    raise SystemExit(
        "Missing environment variable(s): " + ", ".join(missing) + ". Fill in .env."
    )

HOST = f"{ACCOUNT}.snowflakecomputing.com"

# Step 1 — Get a session token for the REST API
login = requests.post(
    f"https://{HOST}/session/v1/login-request",
    json={"data": {"ACCOUNT_NAME": ACCOUNT, "LOGIN_NAME": USER, "PASSWORD": PASSWORD}},
    headers={"Content-Type": "application/json"},
)
token = login.json()["data"]["token"]

# Step 2 — Ask Cortex Analyst (REST API)
question = "What is the current unemployment rate?"
resp = requests.post(
    f"https://{HOST}/api/v2/cortex/analyst/message",
    json={
        "messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
        "semantic_view": SEMANTIC_VIEW,
    },
    headers={"Authorization": f'Snowflake Token="{token}"', "Content-Type": "application/json"},
)

# Step 3 — Parse the response
data = resp.json()
sql_statement = None
for block in data.get("message", {}).get("content", []):
    if block["type"] == "text":
        print("🧠 Analyst:", block["text"])
    elif block["type"] == "sql":
        sql_statement = block["statement"]
        print(f"\n📝 Generated SQL:\n{sql_statement}")

# Step 4 — Execute the SQL with the Python connector
if sql_statement:
    connect_kwargs = {
        "account": ACCOUNT,
        "user": USER,
        "warehouse": WAREHOUSE,
        "database": DATABASE,
        "schema": SCHEMA,
    }
    if AUTHENTICATOR in {"PROGRAMMATIC_ACCESS_TOKEN", "PAT"}:
        connect_kwargs["authenticator"] = "PROGRAMMATIC_ACCESS_TOKEN"
        connect_kwargs["token"] = PASSWORD
    else:
        connect_kwargs["password"] = PASSWORD

    conn = snowflake.connector.connect(**connect_kwargs)
    cur = conn.cursor()
    cur.execute(sql_statement)
    cols = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    print("\n📊 Result:")
    print(" | ".join(cols))
    for row in rows:
        print(" | ".join(str(v) for v in row))
    cur.close()
    conn.close()
