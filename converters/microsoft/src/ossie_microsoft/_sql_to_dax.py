"""A deliberately narrow SQL-to-DAX translator for Apache Ossie metric expressions.

Apache Ossie metrics carry a SQL expression; Power BI evaluates measures only as DAX.
Rather than attempt a general translation, this module recognises a closed set of
shapes whose DAX equivalent is unambiguous, and refuses everything else.

The governing rule is the one stated in `_common`: never emit a plausible-looking
expression that was not authored for the target engine. A metric this module declines
is reported and skipped, which a modeller notices; a metric it mistranslates would
deploy cleanly and quietly return a wrong number.

What is translated
------------------
A single aggregate call over one unqualified column, and `COUNT(*)`. Concretely::

    SUM(amount)            ->  SUM('Sales'[Amount])
    COUNT(DISTINCT cust)   ->  DISTINCTCOUNT('Sales'[Customer])
    COUNT(*)               ->  COUNTROWS('Sales')

What is refused
---------------
Everything else, including arithmetic between aggregates (`SUM(a) / COUNT(*)`),
aggregates over expressions (`SUM(a + b)`), `CASE`, `DISTINCT` outside `COUNT`,
`FILTER`/`OVER` clauses, and any column that cannot be resolved to exactly one
dataset. Ambiguity is treated as failure, never as a guess.

Why the column must resolve
---------------------------
DAX has no bare column reference: `SUM(amount)` is not valid DAX, only
`SUM('Sales'[Amount])` is. The physical name in the SQL (`amount`) is also not
necessarily the Power BI column name -- it is the field's `sourceColumn`, while DAX
addresses the column by its model `name`. So a translation is only possible when the
column resolves to exactly one field in exactly one dataset.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from ._common import DIALECT_ANSI

#: Apache Ossie dialects this module can parse, mapped to their sqlglot reader.
#: Dialects absent from this map (`MDX`, `TABLEAU`, `MAQL`) are not SQL and are refused.
READABLE_DIALECTS = {
    DIALECT_ANSI: None,  # sqlglot's dialect-neutral reader
    "SNOWFLAKE": "snowflake",
    "DATABRICKS": "databricks",
    "BIGQUERY": "bigquery",
}

#: Aggregates whose DAX equivalent takes a single column and means the same thing.
#: Cross-checked against `core-spec/expression_language.md`; every emitted form has
#: been verified to parse against the Power BI DAX grammar.
_AGGREGATES = {
    exp.Sum: "SUM",
    exp.Min: "MIN",
    exp.Max: "MAX",
    exp.Count: "COUNT",
    exp.Avg: "AVERAGE",
    exp.Median: "MEDIAN",
    exp.Stddev: "STDEV.S",
    exp.StddevSamp: "STDEV.S",
    exp.StddevPop: "STDEV.P",
    exp.Variance: "VAR.S",
    exp.VariancePop: "VAR.P",
}


def quote_table(name):
    """Quote a DAX table reference.

    Single quotes are always accepted, including around a name that would not need
    them, so quoting unconditionally avoids a rule that could be applied wrongly.
    """
    escaped = name.replace("'", "''")
    return f"'{escaped}'"


def quote_column(name):
    """Quote a DAX column reference. `]` is escaped by doubling."""
    escaped = name.replace("]", "]]")
    return f"[{escaped}]"


def translate(expression, dialect, resolve_column, resolve_table):
    """Translate a SQL aggregate to DAX, or return ``(None, reason)``.

    ``resolve_column`` takes a SQL identifier and returns ``(table, column)`` naming
    the Power BI table and column, or ``None`` when the name is unknown or ambiguous.
    ``resolve_table`` returns the sole table name for a ``COUNT(*)``, or ``None``.

    Returns ``(dax, None)`` on success and ``(None, reason)`` otherwise, where
    ``reason`` explains the refusal in terms a modeller can act on.
    """
    if dialect not in READABLE_DIALECTS:
        return None, f"'{dialect}' is not a SQL dialect this converter can parse"

    try:
        tree = sqlglot.parse_one(expression, read=READABLE_DIALECTS[dialect])
    except Exception:
        return None, "the expression could not be parsed as SQL"

    # `parse_one` wraps an aliased expression, e.g. `SUM(x) AS total`.
    if isinstance(tree, exp.Alias):
        tree = tree.this

    dax_function = _AGGREGATES.get(type(tree))
    if dax_function is None:
        return None, (
            "only a single aggregate over one column translates unambiguously to DAX"
        )

    # `OVER` and `FILTER (WHERE ...)` parse as wrapper nodes (`exp.Window`,
    # `exp.Filter`) rather than as arguments, so they are already refused above by not
    # being aggregates. `ORDER BY` inside an aggregate is refused the same way.
    argument = tree.this
    distinct = False
    if isinstance(argument, exp.Distinct):
        operands = argument.args.get("expressions") or []
        if len(operands) != 1:
            return None, "'DISTINCT' over multiple columns has no DAX equivalent"
        distinct = True
        argument = operands[0]

    if isinstance(tree, exp.Count):
        if isinstance(argument, exp.Star):
            if distinct:
                return None, "'COUNT(DISTINCT *)' has no DAX equivalent"
            table = resolve_table()
            if table is None:
                return None, "'COUNT(*)' needs exactly one dataset to count rows of"
            return f"COUNTROWS({quote_table(table)})", None
        dax_function = "DISTINCTCOUNT" if distinct else "COUNT"
    elif distinct:
        # `SUM(DISTINCT x)` deduplicates before aggregating; DAX has no such form.
        return None, f"'DISTINCT' inside '{dax_function}' has no DAX equivalent"

    if not isinstance(argument, exp.Column):
        return None, "only an aggregate over a single column reference is translated"
    if argument.args.get("table") or argument.args.get("db"):
        # A qualified reference implies a join or alias the Ossie document does not
        # describe, so the intended Power BI table cannot be established.
        return None, "a qualified column reference is not translated"

    resolved = resolve_column(argument.name)
    if resolved is None:
        return None, (
            f"column '{argument.name}' does not resolve to exactly one dataset field"
        )
    table, column = resolved
    return f"{dax_function}({quote_table(table)}{quote_column(column)})", None
