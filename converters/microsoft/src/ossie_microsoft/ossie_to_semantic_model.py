# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Convert an Apache Ossie (OSI) semantic model to a Power BI model (``model.bim`` / TMSL).

Datasets become tables, fields become columns, metrics become measures and relationships
become TMSL relationships. When the document was produced by
:mod:`ossie_microsoft.semantic_model_to_ossie`, the ``POWER_BI``
``custom_extensions`` blob is replayed so the original Power BI model is restored rather
than approximated.

Expressions are never rewritten between languages. Power BI evaluates DAX; an Apache
Ossie expression written in ``ANSI_SQL`` (or any other dialect) is only usable when it is
a plain column reference, which maps to a TMSL ``sourceColumn``. Anything else is
reported through :func:`ossie_microsoft._common.warn` and skipped, because emitting a
mechanically rewritten expression that was never authored for the DAX engine would
produce a model that looks correct and computes the wrong answer.
"""

from ._common import (
    DEFAULT_COMPATIBILITY_LEVEL,
    DIALECT_ANSI,
    DIALECT_DAX,
    DATE_ONLY_FORMAT,
    IDENTIFIER_RE,
    OSSIE_TO_TMSL_DATATYPE,
    OSSIE_VERSION,
    TMSL_TO_OSSIE_DATATYPE,
    ConversionError,
    dialect_expressions,
    foreign_vendor_extensions,
    prune,
    read_stash,
    warn,
)

# Placeholder partition for a dataset whose Power BI partition was not preserved. It is
# deliberately an `error` expression: a refresh fails loudly with an actionable message
# instead of a plausible-looking query that silently loads nothing.
_PARTITION_PLACEHOLDER = (
    'let\n'
    '    Source = error "Apache Ossie source ""{source}"" has no Power BI connection. '
    'Replace this placeholder partition with a real query."\n'
    'in\n'
    '    Source'
)

# Stash keys the export interprets rather than replays as TMSL properties.
_STASH_CONTROL_KEYS = frozenset(
    {"excludedTables", "excludedRelationships", "document", "descriptionSource"}
)
_TABLE_CONTROL_KEYS = frozenset({"excludedColumns"})
_RELATIONSHIP_CONTROL_KEYS = frozenset({"flipped", "name"})
_MEASURE_CONTROL_KEYS = frozenset({"table", "name"})
_COLUMN_CONTROL_KEYS = frozenset({"dataType"})

# Apache Ossie temporal types that Power BI cannot represent faithfully. Power BI stores
# every temporal value as `dateTime`, so a time-of-day type gains a date part and a
# timezone-aware type loses its offset.
_LOSSY_TEMPORAL = {
    "Time": "Power BI has no time-only data type; stored as dateTime with a date part",
    "DateTimeTz": "Power BI has no timezone-aware data type; the UTC offset is lost",
}


def convert_ossie_to_semantic_model(document):
    """Convert an Apache Ossie document into a Power BI ``model.bim`` document.

    Args:
        document: A parsed Apache Ossie semantic model document.

    Returns:
        The TMSL ``model.bim`` document as a dict, ready to be serialized as JSON.

    Raises:
        TypeError: if `document` is not a parsed Apache Ossie document.
        ValueError: if the document contains no semantic model.
    """
    if not isinstance(document, dict):
        raise TypeError("document must be a parsed Apache Ossie document (dict)")

    version = document.get("version")
    if version and version != OSSIE_VERSION:
        warn(
            "document",
            f"document targets Apache Ossie spec {version}, this converter targets "
            f"{OSSIE_VERSION}; conversion may be incomplete",
        )

    models = document.get("semantic_model")
    if not isinstance(models, list) or not models or not isinstance(models[0], dict):
        raise ValueError("document is missing a 'semantic_model' entry")
    if len(models) > 1:
        warn(
            "document",
            f"a model.bim holds a single model; converting the first of {len(models)} "
            "and skipping the rest",
        )
    semantic_model = models[0]

    stash = read_stash(semantic_model)
    _warn_foreign_extensions("model", semantic_model)

    tables, table_columns = _convert_datasets(semantic_model.get("datasets") or [])
    _apply_measures(tables, semantic_model.get("metrics") or [])

    relationships = _convert_relationships(
        semantic_model.get("relationships") or [], table_columns
    )
    relationships.extend(stash.get("excludedRelationships") or [])

    # Tables the import excluded (private, calculation group, auto date table) are
    # restored verbatim so a round trip reproduces the original model.
    tables.extend(stash.get("excludedTables") or [])

    model = {"tables": tables}
    description = semantic_model.get("description")
    if description and stash.get("descriptionSource") != "document":
        model["description"] = description
    if relationships:
        model["relationships"] = relationships
    # Everything the import could not represent, replayed verbatim. `setdefault` so a
    # preserved value can never overwrite a property derived from the current core
    # fields -- the core document is the source of truth if the two disagree.
    document_properties = dict(stash.get("document") or {})
    for key, value in stash.items():
        if key not in _STASH_CONTROL_KEYS:
            model.setdefault(key, value)
    model.setdefault("culture", "en-US")

    bim = {"name": semantic_model.get("name") or "semantic_model"}
    if description and stash.get("descriptionSource") == "document":
        bim["description"] = description
    bim.update(document_properties)
    bim.setdefault("compatibilityLevel", DEFAULT_COMPATIBILITY_LEVEL)
    bim["model"] = model
    return bim


# ---------------------------------------------------------------------------
# Datasets -> tables
# ---------------------------------------------------------------------------


def _convert_datasets(datasets):
    tables = []
    # Maps a table name to the set of its column names, used to validate relationships.
    table_columns = {}
    for dataset in datasets:
        if not isinstance(dataset, dict) or not dataset.get("name"):
            continue
        table = _convert_dataset(dataset)
        tables.append(table)
        table_columns[table["name"]] = {c["name"] for c in table["columns"]}
    return tables, table_columns


def _convert_dataset(dataset):
    name = dataset["name"]
    scope = f"dataset '{name}'"
    stash = read_stash(dataset)
    _warn_foreign_extensions(scope, dataset)

    key_columns = _key_columns(dataset, scope)
    unique_columns = _unique_columns(dataset, scope)

    columns = []
    for field in dataset.get("fields") or []:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        column = _convert_field(field, scope)
        if column is None:
            continue
        if column["name"] in key_columns:
            column["isKey"] = True
        elif column["name"] in unique_columns:
            column["isUnique"] = True
        columns.append(column)

    table = {"name": name, "columns": columns}
    if dataset.get("description"):
        table["description"] = dataset["description"]

    # rowNumber columns are storage-engine artifacts the import set aside.
    for column in stash.get("excludedColumns") or []:
        columns.insert(0, column)

    partitions = stash.get("partitions")
    if not partitions:
        source = dataset.get("source") or name
        warn(
            scope,
            f"no Power BI partition was preserved for source '{source}'; emitting a "
            "placeholder partition that must be replaced before refresh",
        )
        partitions = [
            {
                "name": name,
                "mode": "import",
                "source": {
                    "type": "m",
                    # `"` is escaped as `""` inside an M string literal.
                    "expression": _tmsl_text(
                        _PARTITION_PLACEHOLDER.format(source=source.replace('"', '""'))
                    ),
                },
            }
        ]
    table["partitions"] = partitions

    for key, value in stash.items():
        if key not in _TABLE_CONTROL_KEYS and key != "partitions":
            table.setdefault(key, value)
    return table


def _key_columns(dataset, scope):
    primary_key = dataset.get("primary_key") or []
    if len(primary_key) > 1:
        # TMSL marks a single column per table with `isKey`; there is no composite form.
        warn(
            scope,
            f"Power BI has no composite key; primary key ({', '.join(primary_key)}) "
            "is not marked on the table",
        )
        return set()
    return set(primary_key)


def _unique_columns(dataset, scope):
    columns = set()
    for unique_key in dataset.get("unique_keys") or []:
        if not isinstance(unique_key, list):
            continue
        if len(unique_key) > 1:
            warn(
                scope,
                f"Power BI has no composite unique constraint; unique key "
                f"({', '.join(unique_key)}) is not marked on the table",
            )
            continue
        columns.update(unique_key)
    return columns


# ---------------------------------------------------------------------------
# Fields -> columns
# ---------------------------------------------------------------------------


def _convert_field(field, dataset_scope):
    name = field["name"]
    scope = f"{dataset_scope} field '{name}'"
    stash = read_stash(field)
    _warn_foreign_extensions(scope, field)

    column = {"name": name}
    expressions = dialect_expressions(field.get("expression"))

    if DIALECT_DAX in expressions:
        column["type"] = "calculated"
        column["expression"] = _tmsl_text(expressions[DIALECT_DAX])
    else:
        source_column = _source_column(expressions, name, scope)
        if source_column is None:
            return None
        column["sourceColumn"] = source_column

    datatype = _column_datatype(field, stash, scope)
    if datatype:
        column["dataType"] = datatype
    if field.get("description"):
        column["description"] = field["description"]
    if field.get("datatype") == "Date" and "formatString" not in stash:
        # Power BI has no date-only data type, so date-only intent is carried by the
        # format string. See `_common.is_date_only_format` for the inverse.
        column["formatString"] = DATE_ONLY_FORMAT

    # Whatever the import could not represent, including a `dataType` the portable
    # vocabulary could not reproduce. `setdefault` so a preserved value -- notably a
    # stale `type` -- can never contradict what the current expression implies.
    for key, value in stash.items():
        if key not in _COLUMN_CONTROL_KEYS:
            column.setdefault(key, value)
    return column


def _column_datatype(field, stash, scope):
    """Resolve a column's TMSL ``dataType``, preferring the core document.

    The import stashes the original TMSL type whenever the portable vocabulary cannot
    reproduce it -- ``binary``, ``variant``, ``automatic``, ``unknown``. That value is
    only replayed while the portable type still agrees with it; once someone edits the
    Apache Ossie ``datatype``, the edit wins and the stashed type is stale.
    """
    datatype = field.get("datatype")
    stashed = stash.get("dataType")
    if stashed is not None and TMSL_TO_OSSIE_DATATYPE.get(stashed) == datatype:
        return stashed
    return _map_datatype(datatype, scope)


def _source_column(expressions, name, scope):
    """Resolve a non-DAX field expression to a TMSL ``sourceColumn``, or None.

    Only a bare identifier can be carried across: it names a column in the table's
    source query, which is exactly what ``sourceColumn`` means. A computed SQL
    expression has no such equivalent and is not rewritten into DAX.
    """
    if not expressions:
        # No expression at all: the field name is the column name by definition.
        return name

    dialect = DIALECT_ANSI if DIALECT_ANSI in expressions else sorted(expressions)[0]
    candidate = expressions[dialect].strip().strip('"').strip("`").strip("[]")
    if IDENTIFIER_RE.match(candidate):
        return candidate
    warn(
        scope,
        f"{dialect} expression is not a plain column reference and is not translated "
        "to DAX; column skipped",
    )
    return None


def _map_datatype(datatype, scope):
    if not datatype:
        return None
    if datatype == "Opaque":
        warn(scope, "'Opaque' has no Power BI equivalent; data type left unspecified")
        return None
    tmsl_type = OSSIE_TO_TMSL_DATATYPE.get(datatype)
    if tmsl_type is None:
        warn(scope, f"unrecognized Apache Ossie data type '{datatype}'; left unspecified")
        return None
    if datatype in _LOSSY_TEMPORAL:
        warn(scope, _LOSSY_TEMPORAL[datatype])
    return tmsl_type


# ---------------------------------------------------------------------------
# Metrics -> measures
# ---------------------------------------------------------------------------


def _apply_measures(tables, metrics):
    """Attach Apache Ossie metrics to their home Power BI table as measures."""
    by_name = {table["name"]: table for table in tables}
    for metric in metrics:
        if not isinstance(metric, dict) or not metric.get("name"):
            continue
        scope = f"metric '{metric['name']}'"
        stash = read_stash(metric)
        _warn_foreign_extensions(scope, metric)

        expressions = dialect_expressions(metric.get("expression"))
        expression = expressions.get(DIALECT_DAX)
        if not expression:
            # A measure is DAX. Mechanically rewriting a SQL aggregate would ignore
            # filter context and produce a measure that is wrong rather than missing.
            available = ", ".join(sorted(expressions)) or "none"
            warn(
                scope,
                f"metric has no DAX expression (dialects: {available}); a Power BI "
                "measure cannot be derived from another dialect, so it is skipped",
            )
            continue

        table = by_name.get(stash.get("table"))
        if table is None:
            if not tables:
                warn(scope, "the model has no table to hold the measure; skipped")
                continue
            table = tables[0]
            warn(
                scope,
                f"no home table recorded; the measure is placed on '{table['name']}'",
            )

        measure = {
            "name": stash.get("name", metric["name"]),
            "expression": _tmsl_text(expression),
        }
        if metric.get("description"):
            measure["description"] = metric["description"]
        if metric.get("datatype") and "dataType" not in stash:
            # A Power BI measure has no writable data type: the engine infers the result
            # type from the DAX. Emitting one would be a property the model does not own.
            warn(
                scope,
                f"Power BI infers a measure's data type from its DAX expression, so "
                f"datatype '{metric['datatype']}' is not applied",
            )
        for key, value in stash.items():
            if key not in _MEASURE_CONTROL_KEYS:
                measure.setdefault(key, value)
        table.setdefault("measures", []).append(measure)


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


def _convert_relationships(relationships, table_columns):
    converted = []
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        scope = f"relationship '{relationship.get('name', '<unnamed>')}'"
        stash = read_stash(relationship)
        _warn_foreign_extensions(scope, relationship)

        from_columns = relationship.get("from_columns") or []
        to_columns = relationship.get("to_columns") or []
        if len(from_columns) != 1 or len(to_columns) != 1:
            # A TMSL relationship joins exactly one column to one column.
            warn(
                scope,
                "Power BI relationships join a single column pair; composite "
                "relationships have no equivalent and are skipped",
            )
            continue

        from_table = relationship.get("from")
        to_table = relationship.get("to")
        from_column, to_column = from_columns[0], to_columns[0]
        if not _endpoints_exist(scope, table_columns, from_table, from_column,
                                to_table, to_column):
            continue

        if stash.get("flipped"):
            # Restore the original orientation the import normalized away.
            from_table, to_table = to_table, from_table
            from_column, to_column = to_column, from_column

        tmsl = {
            "name": stash.get("name", relationship.get("name")),
            "fromTable": from_table,
            "fromColumn": from_column,
            "toTable": to_table,
            "toColumn": to_column,
        }
        for key, value in stash.items():
            if key not in _RELATIONSHIP_CONTROL_KEYS:
                tmsl.setdefault(key, value)
        converted.append(prune(tmsl))
    return converted


def _endpoints_exist(scope, table_columns, from_table, from_column, to_table, to_column):
    for table, column in ((from_table, from_column), (to_table, to_column)):
        if table not in table_columns:
            warn(scope, f"table '{table}' is not in the model; relationship skipped")
            return False
        if column not in table_columns[table]:
            warn(
                scope,
                f"column '{table}'[{column}] is not in the model; relationship skipped",
            )
            return False
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmsl_text(value):
    """Serialize a string the way Power BI writes multi-line TMSL properties.

    TMSL accepts either a plain string or an array of lines; Power BI emits an array
    whenever the value spans multiple lines. Matching that keeps generated files
    diffable against ones written by Power BI itself.
    """
    return value.splitlines() if "\n" in value else value


def _warn_foreign_extensions(scope, obj):
    for ext in foreign_vendor_extensions(obj):
        warn(
            scope,
            f"custom_extensions for vendor '{ext.get('vendor_name')}' have no Power BI "
            "equivalent and are dropped",
        )


__all__ = ["convert_ossie_to_semantic_model", "ConversionError"]
