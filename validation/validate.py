#!/usr/bin/env python3
#
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "jsonschema>=4.26.0",
#     "pyyaml>=6.0.3",
#     "sqlglot>=30.12.0",
# ]
# ///

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

"""
Ossie Semantic Model Validator

Validates Ossie YAML files against:
1. JSON Schema (structure, types, enums)
2. Unique names (datasets, fields, metrics, relationships)
3. Valid relationship references
4. Metric scoping (a dataset-scoped metric's expression references its dataset's
   declared fields as dataset.field, and its source's columns unqualified)
5. SQL syntax (using sqlglot)

Usage:
    python validation/validate.py <yaml_file>
    python validation/validate.py <yaml_file> --schema ontology/ontology.json
    python validation/validate.py examples/tpcds_semantic_model.yaml
"""

import json
import sys
from functools import lru_cache
from pathlib import Path

try:
    import yaml
    from jsonschema import Draft202012Validator
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install pyyaml jsonschema")
    sys.exit(1)

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ParseError, TokenError
    SQLGLOT_AVAILABLE = True
except ImportError:
    SQLGLOT_AVAILABLE = False

# Map Ossie dialects to sqlglot dialects
DIALECT_MAP = {
    "ANSI_SQL": None,  # sqlglot default
    "SNOWFLAKE": "snowflake",
    "DATABRICKS": "databricks",
    "BIGQUERY": "bigquery",
    "MDX": None,  # Not supported by sqlglot, skip validation
    "TABLEAU": None,  # Not supported by sqlglot, skip validation
    "MAQL": None,  # Not supported by sqlglot, skip validation
    "THOUGHTSPOT": None,  # Not supported by sqlglot, skip validation
}

# Dialects that sqlglot cannot parse
SKIP_SQL_VALIDATION = {"MDX", "TABLEAU", "MAQL", "THOUGHTSPOT"}


def validate_schema(data: dict, schema: dict) -> list[str]:
    """Validate against JSON Schema."""
    validator = Draft202012Validator(schema)
    errors = []
    for error in validator.iter_errors(data):
        path = " -> ".join(str(p) for p in error.absolute_path) if error.absolute_path else "(root)"
        errors.append(f"[Schema] {path}: {error.message}")
    return errors


def find_duplicates(items: list[str]) -> list[str]:
    """Find duplicate items in a list."""
    seen = set()
    duplicates = []
    for item in items:
        if item in seen:
            duplicates.append(item)
        seen.add(item)
    return duplicates


def validate_unique_names(data: dict) -> list[str]:
    """Validate unique names for datasets, fields, metrics, relationships."""
    errors = []

    for model in data.get("semantic_model", []):
        model_name = model.get("name", "<unnamed>")

        # Check unique dataset names
        dataset_names = [d.get("name") for d in model.get("datasets", []) if d.get("name")]
        for dup in find_duplicates(dataset_names):
            errors.append(f"[Unique] Duplicate dataset name '{dup}' in model '{model_name}'")

        # Check unique field names within each dataset
        for dataset in model.get("datasets", []):
            dataset_name = dataset.get("name", "<unnamed>")
            field_names = [f.get("name") for f in dataset.get("fields", []) if f.get("name")]
            for dup in find_duplicates(field_names):
                errors.append(f"[Unique] Duplicate field name '{dup}' in dataset '{dataset_name}'")

        # Check unique metric names
        metric_names = [m.get("name") for m in model.get("metrics", []) if m.get("name")]
        for dup in find_duplicates(metric_names):
            errors.append(f"[Unique] Duplicate metric name '{dup}' in model '{model_name}'")

        # Check unique dataset-scoped metric names within each dataset, and that
        # they do not shadow a field of the same dataset. A model-scoped metric
        # reusing the name is reported separately, as a warning against the model.
        model_metric_names = set(metric_names)
        for dataset in model.get("datasets", []):
            dataset_name = dataset.get("name", "<unnamed>")
            ds_metric_names = [
                m.get("name") for m in dataset.get("metrics", []) if m.get("name")
            ]
            ds_field_names = {
                f.get("name") for f in dataset.get("fields", []) if f.get("name")
            }
            for dup in find_duplicates(ds_metric_names):
                errors.append(
                    f"[Unique] Duplicate metric name '{dup}' in dataset '{dataset_name}'"
                )
            for name in sorted(set(ds_metric_names) & ds_field_names):
                errors.append(
                    f"[Unique] Dataset-scoped metric '{dataset_name}.{name}' collides with "
                    f"field '{dataset_name}.{name}'; '{dataset_name}.{name}' would be "
                    f"ambiguous between a row-level field and an aggregate"
                )
            for name in sorted(set(ds_metric_names) & model_metric_names):
                # Reported as a warning, and attributed to the model rather than
                # the dataset. A dataset may be authored independently and reused
                # across models, so it must not become invalid because of a name
                # the surrounding model happens to introduce. References stay
                # unambiguous either way: '<name>' is the model-scoped metric and
                # '<dataset>.<name>' is the dataset-scoped one.
                errors.append(
                    f"[Unique] Warning: model-scoped metric '{name}' in model "
                    f"'{model_name}' shadows dataset-scoped metric "
                    f"'{dataset_name}.{name}'. Both remain addressable, but "
                    f"consumers resolving the name '{name}' cannot tell which was "
                    f"intended; consider renaming the model-scoped metric."
                )

        # Check unique relationship names
        rel_names = [r.get("name") for r in model.get("relationships", []) if r.get("name")]
        for dup in find_duplicates(rel_names):
            errors.append(f"[Unique] Duplicate relationship name '{dup}' in model '{model_name}'")

    return errors


def validate_references(data: dict) -> list[str]:
    """Validate that relationships reference existing datasets and that
    to_columns covers a declared key of the 'to' dataset."""
    errors = []

    for model in data.get("semantic_model", []):
        model_name = model.get("name", "<unnamed>")
        datasets = {d.get("name"): d for d in model.get("datasets", []) if d.get("name")}

        for rel in model.get("relationships", []):
            rel_name = rel.get("name", "<unnamed>")
            from_ds = rel.get("from")
            to_ds = rel.get("to")

            if from_ds and from_ds not in datasets:
                errors.append(f"[Reference] Relationship '{rel_name}' in model '{model_name}' references unknown dataset '{from_ds}'")
            if to_ds and to_ds not in datasets:
                errors.append(f"[Reference] Relationship '{rel_name}' in model '{model_name}' references unknown dataset '{to_ds}'")

            # The spec defines to_columns as "Primary/unique key columns in the
            # 'to' dataset". Coverage (superset of a key) still guarantees the
            # many-to-one join, and declared keys may be incomplete since
            # primary_key and unique_keys are optional — so accept any
            # to_columns that covers a declared key, report a warning rather
            # than an error, and skip datasets that declare no keys.
            # Shape guards keep semantic checks from crashing on documents
            # that already fail schema validation.
            dataset = datasets.get(to_ds)
            to_columns = rel.get("to_columns")
            if dataset and isinstance(to_columns, list) and to_columns:
                candidate_keys = [dataset.get("primary_key")] + list(dataset.get("unique_keys") or [])
                declared_keys = [k for k in candidate_keys if isinstance(k, list) and k]
                to_column_set = set(to_columns)
                if declared_keys and not any(set(key) <= to_column_set for key in declared_keys):
                    errors.append(f"[Reference] Warning: Relationship '{rel_name}' in model '{model_name}': to_columns {to_columns} does not cover the primary key or a unique key of dataset '{to_ds}'")

    return errors


@lru_cache(maxsize=2048)
def _parse_expression(expr: str, dialect: str):
    """Parse an expression, trying it bare and then wrapped in SELECT.

    Returns ``(tree, error)``. Exactly one of the two is meaningful:

    * ``(tree, None)``  - parsed successfully.
    * ``(None, message)`` - sqlglot rejected the expression in both forms.
    * ``(None, None)``  - parsing was skipped, either because sqlglot is not
      installed or because the dialect is one sqlglot cannot parse. Callers
      must treat this as "unknown" rather than as a failure.

    Results are cached because several checks run over the same expressions.
    The returned tree is shared between callers and MUST NOT be mutated.
    """
    if not SQLGLOT_AVAILABLE or dialect in SKIP_SQL_VALIDATION:
        return None, None

    sqlglot_dialect = DIALECT_MAP.get(dialect)
    error = None

    for candidate in (expr, f"SELECT {expr}"):
        try:
            tree = sqlglot.parse_one(candidate, dialect=sqlglot_dialect)
        except (ParseError, TokenError) as exc:
            if error is None:
                error = str(exc).split(chr(10))[0]
            continue
        if tree is not None:
            return tree, None

    return None, error


def _qualified_references(tree) -> set[tuple[str, str]]:
    """Every qualified column path in an expression, as (qualifier, name).

    ``orders.amount`` yields ``{("orders", "amount")}``. For a three-part path
    such as ``payload.attrs.value``, sqlglot puts the middle part in
    ``Column.table`` and the first in ``Column.db``, so reading ``table`` alone
    would report ``attrs``. Taking ``parts[0]`` and ``parts[1]`` gives the
    outermost qualifier and the name it qualifies in every case. Unqualified
    columns contribute nothing.
    """
    refs = set()
    for col in tree.find_all(exp.Column):
        parts = [part.name for part in col.parts]
        if len(parts) > 1 and parts[0] and parts[1]:
            refs.add((parts[0], parts[1]))
    return refs


def validate_metric_scoping(data: dict) -> list[str]:
    """Validate the expression rules for dataset-scoped metrics.

    A dataset-scoped metric's expression may reference the declared fields of
    its dataset, written dataset_name.field_name, and the columns of its
    source, written unqualified. Two things are errors: a qualifier naming
    another dataset, since such a metric belongs in semantic_model.metrics, and
    a qualifier naming the declaring dataset followed by a name that is not one
    of its declared fields.

    Whether a bare name is a real column of the source is not checked, because
    that needs catalog metadata the model does not carry. A qualified reference
    is checked, because the field list is in the model.

    This checks the expression only. It says nothing about how the metric may be
    queried: a dataset-scoped metric is joined and grouped like any other, using
    the model's relationships.

    A qualifier that names neither the declaring dataset nor another dataset is
    left alone: it is a local alias, CTE, or subquery source.
    """
    errors = []

    for model in data.get("semantic_model", []):
        model_name = model.get("name", "<unnamed>")

        datasets = model.get("datasets", [])
        all_dataset_names = {
            d["name"].casefold() for d in datasets if d.get("name")
        }

        for dataset in datasets:
            dataset_name = dataset.get("name", "<unnamed>")
            own_name = dataset_name.casefold()
            other_dataset_names = all_dataset_names - {own_name}
            own_field_names = {
                f["name"].casefold()
                for f in dataset.get("fields", [])
                if f.get("name")
            }

            for metric in dataset.get("metrics", []):
                metric_name = metric.get("name", "<unnamed>")
                expression = metric.get("expression") or {}

                for dialect_expr in expression.get("dialects", []):
                    dialect = dialect_expr.get("dialect", "ANSI_SQL")
                    expr = dialect_expr.get("expression", "")
                    if not expr:
                        continue

                    tree, _ = _parse_expression(expr, dialect)
                    if tree is None:
                        continue

                    foreign = set()
                    undeclared = set()

                    for qualifier, referenced in _qualified_references(tree):
                        folded = qualifier.casefold()
                        if folded == own_name:
                            # Qualifying with the declaring dataset's own name
                            # is a reference to one of its declared fields.
                            if referenced.casefold() not in own_field_names:
                                undeclared.add(f"{qualifier}.{referenced}")
                        elif folded in other_dataset_names:
                            foreign.add(qualifier)
                        # Anything else is a struct or variant path, or a local
                        # alias, so it is not a dataset reference and needs no
                        # report.

                    if foreign:
                        errors.append(
                            f"[Scope] Dataset-scoped metric '{dataset_name}.{metric_name}' "
                            f"in model '{model_name}' ({dialect}) references "
                            f"dataset(s) {', '.join(repr(f) for f in sorted(foreign))}. "
                            f"Dataset-scoped metrics aggregate one dataset; a "
                            f"metric spanning datasets is model-scoped and "
                            f"belongs in semantic_model.metrics."
                        )

                    if undeclared:
                        errors.append(
                            f"[Scope] Dataset-scoped metric '{dataset_name}.{metric_name}' "
                            f"in model '{model_name}' ({dialect}) references "
                            f"{', '.join(repr(u) for u in sorted(undeclared))}. "
                            f"A qualified reference MUST name a declared field "
                            f"of dataset '{dataset_name}'; reference a column of "
                            f"its source by unqualified name instead."
                        )

    return errors


def validate_sql_expression(expr: str, dialect: str, context: str) -> str | None:
    """Validate a single SQL expression. Returns error message or None if valid."""
    _, error = _parse_expression(expr, dialect)
    if error:
        return f"[SQL] {context}: {error}"
    return None


def validate_sql(data: dict) -> list[str]:
    """Validate SQL expressions in fields and metrics."""
    # Only semantic model files contain SQL expressions to validate.
    if not data.get("semantic_model"):
        return []

    if not SQLGLOT_AVAILABLE:
        return ["[SQL] Warning: sqlglot not installed, skipping SQL validation. Install with: pip install sqlglot"]

    errors = []

    for model in data.get("semantic_model", []):
        model_name = model.get("name", "<unnamed>")

        # Validate field expressions
        for dataset in model.get("datasets", []):
            dataset_name = dataset.get("name", "<unnamed>")
            for field in dataset.get("fields", []):
                field_name = field.get("name", "<unnamed>")
                expression = field.get("expression") or {}
                for dialect_expr in expression.get("dialects", []):
                    dialect = dialect_expr.get("dialect", "ANSI_SQL")
                    expr = dialect_expr.get("expression", "")
                    if expr:
                        context = f"Field '{dataset_name}.{field_name}' in model '{model_name}' ({dialect})"
                        error = validate_sql_expression(expr, dialect, context)
                        if error:
                            errors.append(error)

            # Validate dataset-scoped metric expressions
            for metric in dataset.get("metrics", []):
                metric_name = metric.get("name", "<unnamed>")
                expression = metric.get("expression") or {}
                for dialect_expr in expression.get("dialects", []):
                    dialect = dialect_expr.get("dialect", "ANSI_SQL")
                    expr = dialect_expr.get("expression", "")
                    if expr:
                        context = f"Metric '{dataset_name}.{metric_name}' in model '{model_name}' ({dialect})"
                        error = validate_sql_expression(expr, dialect, context)
                        if error:
                            errors.append(error)

        # Validate metric expressions
        for metric in model.get("metrics", []):
            metric_name = metric.get("name", "<unnamed>")
            expression = metric.get("expression") or {}
            for dialect_expr in expression.get("dialects", []):
                dialect = dialect_expr.get("dialect", "ANSI_SQL")
                expr = dialect_expr.get("expression", "")
                if expr:
                    context = f"Metric '{metric_name}' in model '{model_name}' ({dialect})"
                    error = validate_sql_expression(expr, dialect, context)
                    if error:
                        errors.append(error)

    return errors


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    args = sys.argv[1:]
    yaml_path = Path(args[0])

    schema_path = Path(__file__).parent.parent / "core-spec" / "ossie-schema.json"
    if len(args) > 1:
        if len(args) == 3 and args[1] == "--schema":
            schema_path = Path(args[2])
        else:
            print("Usage: python validation/validate.py <yaml_file> [--schema <schema_file>]")
            sys.exit(1)

    if not yaml_path.exists():
        print(f"Error: File not found: {yaml_path}")
        sys.exit(1)

    if not schema_path.exists():
        print(f"Error: Schema not found: {schema_path}")
        sys.exit(1)

    # Load files
    with open(schema_path) as f:
        schema = json.load(f)

    with open(yaml_path) as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"Error: Invalid YAML: {e}")
            sys.exit(1)

    # Run validations
    errors = []
    errors.extend(validate_schema(data, schema))

    # Run semantic-model-specific checks only for semantic model payloads.
    if data.get("semantic_model"):
        errors.extend(validate_unique_names(data))
        errors.extend(validate_references(data))
        errors.extend(validate_metric_scoping(data))
        errors.extend(validate_sql(data))

    # Report results
    if errors:
        # Separate warnings from errors
        warnings = [e for e in errors if "Warning:" in e]
        actual_errors = [e for e in errors if "Warning:" not in e]

        for warning in warnings:
            print(f"  {warning}")

        if actual_errors:
            print(f"\nValidation FAILED with {len(actual_errors)} error(s):\n")
            for error in actual_errors:
                print(f"  {error}")
            sys.exit(1)
        else:
            print(f"Validation PASSED: {yaml_path.name}")
            sys.exit(0)
    else:
        print(f"Validation PASSED: {yaml_path.name}")
        sys.exit(0)


if __name__ == "__main__":
    main()
