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

"""Tests for the Apache Ossie -> Power BI (TMSL ``model.bim``) converter."""

import json
import warnings
from pathlib import Path

import pytest
import yaml

from ossie_microsoft import convert_ossie_to_semantic_model, convert_semantic_model_to_ossie
from ossie_microsoft._common import OSSIE_VERSION, make_expression, write_stash

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def bim_out(model):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return convert_ossie_to_semantic_model(
            {"version": OSSIE_VERSION, "semantic_model": [model]}
        )


def _table(bim, name):
    return next(t for t in bim["model"]["tables"] if t["name"] == name)


def _column(table, name):
    return next(c for c in table["columns"] if c["name"] == name)


def _convert(semantic_model):
    return convert_ossie_to_semantic_model(
        {"version": OSSIE_VERSION, "semantic_model": [semantic_model]}
    )


def _minimal(**overrides):
    semantic_model = {
        "name": "m",
        "datasets": [
            {
                "name": "T",
                "source": "dbo.t",
                "fields": [
                    {
                        "name": "C",
                        "datatype": "String",
                        "expression": make_expression("c", "ANSI_SQL"),
                    }
                ],
            }
        ],
    }
    semantic_model.update(overrides)
    return semantic_model


# --- input handling --------------------------------------------------------


def test_a_non_document_is_rejected():
    with pytest.raises(TypeError):
        convert_ossie_to_semantic_model("not a document")


def test_a_document_without_a_model_is_rejected():
    with pytest.raises(ValueError):
        convert_ossie_to_semantic_model({"version": OSSIE_VERSION})


def test_a_foreign_spec_version_warns():
    with pytest.warns(UserWarning, match="targets Apache Ossie spec"):
        convert_ossie_to_semantic_model(
            {"version": "9.9.9", "semantic_model": [_minimal()]}
        )


def test_only_the_first_model_is_converted():
    document = {"version": OSSIE_VERSION, "semantic_model": [_minimal(), _minimal()]}
    with pytest.warns(UserWarning, match="single model"):
        bim = convert_ossie_to_semantic_model(document)
    assert len(bim["model"]["tables"]) == 1


# --- structure -------------------------------------------------------------


def test_the_model_header_is_restored_from_the_stash(bim_out):
    assert bim_out["name"] == "sales_model"
    assert bim_out["compatibilityLevel"] == 1550
    assert bim_out["model"]["culture"] == "en-US"
    assert bim_out["model"]["description"] == "Retail sales semantic model"


def test_a_model_without_a_stash_gets_documented_defaults():
    bim = _convert(_minimal())
    assert bim["compatibilityLevel"] == 1550
    assert bim["model"]["culture"] == "en-US"


def test_datasets_become_tables(bim_out):
    assert {t["name"] for t in bim_out["model"]["tables"]} >= {
        "Sales",
        "Customer",
        "Calendar",
    }


def test_excluded_tables_are_restored_verbatim(bim_out, bim):
    original = next(t for t in bim["model"]["tables"] if t["name"] == "Internal Staging")
    assert _table(bim_out, "Internal Staging") == original


def test_a_primary_key_column_is_marked(bim_out):
    assert _column(_table(bim_out, "Sales"), "SalesKey")["isKey"] is True


def test_a_composite_primary_key_is_reported_as_unsupported():
    semantic_model = _minimal()
    semantic_model["datasets"][0]["primary_key"] = ["A", "B"]
    with pytest.warns(UserWarning, match="no composite key"):
        bim = _convert(semantic_model)
    assert "isKey" not in _column(_table(bim, "T"), "C")


# --- columns ---------------------------------------------------------------


def test_a_plain_expression_becomes_a_source_column(bim_out):
    assert _column(_table(bim_out, "Sales"), "Amount")["sourceColumn"] == "amount"


def test_a_dax_expression_becomes_a_calculated_column(bim_out):
    column = _column(_table(bim_out, "Sales"), "AmountWithTax")
    assert column["type"] == "calculated"
    assert column["expression"] == "Sales[Amount] * 1.2"


def test_a_computed_sql_expression_is_skipped_rather_than_translated():
    # Rewriting SQL into DAX would produce a column that is wrong, not one that is
    # missing, so the converter refuses.
    semantic_model = _minimal()
    semantic_model["datasets"][0]["fields"][0]["expression"] = make_expression(
        "SUM(amount) / COUNT(*)", "ANSI_SQL"
    )
    with pytest.warns(UserWarning, match="not a plain column reference"):
        bim = _convert(semantic_model)
    assert _table(bim, "T")["columns"] == []


def test_a_date_field_carries_a_date_only_format_string():
    semantic_model = _minimal()
    semantic_model["datasets"][0]["fields"][0]["datatype"] = "Date"
    column = _column(_table(_convert(semantic_model), "T"), "C")
    # Power BI has no date-only data type; the format string carries the intent.
    assert column["dataType"] == "dateTime"
    assert column["formatString"] == "yyyy-mm-dd"


@pytest.mark.parametrize(
    "datatype,expected", [("Time", "time-only"), ("DateTimeTz", "timezone-aware")]
)
def test_temporal_types_power_bi_lacks_are_reported(datatype, expected):
    semantic_model = _minimal()
    semantic_model["datasets"][0]["fields"][0]["datatype"] = datatype
    with pytest.warns(UserWarning, match=expected):
        bim = _convert(semantic_model)
    assert _column(_table(bim, "T"), "C")["dataType"] == "dateTime"


def test_an_opaque_field_leaves_the_data_type_unspecified():
    semantic_model = _minimal()
    semantic_model["datasets"][0]["fields"][0]["datatype"] = "Opaque"
    with pytest.warns(UserWarning, match="'Opaque' has no Power BI equivalent"):
        bim = _convert(semantic_model)
    assert "dataType" not in _column(_table(bim, "T"), "C")


# --- partitions ------------------------------------------------------------


def test_a_preserved_partition_is_replayed(bim_out):
    partition = _table(bim_out, "Sales")["partitions"][0]
    assert partition["source"]["type"] == "m"
    assert "Sql.Database" in "\n".join(partition["source"]["expression"])


def test_a_dataset_without_a_partition_gets_a_failing_placeholder():
    with pytest.warns(UserWarning, match="placeholder partition"):
        bim = _convert(_minimal())
    expression = "\n".join(_table(bim, "T")["partitions"][0]["source"]["expression"])
    # An `error` expression fails the refresh loudly instead of loading nothing.
    assert expression.startswith("let")
    assert "error" in expression
    assert "dbo.t" in expression


# --- measures --------------------------------------------------------------


def test_a_metric_returns_to_its_home_table(bim_out):
    measures = {m["name"]: m for m in _table(bim_out, "Sales")["measures"]}
    assert measures["Total Sales"]["expression"] == "SUM ( Sales[Amount] )"
    assert measures["Total Sales"]["formatString"] == "\\$#,0.00"


def test_a_metric_without_a_dax_expression_is_skipped():
    semantic_model = _minimal(
        metrics=[
            {
                "name": "Revenue",
                "expression": make_expression("SUM(amount)", "ANSI_SQL"),
            }
        ]
    )
    with pytest.warns(UserWarning, match="cannot be derived from another dialect"):
        bim = _convert(semantic_model)
    assert "measures" not in _table(bim, "T")


def test_a_metric_without_a_home_table_lands_on_the_first_table():
    semantic_model = _minimal(
        metrics=[{"name": "Rows", "expression": make_expression("COUNTROWS(T)", "DAX")}]
    )
    with pytest.warns(UserWarning, match="no home table recorded"):
        bim = _convert(semantic_model)
    assert _table(bim, "T")["measures"][0]["name"] == "Rows"


# --- relationships ---------------------------------------------------------


def test_relationships_are_restored_with_their_original_orientation(bim_out, bim):
    by_name = {r["name"]: r for r in bim_out["model"]["relationships"]}
    original = {r["name"]: r for r in bim["model"]["relationships"]}
    # "e5f6a7b8" is authored one-to-many; the import normalizes it to many-to-one and
    # the export must put it back the way Power BI wrote it.
    assert by_name["e5f6a7b8"] == original["e5f6a7b8"]
    assert by_name["a1b2c3d4"] == original["a1b2c3d4"]


def test_a_composite_relationship_is_reported_as_unsupported():
    semantic_model = _minimal(
        relationships=[
            {
                "name": "r",
                "from": "T",
                "to": "T",
                "from_columns": ["A", "B"],
                "to_columns": ["A", "B"],
            }
        ]
    )
    with pytest.warns(UserWarning, match="single column pair"):
        bim = _convert(semantic_model)
    assert "relationships" not in bim["model"]


def test_a_relationship_to_a_missing_column_is_skipped():
    semantic_model = _minimal(
        relationships=[
            {
                "name": "r",
                "from": "T",
                "to": "T",
                "from_columns": ["C"],
                "to_columns": ["Nope"],
            }
        ]
    )
    with pytest.warns(UserWarning, match="is not in the model"):
        bim = _convert(semantic_model)
    assert "relationships" not in bim["model"]


# --- other vendors ---------------------------------------------------------


def test_another_vendors_extensions_are_reported_as_dropped():
    semantic_model = _minimal()
    write_stash_for_other_vendor(semantic_model["datasets"][0])
    with pytest.warns(UserWarning, match="vendor 'DATABRICKS'"):
        _convert(semantic_model)


def write_stash_for_other_vendor(obj):
    obj.setdefault("custom_extensions", []).append(
        {"vendor_name": "DATABRICKS", "data": "{}"}
    )


# --- round trip ------------------------------------------------------------


def test_a_model_survives_a_round_trip(bim, bim_out):
    """A ``model.bim`` converted to Apache Ossie and back is the same model."""
    original = {t["name"]: t for t in bim["model"]["tables"]}
    result = {t["name"]: t for t in bim_out["model"]["tables"]}
    assert set(original) == set(result)

    for name, table in original.items():
        expected = _normalize(table)
        # A rowNumber column is a storage-engine artifact, not user data.
        expected["columns"] = [c for c in expected["columns"] if c.get("type") != "rowNumber"]
        assert expected == _normalize(result[name]), name

    assert _normalize(bim["model"]["relationships"]) == _normalize(
        bim_out["model"]["relationships"]
    )


def _normalize(node):
    """Compare TMSL structurally, ignoring key order and the string/array text form."""
    if isinstance(node, dict):
        return {key: _normalize(value) for key, value in sorted(node.items())}
    if isinstance(node, list):
        if node and all(isinstance(item, str) for item in node):
            return "\n".join(node)
        return [_normalize(item) for item in node]
    return node


def test_cli_round_trip(tmp_path):
    from ossie_microsoft.cli import main

    osi_path = tmp_path / "model.yaml"
    bim_path = tmp_path / "model.bim"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert main(["import", "-i", str(FIXTURES / "sales_model.bim"), "-o", str(osi_path)]) == 0
        assert main(["export", "-i", str(osi_path), "-o", str(bim_path)]) == 0

    written = json.loads(bim_path.read_text(encoding="utf-8"))
    assert written["name"] == "sales_model"
    assert bim_path.read_text(encoding="utf-8").endswith("\n")


def test_cli_export_reports_errors_without_traceback(tmp_path, capsys):
    from ossie_microsoft.cli import main

    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 0.2.0.dev0\n", encoding="utf-8")
    assert main(["export", "-i", str(bad)]) == 1
    assert "Error:" in capsys.readouterr().err


def test_stash_data_that_is_not_json_is_rejected():
    from ossie_microsoft import ConversionError

    semantic_model = _minimal()
    semantic_model["custom_extensions"] = [{"vendor_name": "POWER_BI", "data": "{oops"}]
    with pytest.raises(ConversionError):
        _convert(semantic_model)


def test_a_written_stash_round_trips():
    obj = {}
    write_stash(obj, {"a": 1})
    assert yaml.safe_load(json.dumps(obj))["custom_extensions"][0]["vendor_name"] == "POWER_BI"


def test_import_export_is_stable_across_two_passes(bim):
    """Converting twice produces the same model, so the pipeline has no drift."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        first = convert_ossie_to_semantic_model(
            yaml.safe_load(convert_semantic_model_to_ossie(bim))
        )
        second = convert_ossie_to_semantic_model(
            yaml.safe_load(convert_semantic_model_to_ossie(first))
        )
    assert first == second
