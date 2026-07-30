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

"""Convert a Power BI semantic model (``model.bim`` / TMSL) to Apache Ossie (OSI).

Only concepts that exist in both formats are populated: model name and
description, tables -> datasets, columns -> fields, measures -> metrics and
relationships -> relationships. Power BI specifics without an OSI counterpart
(format strings, display folders, perspectives, RLS, KPIs, hierarchies,
storage/partition modes, cross-filter direction, ...) are dropped.
"""

import re

import yaml

OSSIE_VERSION = "0.2.0.dev0"
DIALECT_DAX = "DAX"
DIALECT_SQL = "ANSI_SQL"

# Power BI (TOM) data types -> OSI portable data types. Types with no portable
# equivalent ("automatic", "unknown", "variant") are left unmapped.
_DATATYPES = {
    "string": "String",
    "int64": "Integer",
    "decimal": "Decimal",
    "double": "Float",
    "boolean": "Boolean",
    "dateTime": "DateTime",
    "date": "Date",
    "time": "Time",
    "binary": "Opaque",
}

_TEMPORAL_DATATYPES = frozenset({"Date", "Time", "DateTime", "DateTimeTz"})

# Auto-generated date tables Power BI creates behind the scenes for time
# intelligence; they are an implementation detail, not part of the model.
_AUTO_DATE_TABLE_RE = re.compile(r"^(LocalDateTable_|DateTableTemplate_)")

# `Schema="dbo", Item="Sales"` navigation in a Power Query (M) partition.
_M_ITEM_RE = re.compile(r'Item\s*=\s*"([^"]+)"')
_M_SCHEMA_RE = re.compile(r'Schema\s*=\s*"([^"]+)"')
_M_DATABASE_RE = re.compile(r'Sql\.Databases?\s*\(\s*"[^"]*"\s*,\s*"([^"]+)"')


def convert_semantic_model_to_ossie(bim_file: dict) -> str:
    """Convert a parsed ``model.bim`` document into an OSI semantic model.

    Args:
        bim_file: The deserialized contents of a ``model.bim`` file (TMSL).

    Returns:
        The OSI document serialized as YAML.
    """
    return _dump_yaml(_build_document(bim_file))


def _build_document(bim_file: dict) -> dict:
    if not isinstance(bim_file, dict):
        raise TypeError("bim_file must be a parsed model.bim document (dict)")

    model = bim_file.get("model")
    if not isinstance(model, dict):
        raise ValueError("model.bim is missing the required 'model' object")

    tables = [t for t in model.get("tables") or [] if _is_exported_table(t)]
    exported_names = {t["name"] for t in tables}

    semantic_model = {"name": bim_file.get("name") or "semantic_model"}
    description = model.get("description") or bim_file.get("description")
    if description:
        semantic_model["description"] = _text(description)

    semantic_model["datasets"] = [_convert_table(t) for t in tables]

    relationships = _convert_relationships(model.get("relationships") or [], exported_names)
    if relationships:
        semantic_model["relationships"] = relationships

    metrics = _convert_metrics(tables)
    if metrics:
        semantic_model["metrics"] = metrics

    return {"version": OSSIE_VERSION, "semantic_model": [semantic_model]}


def _is_exported_table(table) -> bool:
    if not isinstance(table, dict) or not table.get("name"):
        return False
    if table.get("isPrivate"):
        return False
    # A calculation group is a DAX calculation modifier, not a logical dataset.
    if table.get("calculationGroup"):
        return False
    return not _AUTO_DATE_TABLE_RE.match(table["name"])


def _convert_table(table: dict) -> dict:
    dataset = {"name": table["name"], "source": _table_source(table)}

    primary_key = []
    unique_keys = []
    fields = []
    for column in table.get("columns") or []:
        if not isinstance(column, dict) or not column.get("name"):
            continue
        # A rowNumber column is a storage-engine artifact with no user-visible data.
        if column.get("type") == "rowNumber":
            continue
        fields.append(_convert_column(column))
        if column.get("isKey"):
            primary_key.append(column["name"])
        elif column.get("isUnique"):
            unique_keys.append([column["name"]])

    if primary_key:
        dataset["primary_key"] = primary_key
    if unique_keys:
        dataset["unique_keys"] = unique_keys
    if table.get("description"):
        dataset["description"] = _text(table["description"])
    if fields:
        dataset["fields"] = fields
    return dataset


def _table_source(table: dict) -> str:
    """Best-effort physical source for a table, falling back to its name."""
    for partition in table.get("partitions") or []:
        source = partition.get("source") if isinstance(partition, dict) else None
        if not isinstance(source, dict):
            continue
        source_type = source.get("type")
        if source_type == "query" and source.get("query"):
            return _text(source["query"]).strip()
        if source_type == "entity" and source.get("entityName"):
            schema = source.get("schemaName")
            return f"{schema}.{source['entityName']}" if schema else source["entityName"]
        if source_type in ("m", "calculated") and source.get("expression"):
            expression = _text(source["expression"]).strip()
            if source_type == "calculated":
                return expression
            qualified = _qualified_name_from_m(expression)
            if qualified:
                return qualified
    return table["name"]


def _qualified_name_from_m(expression: str):
    item = _M_ITEM_RE.search(expression)
    if not item:
        return None
    parts = [item.group(1)]
    schema = _M_SCHEMA_RE.search(expression)
    if schema:
        parts.insert(0, schema.group(1))
        database = _M_DATABASE_RE.search(expression)
        if database:
            parts.insert(0, database.group(1))
    return ".".join(parts)


def _convert_column(column: dict) -> dict:
    name = column["name"]
    if column.get("type") == "calculated":
        expression = _expression(_text(column.get("expression", "")).strip(), DIALECT_DAX)
    else:
        expression = _expression(column.get("sourceColumn") or name, DIALECT_SQL)

    field = {"name": name, "expression": expression}
    datatype = _DATATYPES.get(column.get("dataType"))
    if datatype:
        field["datatype"] = datatype
    if column.get("description"):
        field["description"] = _text(column["description"])
    if datatype in _TEMPORAL_DATATYPES or column.get("dataCategory") == "Time":
        field["dimension"] = {"is_time": True}
    return field


def _convert_metrics(tables: list) -> list:
    metrics = []
    seen = set()
    for table in tables:
        for measure in table.get("measures") or []:
            if not isinstance(measure, dict) or not measure.get("name"):
                continue
            expression = _text(measure.get("expression", "")).strip()
            if not expression:
                continue
            # Measure names are unique per model in Power BI; qualify on the rare clash.
            name = measure["name"]
            if name in seen:
                name = f"{table['name']}.{name}"
            seen.add(name)

            metric = {"name": name, "expression": _expression(expression, DIALECT_DAX)}
            if measure.get("description"):
                metric["description"] = _text(measure["description"])
            datatype = _DATATYPES.get(measure.get("dataType"))
            if datatype:
                metric["datatype"] = datatype
            metrics.append(metric)
    return metrics


def _convert_relationships(relationships: list, exported_names: set) -> list:
    converted = []
    seen = set()
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        # OSI has no notion of an inactive relationship; keeping one would make it
        # look like an active join path.
        if relationship.get("isActive") is False:
            continue

        from_table = relationship.get("fromTable")
        from_column = relationship.get("fromColumn")
        to_table = relationship.get("toTable")
        to_column = relationship.get("toColumn")
        if not all((from_table, from_column, to_table, to_column)):
            continue
        if from_table not in exported_names or to_table not in exported_names:
            continue

        from_cardinality = relationship.get("fromCardinality", "many")
        to_cardinality = relationship.get("toCardinality", "one")
        if from_cardinality == "many" and to_cardinality == "many":
            # OSI relationships are many-to-one or one-to-one only.
            continue
        if from_cardinality == "one" and to_cardinality == "many":
            from_table, to_table = to_table, from_table
            from_column, to_column = to_column, from_column

        name = f"{from_table}_{from_column}_to_{to_table}_{to_column}"
        if name in seen:
            continue
        seen.add(name)
        converted.append(
            {
                "name": name,
                "from": from_table,
                "to": to_table,
                "from_columns": [from_column],
                "to_columns": [to_column],
            }
        )
    return converted


class _OssieDumper(yaml.SafeDumper):
    pass


def _represent_str(dumper, data):
    # Multi-line DAX/M/SQL reads far better as a literal block than as an escaped scalar.
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_OssieDumper.add_representer(str, _represent_str)


def _dump_yaml(document: dict) -> str:
    return yaml.dump(
        document,
        Dumper=_OssieDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=float("inf"),
    )


def _expression(expression: str, dialect: str) -> dict:
    return {"dialects": [{"dialect": dialect, "expression": expression}]}


def _text(value) -> str:
    """TMSL allows multi-line strings to be stored as an array of lines."""
    if isinstance(value, list):
        return "\n".join(str(line) for line in value)
    return str(value)