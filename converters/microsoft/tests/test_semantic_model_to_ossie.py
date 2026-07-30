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

"""Tests for the Power BI (TMSL ``model.bim``) -> Apache Ossie converter."""

import json
from pathlib import Path

import pytest
import yaml

from ossie_microsoft import convert_semantic_model_to_ossie

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def bim():
    with open(FIXTURES / "sales_model.bim", encoding="utf-8-sig") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def osi(bim):
    return yaml.safe_load(convert_semantic_model_to_ossie(bim))


@pytest.fixture(scope="module")
def model(osi):
    return osi["semantic_model"][0]


def _dataset(model, name):
    return next(d for d in model["datasets"] if d["name"] == name)


def _field(dataset, name):
    return next(f for f in dataset["fields"] if f["name"] == name)


def _metric(model, name):
    return next(m for m in model["metrics"] if m["name"] == name)


def _expression(node, dialect):
    return next(d["expression"] for d in node["expression"]["dialects"] if d["dialect"] == dialect)


# --- public API ------------------------------------------------------------


def test_package_exports_the_converters():
    import ossie_microsoft

    assert ossie_microsoft.__all__ == [
        "ConversionError",
        "build_ossie_document",
        "convert_ossie_to_semantic_model",
        "convert_semantic_model_to_ossie",
    ]
    assert callable(ossie_microsoft.convert_semantic_model_to_ossie)
    assert callable(ossie_microsoft.convert_ossie_to_semantic_model)


def test_cli_writes_ossie_yaml(tmp_path):
    from ossie_microsoft.cli import main

    out = tmp_path / "model.yaml"
    assert main(["import", "-i", str(FIXTURES / "sales_model.bim"), "-o", str(out)]) == 0
    assert yaml.safe_load(out.read_text(encoding="utf-8"))["semantic_model"][0]["name"] == "sales_model"


def test_cli_reports_errors_without_traceback(tmp_path, capsys):
    from ossie_microsoft.cli import main

    bad = tmp_path / "bad.bim"
    bad.write_text('{"not_a_model": true}', encoding="utf-8")
    assert main(["import", "-i", str(bad)]) == 1
    assert "Error:" in capsys.readouterr().err


# --- document shape --------------------------------------------------------


def test_document_header(osi):
    assert osi["version"] == "0.2.0.dev0"
    assert len(osi["semantic_model"]) == 1


def test_model_name_and_description(model):
    assert model["name"] == "sales_model"
    assert model["description"] == "Retail sales semantic model"


def test_rejects_non_dict_input():
    with pytest.raises(TypeError):
        convert_semantic_model_to_ossie("not a dict")


def test_rejects_document_without_model():
    with pytest.raises(ValueError):
        convert_semantic_model_to_ossie({"name": "x"})


# --- datasets --------------------------------------------------------------


def test_exports_only_user_facing_tables(model):
    assert [d["name"] for d in model["datasets"]] == ["Sales", "Customer", "Calendar"]


def test_row_number_column_is_skipped(model):
    assert "RowNumber" not in [f["name"] for f in _dataset(model, "Sales")["fields"]]


def test_keys_are_mapped(model):
    assert _dataset(model, "Sales")["primary_key"] == ["SalesKey"]
    customer = _dataset(model, "Customer")
    assert customer["primary_key"] == ["CustomerKey"]
    assert customer["unique_keys"] == [["Email"]]


@pytest.mark.parametrize(
    "dataset_name,expected_source",
    [
        ("Sales", "retail.dbo.sales"),
        ("Customer", "dbo.customer"),
        ("Calendar", "SELECT * FROM dbo.calendar"),
    ],
)
def test_partition_sources(model, dataset_name, expected_source):
    assert _dataset(model, dataset_name)["source"] == expected_source


# --- fields ----------------------------------------------------------------


def test_plain_column_uses_source_column_as_sql(model):
    amount = _field(_dataset(model, "Sales"), "Amount")
    assert _expression(amount, "ANSI_SQL") == "amount"
    assert amount["datatype"] == "Decimal"
    assert amount["description"] == "Extended sales amount"


def test_calculated_column_uses_dax_dialect(model):
    field = _field(_dataset(model, "Sales"), "AmountWithTax")
    assert _expression(field, "DAX") == "Sales[Amount] * 1.2"


def test_temporal_column_is_marked_as_time(model):
    assert _field(_dataset(model, "Sales"), "OrderDate")["dimension"] == {"is_time": True}


def test_non_temporal_time_data_category_is_marked_as_time(model):
    fiscal_year = _field(_dataset(model, "Calendar"), "FiscalYear")
    assert fiscal_year["datatype"] == "Integer"
    assert fiscal_year["dimension"] == {"is_time": True}


def test_plain_column_has_no_dimension_marker(model):
    assert "dimension" not in _field(_dataset(model, "Customer"), "Country")


# --- metrics ---------------------------------------------------------------


def test_measures_become_dax_metrics(model):
    assert [m["name"] for m in model["metrics"]] == ["Total Sales", "Order Count"]
    total = _metric(model, "Total Sales")
    assert _expression(total, "DAX") == "SUM ( Sales[Amount] )"
    assert total["datatype"] == "Decimal"
    assert total["description"] == "Sum of sales amount"


def test_multi_line_measure_expression_is_joined(model):
    assert _expression(_metric(model, "Order Count"), "DAX") == "COUNTROWS (\n    Sales\n)"


def test_multi_line_expressions_use_yaml_literal_blocks(bim):
    assert "COUNTROWS (\n" in convert_semantic_model_to_ossie(bim)


# --- relationships ---------------------------------------------------------


def test_active_many_to_one_relationship(model):
    rel = next(r for r in model["relationships"] if r["from"] == "Sales" and r["to"] == "Customer")
    assert rel["from_columns"] == ["CustomerKey"]
    assert rel["to_columns"] == ["CustomerKey"]


def test_one_to_many_relationship_is_flipped(model):
    rel = next(r for r in model["relationships"] if r["to"] == "Calendar")
    assert rel["from"] == "Sales"
    assert rel["from_columns"] == ["OrderDate"]
    assert rel["to_columns"] == ["Date"]


def test_inactive_many_to_many_and_dangling_relationships_are_dropped(model):
    assert len(model["relationships"]) == 2
    assert all("Internal Staging" not in (r["from"], r["to"]) for r in model["relationships"])


# --- spec conformance ------------------------------------------------------


def test_output_validates_against_core_spec_schema(osi):
    jsonschema = pytest.importorskip("jsonschema")

    with open(REPO_ROOT / "core-spec" / "osi-schema.json", encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.validate(osi, schema)


def test_dax_is_a_spec_dialect():
    with open(REPO_ROOT / "core-spec" / "osi-schema.json", encoding="utf-8") as fh:
        schema = json.load(fh)
    assert "DAX" in schema["$defs"]["Dialect"]["enum"]
