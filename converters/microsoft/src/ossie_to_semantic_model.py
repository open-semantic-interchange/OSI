"""Convert an Apache Ossie (OSI) semantic model to a Power BI ``model.bim`` (TMSL).

Datasets -> tables, fields -> columns, metrics -> measures and relationships ->
relationships. Tables default to Direct Lake partitions over a OneLake item; OSI
carries no connection details, so the OneLake workspace and item come from the
optional ``source`` argument and fall back to placeholder ids that must be replaced
before the model can load.
"""

import json
import re
import warnings
import yaml
from typing import Optional

COMPATIBILITY_LEVEL = 1702
DEFAULT_CULTURE = "en-US"
DIALECT_DAX = "DAX"

# TMSL has no ai_context field, so it rides along as an annotation.
AI_CONTEXT_ANNOTATION = "OssieAIContext"

# A non-DAX expression is kept in annotations; the column or measure itself needs a
# valid DAX expression to load, so it gets a neutral placeholder.
EXPRESSION_ANNOTATION = "OssieExpression"
DIALECT_ANNOTATION = "OssieExpressionDialect"
PLACEHOLDER_EXPRESSION = "BLANK()"

# Connection details OSI has no field for.
PLACEHOLDER_SERVER = "localhost"
PLACEHOLDER_DATABASE = "database"
PLACEHOLDER_WORKSPACE_ID = "00000000-0000-0000-0000-000000000000"
PLACEHOLDER_ITEM_ID = "00000000-0000-0000-0000-000000000000"
DEFAULT_SCHEMA = "dbo"

# Shared M expression that every Direct Lake partition binds to.
DIRECT_LAKE_EXPRESSION = "DatabaseQuery"
ONELAKE_ENDPOINT = "https://onelake.dfs.fabric.microsoft.com"

# OSI portable data types -> Power BI (TOM) data types. Power BI has no date-only or
# time-only type, so every temporal type lands on dateTime.
_DATATYPES = {
    "String": "string",
    "Integer": "int64",
    "Decimal": "decimal",
    "Float": "double",
    "Boolean": "boolean",
    "Date": "dateTime",
    "Time": "dateTime",
    "DateTime": "dateTime",
    "DateTimeTz": "dateTime",
    "Opaque": "string",
}
DEFAULT_DATATYPE = "string"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$@#]*$")
_DELIMITED_RE = re.compile(r'^\[([^\]]+)\]$|^"([^"]+)"$|^`([^`]+)`$')
_QUERY_START_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def convert_ossie_to_semantic_model(ossie_yaml_str: str, source: Optional[dict] = None) -> dict:
    """Convert an OSI semantic model (YAML text) into a ``model.bim`` document.

    Args:
        ossie_yaml_str: The OSI document as YAML.
        source: Optional OneLake location of the Direct Lake data, as
            ``{"workspaceId": ..., "itemId": ...}``. Placeholder ids are used when omitted.

    Returns:
        The TMSL document, ready to serialize as ``model.bim`` JSON.
    """
    document = yaml.safe_load(ossie_yaml_str)
    if not isinstance(document, dict):
        raise ValueError("Invalid OSI YAML: expected a mapping at the root")

    models = document.get("semantic_model")
    if not isinstance(models, list) or not models:
        raise ValueError("OSI document is missing a non-empty 'semantic_model' list")
    if len(models) > 1:
        raise ValueError(
            "A model.bim holds a single model; convert one 'semantic_model' entry at a time"
        )

    semantic_model = models[0]
    if not isinstance(semantic_model, dict) or not semantic_model.get("name"):
        raise ValueError("The semantic model is missing a 'name'")

    datasets = semantic_model.get("datasets") or []
    if not datasets:
        raise ValueError("The semantic model has no datasets")

    tables = [_convert_dataset(d) for d in datasets]
    _attach_metrics(tables, semantic_model.get("metrics") or [])

    model = {"culture": DEFAULT_CULTURE}
    description = semantic_model.get("description")
    if description:
        model["description"] = description
    model["tables"] = tables
    model["expressions"] = [_direct_lake_expression(source)]

    relationships = _convert_relationships(
        semantic_model.get("relationships") or [], {t["name"] for t in tables}
    )
    if relationships:
        model["relationships"] = relationships
    _apply_ai_context(model, semantic_model.get("ai_context"))

    return {
        "name": semantic_model["name"],
        "compatibilityLevel": COMPATIBILITY_LEVEL,
        "model": model,
    }


def _convert_dataset(dataset) -> dict:
    if not isinstance(dataset, dict) or not dataset.get("name"):
        raise ValueError("Every dataset needs a 'name'")
    if not dataset.get("source"):
        raise ValueError(f"Dataset '{dataset['name']}' is missing a 'source'")

    name = dataset["name"]
    key_columns = set(dataset.get("primary_key") or [])
    unique_columns = set()
    for unique_key in dataset.get("unique_keys") or []:
        if len(unique_key) == 1:
            unique_columns.add(unique_key[0])
        else:
            _warn(name, "composite unique key dropped; Power BI keys are single-column")
    if len(key_columns) > 1:
        _warn(name, "composite primary key: Power BI marks each column as a key on its own")

    table = {"name": name}
    if dataset.get("description"):
        table["description"] = _text(dataset["description"])
    table["columns"] = [
        _convert_field(f, name, key_columns, unique_columns)
        for f in dataset.get("fields") or []
    ]
    table["partitions"] = [_convert_partition(name, dataset["source"])]
    _apply_ai_context(table, dataset.get("ai_context"))
    return table


def _convert_field(field, table_name, key_columns, unique_columns) -> dict:
    if not isinstance(field, dict) or not field.get("name"):
        raise ValueError(f"Every field in dataset '{table_name}' needs a 'name'")

    name = field["name"]
    dialect, expression = _pick_expression(field.get("expression"), f"{table_name}.{name}")

    column = {"name": name}
    if _unquote(expression) == name:
        column["sourceColumn"] = name
    else:
        column["type"] = "calculated"
        column["expression"] = (
            _lines(expression) if dialect == DIALECT_DAX else PLACEHOLDER_EXPRESSION
        )
        column["annotations"] = _expression_annotations(dialect, expression)
    column["dataType"] = _DATATYPES.get(field.get("datatype"), DEFAULT_DATATYPE)
    if field.get("description"):
        column["description"] = _text(field["description"])
    if name in key_columns:
        column["isKey"] = True
    elif name in unique_columns:
        column["isUnique"] = True
    _apply_ai_context(column, field.get("ai_context"))
    return column


def _expression_annotations(dialect, expression) -> list:
    return [
        {"name": DIALECT_ANNOTATION, "value": _text(dialect)},
        {"name": EXPRESSION_ANNOTATION, "value": expression},
    ]


def _convert_partition(table_name, source) -> dict:
    source = _text(source).strip()
    parts = _table_reference_parts(source)
    if parts is None:
        _warn(table_name, "Direct Lake cannot read a query source; using an import partition")
        return {
            "name": table_name,
            "mode": "import",
            "source": {"type": "m", "expression": _lines(_m_expression(source))},
        }

    partition_source = {"type": "entity", "entityName": parts[-1]}
    if len(parts) > 1:
        partition_source["schemaName"] = parts[-2]
    partition_source["expressionSource"] = DIRECT_LAKE_EXPRESSION
    return {"name": table_name, "mode": "directLake", "source": partition_source}


def _direct_lake_expression(source) -> dict:
    """Shared M expression addressing the OneLake item that backs the Direct Lake tables."""
    source = source or {}
    workspace_id = source.get("workspaceId") or PLACEHOLDER_WORKSPACE_ID
    item_id = source.get("itemId") or PLACEHOLDER_ITEM_ID
    if not source.get("workspaceId") or not source.get("itemId"):
        _warn(
            DIRECT_LAKE_EXPRESSION,
            "no workspaceId/itemId given; the Direct Lake source uses placeholder ids",
        )

    url = f"{ONELAKE_ENDPOINT}/{workspace_id}/{item_id}"
    return {
        "name": DIRECT_LAKE_EXPRESSION,
        "kind": "m",
        "expression": [
            "let",
            f"    Source = AzureStorage.DataLake({_m_string(url)})",
            "in",
            "    Source",
        ],
    }


def _m_expression(source) -> str:
    """Build the Power Query for an OSI source: a table reference or a SQL query."""
    source = _text(source).strip()
    parts = _table_reference_parts(source)
    if parts is None:
        query = source.rstrip().rstrip(";")
        return (
            f"let\n"
            f'    Source = Sql.Database({_m_string(PLACEHOLDER_SERVER)}, '
            f"{_m_string(PLACEHOLDER_DATABASE)}, [Query={_m_string(query)}])\n"
            f"in\n"
            f"    Source"
        )

    database, schema, item = _padded_reference(parts)
    return (
        f"let\n"
        f"    Source = Sql.Database({_m_string(PLACEHOLDER_SERVER)}, {_m_string(database)}),\n"
        f"    Navigation = Source{{[Schema={_m_string(schema)}, Item={_m_string(item)}]}}[Data]\n"
        f"in\n"
        f"    Navigation"
    )


def _table_reference_parts(source):
    """Split a qualified table reference into its identifier parts, or None for a query."""
    if _QUERY_START_RE.match(source):
        return None
    parts = [_unquote(p.strip()) for p in _split_qualified_name(source)]
    if not parts or len(parts) > 3 or not all(_IDENTIFIER_RE.match(p) for p in parts):
        return None
    return parts


def _padded_reference(parts):
    parts = list(parts)
    while len(parts) < 3:
        parts.insert(0, DEFAULT_SCHEMA if len(parts) == 1 else PLACEHOLDER_DATABASE)
    return tuple(parts)


def _split_qualified_name(source):
    """Split on dots that are outside [], "" or `` delimiters."""
    parts = []
    current = ""
    closing = None
    for char in source:
        if closing:
            current += char
            if char == closing:
                closing = None
        elif char in ('"', "`", "["):
            current += char
            closing = "]" if char == "[" else char
        elif char == ".":
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return parts


def _attach_metrics(tables, metrics):
    """Measures live on a table in Power BI; home each metric on a dataset it references."""
    table_names = [t["name"] for t in tables]
    by_name = {t["name"]: t for t in tables}
    for metric in metrics:
        if not isinstance(metric, dict) or not metric.get("name"):
            raise ValueError("Every metric needs a 'name'")
        name = metric["name"]
        dialect, expression = _pick_expression(metric.get("expression"), name)

        home = _home_table(expression, table_names)
        if dialect == DIALECT_DAX:
            measure = {"name": name, "expression": _lines(expression)}
        else:
            measure = {
                "name": name,
                "expression": PLACEHOLDER_MEASURE_EXPRESSION,
                "annotations": _expression_annotations(dialect, expression),
            }
        if metric.get("description"):
            measure["description"] = _text(metric["description"])
        _apply_ai_context(measure, metric.get("ai_context"))
        by_name[home].setdefault("measures", []).append(measure)


def _home_table(expression, table_names):
    for table_name in table_names:
        if re.search(rf"(?<!\w){re.escape(table_name)}(?!\w)", expression, re.IGNORECASE):
            return table_name
    return table_names[0]


def _convert_relationships(relationships, table_names) -> list:
    converted = []
    for relationship in relationships:
        if not isinstance(relationship, dict) or not relationship.get("name"):
            raise ValueError("Every relationship needs a 'name'")
        name = relationship["name"]
        from_table = relationship.get("from")
        to_table = relationship.get("to")
        from_columns = relationship.get("from_columns") or []
        to_columns = relationship.get("to_columns") or []
        if not (from_table and to_table and from_columns and to_columns):
            raise ValueError(f"Relationship '{name}' is missing endpoints or columns")
        if len(from_columns) != 1 or len(to_columns) != 1:
            _warn(name, "composite relationship dropped; Power BI joins on a single column")
            continue
        if from_table not in table_names or to_table not in table_names:
            _warn(name, "dropped; it references a dataset that is not in the model")
            continue

        converted_relationship = {
            "name": name,
            "fromTable": from_table,
            "fromColumn": from_columns[0],
            "toTable": to_table,
            "toColumn": to_columns[0],
            "fromCardinality": "many",
            "toCardinality": "one",
        }
        _apply_ai_context(converted_relationship, relationship.get("ai_context"))
        converted.append(converted_relationship)
    return converted


def _apply_ai_context(target, ai_context):
    """An annotation value must be text: a string context is kept verbatim, an object is JSON."""
    if not ai_context:
        return
    value = (
        ai_context
        if isinstance(ai_context, str)
        else json.dumps(ai_context, ensure_ascii=False, sort_keys=True)
    )
    target.setdefault("annotations", []).append(
        {"name": AI_CONTEXT_ANNOTATION, "value": value}
    )


def _pick_expression(expression, owner):
    """Return the (dialect, expression) to use, preferring DAX over other dialects."""
    dialects = (expression or {}).get("dialects") if isinstance(expression, dict) else None
    if not dialects:
        raise ValueError(f"'{owner}' is missing an expression")
    chosen = next((d for d in dialects if d.get("dialect") == DIALECT_DAX), dialects[0])
    text = _text(chosen.get("expression", "")).strip()
    if not text:
        raise ValueError(f"'{owner}' has an empty expression")
    return chosen.get("dialect"), text


def _unquote(value: str) -> str:
    match = _DELIMITED_RE.match(value.strip())
    return next(g for g in match.groups() if g is not None) if match else value.strip()


def _m_string(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _lines(value: str):
    """model.bim stores multi-line expressions as an array of lines."""
    return value.split("\n") if "\n" in value else value


def _text(value) -> str:
    return "\n".join(str(line) for line in value) if isinstance(value, list) else str(value)


def _warn(scope, message):
    warnings.warn(f"[{scope}] {message}")