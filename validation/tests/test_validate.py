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

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

# validate.py exits at import time when its dependencies are missing, which
# would abort the whole pytest session during collection — skip instead.
pytest.importorskip("yaml")
pytest.importorskip("jsonschema")

_VALIDATE_PATH = Path(__file__).parents[1] / "validate.py"
_SPEC = spec_from_file_location("ossie_validate", _VALIDATE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_VALIDATE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATE)

validate_references = _VALIDATE.validate_references


def _document(datasets: list[dict], relationships: list[dict]) -> dict:
    return {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "m",
                "datasets": datasets,
                "relationships": relationships,
            }
        ],
    }


_CUSTOMERS = {
    "name": "customers",
    "source": "db.s.customers",
    "primary_key": ["id"],
    "unique_keys": [["email"]],
}

_ORDERS = {"name": "orders", "source": "db.s.orders"}


def _relationship(to_columns: list[str], to: str = "customers") -> dict:
    return {
        "name": "orders_to_customers",
        "from": "orders",
        "to": to,
        "from_columns": ["customer_id"],
        "to_columns": to_columns,
    }


def test_warns_when_to_columns_does_not_cover_a_declared_key() -> None:
    errors = validate_references(
        _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["region"])])
    )

    assert errors == [
        "[Reference] Warning: Relationship 'orders_to_customers' in model 'm': "
        "to_columns ['region'] does not cover the primary key or a unique key of dataset 'customers'"
    ]


def test_accepts_to_columns_matching_the_primary_key() -> None:
    errors = validate_references(
        _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["id"])])
    )

    assert errors == []


def test_accepts_to_columns_matching_a_unique_key() -> None:
    errors = validate_references(
        _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["email"])])
    )

    assert errors == []


def test_accepts_to_columns_that_is_a_superset_of_a_key() -> None:
    # e.g. tenant-sharded joins carry extra columns on top of the key;
    # coverage still guarantees the many-to-one semantics.
    errors = validate_references(
        _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["tenant_id", "id"])])
    )

    assert errors == []


def test_accepts_composite_key_regardless_of_column_order() -> None:
    composite = {
        "name": "order_lines",
        "source": "db.s.order_lines",
        "primary_key": ["order_id", "line_number"],
    }
    rel = _relationship(to_columns=["line_number", "order_id"], to="order_lines")

    assert validate_references(_document([_ORDERS, composite], [rel])) == []


def test_skips_datasets_that_declare_no_keys() -> None:
    no_keys = {"name": "raw_table", "source": "db.s.raw_table"}
    rel = _relationship(to_columns=["anything"], to="raw_table")

    assert validate_references(_document([_ORDERS, no_keys], [rel])) == []


def test_still_reports_unknown_datasets() -> None:
    errors = validate_references(
        _document([_ORDERS], [_relationship(to_columns=["id"], to="nope")])
    )

    assert errors == [
        "[Reference] Relationship 'orders_to_customers' in model 'm' references unknown dataset 'nope'"
    ]


def test_tolerates_null_unique_keys() -> None:
    # `unique_keys:` present but empty parses to None; the check must not crash.
    dataset = {"name": "customers", "source": "db.s.customers",
               "primary_key": ["id"], "unique_keys": None}
    errors = validate_references(
        _document([_ORDERS, dataset], [_relationship(to_columns=["id"])])
    )

    assert errors == []


def test_skips_non_list_to_columns() -> None:
    # Schema validation reports the shape error; the semantic check must
    # neither crash nor emit a misleading character-set comparison.
    rel = _relationship(to_columns=["id"])
    rel["to_columns"] = "id"

    assert validate_references(_document([_ORDERS, _CUSTOMERS], [rel])) == []


def test_skips_malformed_flat_unique_keys() -> None:
    # unique_keys mistakenly written flat like primary_key: strings are not
    # keys, so with no well-formed key declared the check does not fire.
    dataset = {"name": "customers", "source": "db.s.customers", "unique_keys": ["email"]}
    errors = validate_references(
        _document([_ORDERS, dataset], [_relationship(to_columns=["email"])])
    )

    assert errors == []


# ---------------------------------------------------------------------------
# Metric scoping and metric name uniqueness.
#
# Every test under "reported in review" corresponds to a defect found in review
# of apache/ossie#343 and fails against the validator as it stood before it.
# ---------------------------------------------------------------------------

# The metric-scoping checks no-op without sqlglot, which would let the whole
# scoping section pass without asserting anything. Skip visibly instead.
pytest.importorskip("sqlglot")

validate_metric_scoping = _VALIDATE.validate_metric_scoping
validate_unique_names = _VALIDATE.validate_unique_names
validate_sql = _VALIDATE.validate_sql


def _expr(sql: str, dialect: str = "ANSI_SQL") -> dict:
    return {"dialects": [{"dialect": dialect, "expression": sql}]}


def _field(name: str) -> dict:
    return {"name": name, "expression": _expr(name)}


def _metric(name: str, sql: str, dialect: str = "ANSI_SQL") -> dict:
    return {"name": name, "expression": _expr(sql, dialect)}


def _metric_document(
    dataset_metrics: list[dict] | None = None,
    fields: list[dict] | None = None,
    extra_datasets: list[dict] | None = None,
    model_metrics: list[dict] | None = None,
) -> dict:
    orders = {
        "name": "orders",
        "source": "db.s.orders",
        "fields": fields if fields is not None else [_field("amount")],
        "metrics": dataset_metrics or [],
    }
    return {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "m",
                "datasets": [orders] + (extra_datasets or []),
                "metrics": model_metrics or [],
            }
        ],
    }


_CUSTOMERS_WITH_ID = {
    "name": "customers",
    "source": "db.s.customers",
    "fields": [_field("id")],
}


# --- scoping rules ---------------------------------------------------------


def test_dataset_scoped_metric_accepts_unqualified_column_reference() -> None:
    doc = _metric_document([_metric("total", "SUM(amount)")])

    assert validate_metric_scoping(doc) == []


def test_dataset_scoped_metric_accepts_a_qualified_declared_field() -> None:
    # A qualifier names a declared field, which is how a metric reuses a
    # field's expression rather than repeating it. `amount` is declared here.
    doc = _metric_document([_metric("total", "SUM(orders.amount)")])

    assert validate_metric_scoping(doc) == []


def test_dataset_scoped_metric_rejects_a_qualified_undeclared_name() -> None:
    # `tax` is a column of the source, not a declared field, so the qualified
    # spelling is wrong: it should be written SUM(tax).
    errors = validate_metric_scoping(
        _metric_document([_metric("total", "SUM(orders.tax)")])
    )

    assert any("MUST name a declared field" in error for error in errors)


def test_dataset_scoped_metric_rejects_another_dataset() -> None:
    doc = _metric_document(
        [_metric("bad", "SUM(amount) / COUNT(DISTINCT customers.id)")],
        extra_datasets=[_CUSTOMERS_WITH_ID],
    )
    errors = validate_metric_scoping(doc)

    assert any("references dataset(s) 'customers'" in error for error in errors)


def test_dataset_scope_does_not_limit_aggregation_complexity() -> None:
    # Rule 1 limits what an expression may reach, not its complexity.
    doc = _metric_document(
        [_metric("odd", "SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) / NULLIF(COUNT(*), 0)")]
    )

    assert validate_metric_scoping(doc) == []


# --- reported in review ----------------------------------------------------


def test_uppercase_qualifier_is_not_reported_as_a_missing_dataset() -> None:
    # SQL identifiers are case-insensitive, but the qualifier was compared
    # against the raw YAML name, so SUM(ORDERS.AMOUNT) on dataset `orders` was
    # rejected while naming a dataset that does not exist. Both the qualifier
    # and the field name it qualifies must be matched case-insensitively.
    errors = validate_metric_scoping(
        _metric_document([_metric("total", "SUM(ORDERS.AMOUNT)", "SNOWFLAKE")])
    )

    assert errors == []


def test_struct_path_is_not_reported_as_a_dataset() -> None:
    # sqlglot places the middle element of a three-part path in Column.table,
    # so reading `table` alone reported the struct field as a dataset.
    doc = _metric_document(
        [_metric("total", "SUM(payload.amount)")], fields=[_field("payload")]
    )

    assert validate_metric_scoping(doc) == []


def test_three_part_path_reports_the_dataset_not_the_struct_field() -> None:
    # sqlglot places the middle element of a three-part path in Column.table,
    # so reading `table` alone reported the struct field as a dataset. The
    # qualifier must read as `orders` and the name it qualifies as `payload`,
    # which is undeclared here.
    errors = validate_metric_scoping(
        _metric_document([_metric("total", "SUM(orders.payload.amount)")])
    )

    assert any("'orders.payload'" in error for error in errors)
    assert not any("references dataset(s)" in error for error in errors)


def test_three_part_path_to_a_declared_struct_field_is_valid() -> None:
    doc = _metric_document(
        [_metric("total", "SUM(orders.payload.amount)")], fields=[_field("payload")]
    )

    assert validate_metric_scoping(doc) == []


def test_local_alias_is_not_reported_as_a_dataset() -> None:
    doc = _metric_document([_metric("total", "SUM(o.amount)")])

    assert validate_metric_scoping(doc) == []


def test_subquery_source_is_not_reported_as_a_dataset() -> None:
    doc = _metric_document(
        [_metric("total", "(SELECT SUM(x) FROM other WHERE other.id = amount)")]
    )

    assert validate_metric_scoping(doc) == []


def test_collision_report_order_is_deterministic() -> None:
    # Set iteration order varies between processes under hash randomisation.
    names = ("a", "b", "c", "d")
    doc = _metric_document(
        [_metric(name, "SUM(amount)") for name in names],
        model_metrics=[_metric(name, "SUM(orders.amount)") for name in names],
    )

    assert len({tuple(validate_unique_names(doc)) for _ in range(8)}) == 1


def test_null_expression_does_not_raise() -> None:
    # `.get(key, {})` returns None when the key is present but null, so a
    # truncated hand-edit produced a traceback instead of the schema error.
    doc = {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "m",
                "datasets": [
                    {
                        "name": "orders",
                        "source": "db.s.orders",
                        "metrics": [{"name": "total", "expression": None}],
                    }
                ],
                "metrics": [{"name": "other", "expression": None}],
            }
        ],
    }

    validate_sql(doc)
    validate_metric_scoping(doc)


def test_expression_is_parsed_once_across_checks() -> None:
    _VALIDATE._parse_expression.cache_clear()
    doc = _metric_document([_metric("total", "SUM(amount)")])
    before = _VALIDATE._parse_expression.cache_info().hits

    validate_metric_scoping(doc)
    validate_sql(doc)

    assert _VALIDATE._parse_expression.cache_info().hits > before


# --- metric name rules -----------------------------------------------------


def test_metric_may_not_take_the_name_of_a_field_of_its_dataset() -> None:
    # Both occupy `orders.amount`, so the reference would resolve two ways.
    errors = validate_unique_names(_metric_document([_metric("amount", "SUM(amount)")]))

    assert any("collides with field" in error for error in errors)
    assert not any("Warning:" in error for error in errors)


def test_duplicate_metric_name_within_a_dataset_is_an_error() -> None:
    doc = _metric_document(
        [_metric("total", "SUM(amount)"), _metric("total", "COUNT(*)")]
    )
    errors = validate_unique_names(doc)

    assert any("Duplicate metric name 'total'" in error for error in errors)


def test_two_datasets_may_reuse_a_metric_name() -> None:
    # Dataset-scoped names are scoped to their dataset.
    shipments = {
        "name": "shipments",
        "source": "db.s.shipments",
        "fields": [_field("qty")],
        "metrics": [_metric("item_count", "COUNT(*)")],
    }
    doc = _metric_document(
        [_metric("item_count", "COUNT(*)")], extra_datasets=[shipments]
    )

    assert validate_unique_names(doc) == []


def test_model_metric_shadowing_a_dataset_metric_only_warns() -> None:
    # A dataset may be authored independently and reused across models, so it
    # must not become invalid because of a name the surrounding model adds.
    doc = _metric_document(
        [_metric("total_sales", "SUM(amount)")],
        model_metrics=[_metric("total_sales", "SUM(orders.amount)")],
    )
    errors = validate_unique_names(doc)

    assert errors == [
        "[Unique] Warning: model-scoped metric 'total_sales' in model 'm' shadows "
        "dataset-scoped metric 'orders.total_sales'. Both remain addressable, but "
        "consumers resolving the name 'total_sales' cannot tell which was "
        "intended; consider renaming the model-scoped metric."
    ]


@pytest.mark.parametrize("name", ["amount", "orders"])
def test_model_metric_may_reuse_a_field_or_dataset_name(name: str) -> None:
    # Bare and qualified names never collide, and a model-scoped metric usually
    # takes the name of the column it aggregates, so this is left permitted.
    doc = _metric_document(model_metrics=[_metric(name, "SUM(orders.amount)")])

    assert validate_unique_names(doc) == []


def test_metric_may_reference_an_undeclared_source_column() -> None:
    # A dataset-scoped metric's expression is written against the dataset's
    # source, so a column used only inside a metric need not be declared as a
    # field first. Here 'tax' is not in fields.
    doc = _metric_document(
        [_metric("total_tax", "SUM(tax)")],
        fields=[_field("amount")],
    )

    assert validate_metric_scoping(doc) == []


def test_declared_field_and_source_column_may_share_a_name() -> None:
    # A field `foo` whose expression is not simply `foo` does not shadow the
    # source column `foo`. The two spellings keep them separately reachable:
    # `orders.foo` is the field, bare `foo` is the column.
    shadowing_field = {"name": "foo", "expression": _expr("UPPER(bar)")}
    doc = _metric_document(
        [
            _metric("via_field", "COUNT(DISTINCT orders.foo)"),
            _metric("via_column", "COUNT(DISTINCT foo)"),
        ],
        fields=[shadowing_field],
    )

    assert validate_metric_scoping(doc) == []
