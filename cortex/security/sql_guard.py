"""SQL safety (OWASP LLM05 improper output handling) and row-level security.

Every SQL string, whether compiled from the semantic layer or written by an LLM,
passes through `guard()` before execution:
  1. parse with sqlglot; reject anything that is not a single SELECT
  2. reject unknown tables and dangerous functions (file readers, extensions)
  3. enforce a LIMIT
  4. apply row-level security by shadowing base tables with filtered CTEs,
     so the restriction holds no matter what SQL the model wrote.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

BLOCKED_FUNCS = {"read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
                 "read_blob", "glob", "load", "install", "attach", "copy", "export", "pragma", "getenv",
                 "query_table", "query"}


class SQLRejected(ValueError):
    pass


@dataclass
class GuardedSQL:
    sql: str
    tables: list[str]
    rls_applied: bool


def _rls_ctes(values: list[str]) -> str:
    """Shadow each scoped base table with a filtered CTE, as the domain pack defines them."""
    from cortex.domain import get_domain
    vals = ", ".join("'" + str(v).replace("'", "") + "'" for v in values)
    ctes = get_domain().rls.get("ctes", {})
    return ", ".join(f"{name} AS ({tmpl.format(values=vals)})" for name, tmpl in ctes.items())


def guard(sql: str, allowed_scope: list[str] | str = "*", row_limit: int = 200,
          allowed_tables: set[str] | None = None) -> GuardedSQL:
    if allowed_tables is None:
        from cortex.domain import get_domain
        allowed_tables = get_domain().tables
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as e:
        raise SQLRejected(f"unparseable SQL: {e}") from e
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SQLRejected("exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)) and not tree.find(exp.Select):
        raise SQLRejected(f"only SELECT is allowed, got {type(tree).__name__}")
    for node_type in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command,
                      exp.Copy, exp.Pragma, exp.Attach if hasattr(exp, "Attach") else exp.Command):
        if tree.find(node_type):
            raise SQLRejected(f"forbidden statement: {node_type.__name__}")
    if any(t.args.get("db") or t.args.get("catalog") for t in tree.find_all(exp.Table)):
        raise SQLRejected("schema-qualified table names are not allowed")
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables = sorted({t.name.lower() for t in tree.find_all(exp.Table)} - cte_names)
    unknown = [t for t in tables if t not in allowed_tables]
    if unknown:
        raise SQLRejected(f"unknown or forbidden tables: {unknown}")
    for fn in tree.find_all(exp.Func):
        name = (fn.sql_name() if hasattr(fn, "sql_name") else type(fn).__name__).lower()
        anon = fn.name.lower() if isinstance(fn, exp.Anonymous) else name
        if anon in BLOCKED_FUNCS or name in BLOCKED_FUNCS:
            raise SQLRejected(f"forbidden function: {anon}")
    if isinstance(tree, exp.Select) and not tree.args.get("limit"):
        tree = tree.limit(row_limit)
    body = tree.sql(dialect="duckdb")
    rls = allowed_scope != "*"
    if rls:
        body = f"WITH {_rls_ctes(list(allowed_scope))} SELECT * FROM ({body}) AS rls_q"
    return GuardedSQL(sql=body, tables=tables, rls_applied=rls)
