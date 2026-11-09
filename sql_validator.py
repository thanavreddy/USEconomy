"""Security validation for LLM-generated SQL.

The validator allows exactly one read-only SELECT statement (optionally with
CTEs) and rejects references to tables outside the approved allowlist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

import sqlparse
from sqlparse import tokens as T
from sqlparse.sql import Function, Identifier, IdentifierList, Parenthesis, TokenList


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating a generated SQL statement."""

    ok: bool
    sql: str = ""
    reason: str = ""


# Keyword tokens that must never appear in a generated query.
BLOCKED_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "UPSERT",
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
    "RENAME",
    "COMMENT",
    "UNDROP",
    "GRANT",
    "REVOKE",
    "CALL",
    "EXECUTE",
    "COPY",
    "PUT",
    "GET",
    "REMOVE",
    "USE",
    "SET",
    "UNSET",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "INTO",
    "ATTACH",
    "DETACH",
    "CLONE",
    "SWAP",
    "LOCK",
    "UNLOCK",
    "INSTALL",
    "UNINSTALL",
}

# Cortex and other AI functions are explicitly forbidden.
FORBIDDEN_PATTERNS = (
    (re.compile(r"\bSNOWFLAKE\s*\.\s*CORTEX\b", re.IGNORECASE), "Snowflake Cortex is not allowed."),
    (re.compile(r"\bCORTEX\b", re.IGNORECASE), "Snowflake Cortex is not allowed."),
    (
        re.compile(
            r"\b(?:AI_COMPLETE|AI_SUMMARIZE|AI_SENTIMENT|AI_CLASSIFY|AI_EXTRACT|"
            r"AI_TRANSLATE|AI_EMBED|COMPLETE|SUMMARIZE|SENTIMENT|CLASSIFY_TEXT|"
            r"EXTRACT_ANSWER|TRANSLATE|EMBED_TEXT_768|EMBED_TEXT_1024)\s*\(",
            re.IGNORECASE,
        ),
        "Snowflake Cortex / AI functions are not allowed.",
    ),
    (re.compile(r"\bIDENTIFIER\s*\(", re.IGNORECASE), "Dynamic identifiers are not allowed."),
    (re.compile(r"\bRESULT_SCAN\s*\(", re.IGNORECASE), "RESULT_SCAN is not allowed."),
    (re.compile(r"\bSYSTEM\$", re.IGNORECASE), "System functions are not allowed."),
)


def _next_significant(tokens: Sequence, start: int) -> int | None:
    for index in range(start, len(tokens)):
        token = tokens[index]
        if token.is_whitespace or token.ttype in T.Comment:
            continue
        return index
    return None


def _identifier_parts(identifier: Identifier) -> list[str]:
    """Return the dotted name parts before any alias/column list."""
    parts: list[str] = []
    for child in identifier.tokens:
        if child.ttype in (T.Name, T.Literal.String.Symbol):
            parts.append(child.value.strip('"'))
        elif child.ttype is T.Punctuation and child.value == ".":
            continue
        elif child.ttype in T.Whitespace or child.ttype in T.Comment:
            if parts:
                break
        else:
            break
    return parts


def _is_subquery(identifier: Identifier) -> bool:
    return any(isinstance(child, Parenthesis) for child in identifier.tokens)


def _collect_cte_names(token_list: TokenList, names: set[str]) -> None:
    tokens = list(token_list.tokens)
    for index, token in enumerate(tokens):
        if token.ttype is T.Keyword.CTE and token.normalized.upper() == "WITH":
            next_index = _next_significant(tokens, index + 1)
            if next_index is None:
                continue
            source = tokens[next_index]
            if isinstance(source, IdentifierList):
                for identifier in source.get_identifiers():
                    name = identifier.get_name()
                    if name:
                        names.add(name.strip('"').upper())
            elif isinstance(source, Identifier):
                name = source.get_name()
                if name:
                    names.add(name.strip('"').upper())
        if token.is_group:
            _collect_cte_names(token, names)


def _add_source(source, references: list[str], cte_names: set[str]) -> None:
    if isinstance(source, IdentifierList):
        for identifier in source.get_identifiers():
            _add_source(identifier, references, cte_names)
        return

    if isinstance(source, Function):
        references.append(f"<function:{source.get_name() or source.value}>")
        return

    if isinstance(source, Identifier):
        if _is_subquery(source):
            # The inner SELECT is visited separately by the recursive walk.
            return
        parts = _identifier_parts(source)
        if parts:
            references.append(".".join(parts).upper())
        return

    if isinstance(source, Parenthesis):
        # Subquery; its inner FROM/JOIN clauses are visited recursively.
        return

    if getattr(source, "ttype", None) in (T.Name, T.Literal.String.Symbol):
        references.append(source.value.strip('"').upper())


def _collect_table_references(
    token_list: TokenList, references: list[str], cte_names: set[str]
) -> None:
    tokens = list(token_list.tokens)
    for index, token in enumerate(tokens):
        normalized = token.normalized.upper() if getattr(token, "normalized", None) else ""
        is_source_keyword = token.ttype is T.Keyword and (
            normalized == "FROM" or normalized.endswith("JOIN")
        )
        if is_source_keyword:
            next_index = _next_significant(tokens, index + 1)
            if next_index is not None:
                _add_source(tokens[next_index], references, cte_names)
        if token.is_group:
            _collect_table_references(token, references, cte_names)


def _is_blocked_keyword(token) -> bool:
    normalized = token.normalized.upper() if getattr(token, "normalized", None) else ""
    if token.ttype in T.Keyword.DML and normalized != "SELECT":
        return True
    if token.ttype in T.Keyword.DDL:
        return True
    if token.ttype is T.Keyword and normalized in BLOCKED_KEYWORDS:
        return True
    return False


def _mask_string_literals(statement) -> str:
    pieces: list[str] = []
    for token in statement.flatten():
        if token.ttype in T.Literal.String:
            pieces.append(" " * len(token.value))
        else:
            pieces.append(token.value)
    return "".join(pieces)


def _normalize_allowed(allowed_tables: Iterable[str]) -> list[str]:
    normalized = []
    for table in allowed_tables:
        cleaned = ".".join(
            part.strip().strip('"').upper() for part in str(table).split(".") if part.strip()
        )
        if cleaned:
            normalized.append(cleaned)
    return normalized


def _is_allowed_table(reference: str, allowed_tables: Sequence[str]) -> bool:
    upper = reference.upper()
    parts = upper.split(".")
    if len(parts) > 3:
        return False
    for allowed in allowed_tables:
        if upper == allowed:
            return True
        if len(parts) == 2 and allowed.endswith("." + upper):
            return True
        if len(parts) == 1 and allowed.rsplit(".", 1)[-1] == upper:
            return True
    return False


def _prepare_text(sql: str) -> str:
    if not isinstance(sql, str):
        return ""
    text = sql.strip()
    # Tolerate an accidental markdown fence from the model.
    fence = re.search(r"```(?:sql)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    return text


def _contains_comment(text: str) -> bool:
    try:
        for statement in sqlparse.parse(text):
            for token in statement.flatten():
                if token.ttype in T.Comment:
                    return True
    except Exception:
        return "--" in text or "/*" in text
    return False


def validate_sql(
    sql: str,
    allowed_tables: Iterable[str],
    max_rows: int = 200,
) -> ValidationResult:
    """Validate a generated SQL statement before it reaches Snowflake.

    Returns a ValidationResult instead of raising so the UI can show a friendly
    message. The returned ``sql`` is the cleaned statement to execute.
    """
    del max_rows  # The row cap is enforced by the Snowflake client fetch.

    text = _prepare_text(sql)
    if not text:
        return ValidationResult(False, reason="No SQL query was generated.")

    if _contains_comment(text):
        return ValidationResult(False, reason="SQL comments are not allowed.")

    try:
        cleaned = sqlparse.format(text, strip_comments=True).strip()
    except Exception:
        cleaned = text

    try:
        statements = [statement for statement in sqlparse.parse(cleaned) if str(statement).strip()]
    except Exception as exc:
        return ValidationResult(False, reason=f"Could not parse the generated SQL: {exc}")

    if len(statements) != 1:
        return ValidationResult(
            False,
            reason="Only one SQL statement is allowed. Multiple statements were detected.",
        )

    statement = statements[0]
    masked = _mask_string_literals(statement)
    stripped = masked.strip()

    if not re.match(r"^(SELECT|WITH)\b", stripped, re.IGNORECASE):
        return ValidationResult(
            False,
            reason="Only read-only SELECT queries (optionally starting with WITH) are allowed.",
        )

    if not re.search(r"\bSELECT\b", stripped, re.IGNORECASE):
        return ValidationResult(False, reason="The query must contain a SELECT statement.")

    for token in statement.flatten():
        if _is_blocked_keyword(token):
            return ValidationResult(
                False,
                reason=f"Write/DDL keyword '{token.value}' is not allowed.",
            )

    for pattern, message in FORBIDDEN_PATTERNS:
        if pattern.search(masked):
            return ValidationResult(False, reason=message)

    cte_names: set[str] = set()
    _collect_cte_names(statement, cte_names)

    references: list[str] = []
    _collect_table_references(statement, references, cte_names)

    allowed = _normalize_allowed(allowed_tables)
    for reference in references:
        if reference.startswith("<function:"):
            return ValidationResult(
                False,
                reason=(
                    "Table functions are not allowed. Query an approved table directly "
                    "instead."
                ),
            )
        if reference in cte_names:
            continue
        if not _is_allowed_table(reference, allowed):
            return ValidationResult(
                False,
                reason=(
                    f"Table '{reference}' is not in the approved table list. "
                    f"Approved table(s): {', '.join(allowed) or '(none configured)'}."
                ),
            )

    execution_sql = cleaned.strip().rstrip(";").strip()
    if not execution_sql:
        return ValidationResult(False, reason="No executable SQL remained after validation.")

    return ValidationResult(True, sql=execution_sql)
