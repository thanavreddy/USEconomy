"""Snowflake connection, schema introspection, and read-only query execution."""

from __future__ import annotations

import atexit
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import snowflake.connector

from config import Settings


class SnowflakeClientError(Exception):
    """Raised for connection, schema, or query failures."""


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: str
    comment: str


@dataclass(frozen=True)
class DataContext:
    table: str
    columns: tuple[ColumnInfo, ...]
    row_count: int | None
    min_date: str | None
    max_date: str | None
    semantic_view: str
    semantic_view_ddl: str


@dataclass(frozen=True)
class SnapshotValue:
    date: str | None
    value: float


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    truncated: bool
    elapsed_ms: float
    sql: str


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_DATE_TYPES = {"DATE", "DATETIME", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}
_SNAPSHOT_METRICS = ("CPI", "UNEMPLOYMENT_RATE", "MORTGAGE_RATE_30Y")


def parse_table_name(table_name: str) -> tuple[str, str, str]:
    """Split and validate a fully-qualified Snowflake object name.

    Only simple identifiers are accepted because the parts are interpolated
    into introspection SQL (Snowflake does not allow bind parameters there).
    """
    parts = [part.strip().strip('"') for part in str(table_name).split(".")]
    if len(parts) != 3 or not all(_IDENTIFIER_RE.match(part) for part in parts):
        raise SnowflakeClientError(
            f"Invalid Snowflake object name '{table_name}'. "
            "Use the fully-qualified form DATABASE.SCHEMA.OBJECT."
        )
    return parts[0], parts[1], parts[2]


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _find_date_column(columns: tuple[ColumnInfo, ...]) -> str | None:
    for column in columns:
        if column.name.upper() == "DATE":
            return column.name
    for column in columns:
        if column.data_type.upper() in _DATE_TYPES:
            return column.name
    return None


class SnowflakeClient:
    """Small wrapper around the Snowflake Python connector."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._connection = None
        self._schema_cache: dict[str, tuple[ColumnInfo, ...]] = {}
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> None:
        if self._connection is not None:
            try:
                if not self._connection.is_closed():
                    return
            except Exception:
                pass

        kwargs = {
            "account": self.settings.snowflake_account,
            "user": self.settings.snowflake_user,
            "warehouse": self.settings.snowflake_warehouse,
            "database": self.settings.snowflake_database,
            "schema": self.settings.snowflake_schema,
            "application": "ECON_AI_ANALYST",
            "login_timeout": 30,
            "network_timeout": 60,
            "session_parameters": {
                "STATEMENT_TIMEOUT_IN_SECONDS": str(self.settings.query_timeout_seconds)
            },
        }
        if self.settings.snowflake_role:
            kwargs["role"] = self.settings.snowflake_role

        authenticator = self.settings.snowflake_authenticator.upper()
        if authenticator in {"PROGRAMMATIC_ACCESS_TOKEN", "PAT"}:
            kwargs["authenticator"] = "PROGRAMMATIC_ACCESS_TOKEN"
            kwargs["token"] = self.settings.snowflake_password
        elif authenticator == "OAUTH":
            kwargs["authenticator"] = "oauth"
            kwargs["token"] = self.settings.snowflake_password
        else:
            kwargs["password"] = self.settings.snowflake_password

        try:
            self._connection = snowflake.connector.connect(**kwargs)
            self._schema_cache.clear()
        except Exception as exc:
            self._connection = None
            raise SnowflakeClientError(f"Could not connect to Snowflake: {exc}") from exc

    def close(self) -> None:
        if self._connection is None:
            return
        try:
            if not self._connection.is_closed():
                self._connection.close()
        except Exception:
            pass
        finally:
            self._connection = None

    def _ensure_connection(self) -> None:
        if self._connection is None:
            self.connect()
            return
        try:
            if self._connection.is_closed():
                self.connect()
        except Exception:
            self._connection = None
            self.connect()

    # ------------------------------------------------------------------
    # Low-level query helpers
    # ------------------------------------------------------------------
    def _query(
        self,
        sql: str,
        params: tuple[Any, ...] | None = None,
        max_rows: int | None = None,
    ) -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...], bool]:
        self._ensure_connection()
        cursor = self._connection.cursor()
        try:
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)

            if not cursor.description:
                return (), (), False

            columns = tuple(description[0] for description in cursor.description)
            if max_rows is None:
                rows = cursor.fetchall()
                truncated = False
            else:
                rows = cursor.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                rows = rows[:max_rows]
            return columns, tuple(tuple(row) for row in rows), truncated
        except SnowflakeClientError:
            raise
        except Exception as exc:
            raise SnowflakeClientError(f"Snowflake query failed: {exc}") from exc
        finally:
            try:
                cursor.close()
            except Exception:
                pass

    def execute_query(self, sql: str, max_rows: int | None = None) -> QueryResult:
        """Execute a validated SELECT and return at most ``max_rows`` rows."""
        limit = self.settings.max_rows if max_rows is None else max_rows
        limit = max(1, min(limit, self.settings.max_rows))
        started = time.perf_counter()
        columns, rows, truncated = self._query(sql, max_rows=limit)
        elapsed_ms = (time.perf_counter() - started) * 1000
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=round(elapsed_ms, 1),
            sql=sql,
        )

    def test_connection(self) -> dict[str, str | None]:
        """Run a tiny query and return the active Snowflake context."""
        columns, rows, _ = self._query(
            "SELECT CURRENT_ACCOUNT() AS ACCOUNT, CURRENT_USER() AS USER, "
            "CURRENT_ROLE() AS ROLE, CURRENT_WAREHOUSE() AS WAREHOUSE, "
            "CURRENT_DATABASE() AS DATABASE, CURRENT_SCHEMA() AS SCHEMA"
        )
        if not rows:
            return {}
        return {
            str(column).lower(): _to_text(value)
            for column, value in zip(columns, rows[0])
        }

    # ------------------------------------------------------------------
    # Schema / semantic view introspection
    # ------------------------------------------------------------------
    def get_table_schema(self, table_name: str | None = None) -> tuple[ColumnInfo, ...]:
        table = table_name or self.settings.snowflake_table
        cache_key = table.upper()
        if cache_key in self._schema_cache:
            return self._schema_cache[cache_key]

        database, schema, object_name = parse_table_name(table)
        # If this query fails, it is almost certainly a connection/permission
        # problem. Re-raise immediately instead of retrying DESCRIBE queries.
        _, rows, _ = self._query(
            f"""
            SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COALESCE(COMMENT, '') AS COMMENT
            FROM {database}.INFORMATION_SCHEMA.COLUMNS
            WHERE UPPER(TABLE_SCHEMA) = %s
              AND UPPER(TABLE_NAME) = %s
            ORDER BY ORDINAL_POSITION
            """,
            (schema.upper(), object_name.upper()),
        )

        if rows:
            columns = tuple(
                ColumnInfo(
                    name=str(row[0]),
                    data_type=str(row[1]),
                    nullable=str(row[2]),
                    comment=str(row[3] or ""),
                )
                for row in rows
            )
            self._schema_cache[cache_key] = columns
            return columns

        # Fallback for object types that do not expose columns via
        # INFORMATION_SCHEMA.COLUMNS.
        columns = self._describe_table(table)

        if not columns:
            raise SnowflakeClientError(
                f"No columns were found for {table}. Check the table name, "
                "database/schema, and role permissions."
            )

        self._schema_cache[cache_key] = columns
        return columns

    def _describe_table(self, table: str) -> tuple[ColumnInfo, ...]:
        for object_type in ("DYNAMIC TABLE", "TABLE", "VIEW"):
            try:
                columns, rows, _ = self._query(f"DESCRIBE {object_type} {table}")
            except SnowflakeClientError:
                continue
            if not rows:
                continue

            names = [str(column).lower() for column in columns]

            def index_of(*candidates: str, default: int | None = None) -> int | None:
                for candidate in candidates:
                    if candidate in names:
                        return names.index(candidate)
                return default

            name_index = index_of("name", default=0)
            type_index = index_of("type", default=1)
            null_index = index_of("null?", "null", default=2)
            comment_index = index_of("comment", default=None)

            result = []
            for row in rows:
                if name_index is None or name_index >= len(row):
                    continue
                name = row[name_index]
                if not name:
                    continue
                result.append(
                    ColumnInfo(
                        name=str(name),
                        data_type=(
                            str(row[type_index] or "")
                            if type_index is not None and type_index < len(row)
                            else ""
                        ),
                        nullable=(
                            str(row[null_index] or "")
                            if null_index is not None and null_index < len(row)
                            else ""
                        ),
                        comment=(
                            str(row[comment_index] or "")
                            if comment_index is not None and comment_index < len(row)
                            else ""
                        ),
                    )
                )
            if result:
                return tuple(result)
        return ()

    def get_table_stats(
        self,
        columns: tuple[ColumnInfo, ...],
        table_name: str | None = None,
    ) -> tuple[int | None, str | None, str | None]:
        table = table_name or self.settings.snowflake_table
        database, schema, object_name = parse_table_name(table)
        fqn = f"{database}.{schema}.{object_name}"
        date_column = _find_date_column(columns)
        if not date_column:
            return None, None, None
        try:
            _, rows, _ = self._query(
                f"SELECT COUNT(*) AS ROW_COUNT, MIN({date_column}) AS MIN_DATE, "
                f"MAX({date_column}) AS MAX_DATE FROM {fqn}"
            )
        except SnowflakeClientError:
            return None, None, None
        if not rows:
            return None, None, None
        row = rows[0]
        row_count = int(row[0]) if row[0] is not None else None
        return row_count, _to_text(row[1]), _to_text(row[2])

    def get_semantic_view_ddl(self, semantic_view: str | None = None) -> str:
        view = semantic_view or self.settings.snowflake_semantic_view
        if not view:
            return ""
        try:
            database, schema, object_name = parse_table_name(view)
        except SnowflakeClientError:
            return ""
        fqn = f"{database}.{schema}.{object_name}"

        try:
            _, rows, _ = self._query(
                "SELECT GET_DDL('SEMANTIC_VIEW', %s) AS DDL", (fqn,)
            )
            if rows and rows[0][0]:
                return str(rows[0][0])
        except SnowflakeClientError:
            pass

        try:
            columns, rows, _ = self._query(f"DESCRIBE SEMANTIC VIEW {fqn}")
            if rows:
                lines = [f"Semantic view: {fqn}"]
                for row in rows[:100]:
                    lines.append(
                        " | ".join(
                            f"{column}={value}" for column, value in zip(columns, row)
                        )
                    )
                return "\n".join(lines)
        except SnowflakeClientError:
            pass

        return ""

    def get_data_context(self) -> DataContext:
        table = self.settings.snowflake_table
        columns = self.get_table_schema(table)
        row_count, min_date, max_date = self.get_table_stats(columns, table)
        return DataContext(
            table=table,
            columns=columns,
            row_count=row_count,
            min_date=min_date,
            max_date=max_date,
            semantic_view=self.settings.snowflake_semantic_view,
            semantic_view_ddl=self.get_semantic_view_ddl(),
        )

    def get_latest_snapshot(self, table_name: str | None = None) -> dict[str, SnapshotValue]:
        """Return the most recent non-null value for each known dashboard metric."""
        table = table_name or self.settings.snowflake_table
        database, schema, object_name = parse_table_name(table)
        fqn = f"{database}.{schema}.{object_name}"
        columns = self.get_table_schema(fqn)
        date_column = _find_date_column(columns)
        if not date_column:
            return {}

        metric_columns = [
            column.name
            for column in columns
            if column.name.upper() in _SNAPSHOT_METRICS
        ]
        if not metric_columns:
            return {}

        selected = ", ".join([date_column, *metric_columns])
        try:
            result_columns, rows, _ = self._query(
                f"SELECT {selected} FROM {fqn} "
                f"ORDER BY {date_column} DESC LIMIT 30"
            )
        except SnowflakeClientError:
            return {}

        if not rows:
            return {}

        upper_columns = [column.upper() for column in result_columns]
        date_index = upper_columns.index(date_column.upper())
        snapshot: dict[str, SnapshotValue] = {}
        for metric in metric_columns:
            metric_index = upper_columns.index(metric.upper())
            for row in rows:
                if row[metric_index] is not None:
                    snapshot[metric.upper()] = SnapshotValue(
                        date=_to_text(row[date_index]),
                        value=float(row[metric_index]),
                    )
                    break
        return snapshot
