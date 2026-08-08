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

from pathlib import Path

import yaml
from ossie import (
    OSIDataset,
    OSIDialect,
    OSIDialectExpression,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSIMetric,
    OSISemanticModel,
)

from ossie_sigma.converter_issues import ConverterIssueType
from ossie_sigma.osi_to_sigma import OSIToSigmaConverter, _stable_id
from ossie_sigma.sigma_to_osi import SigmaToOSIConverter

from .helpers import load_fixture, normalize

EXAMPLES_DIR = Path(__file__).parent.parent.parent.parent / "examples"


def test_roundtrip_fixture_a_is_byte_identical():
    spec = load_fixture("fixtureA_sigma.json")
    document = SigmaToOSIConverter().convert(spec).output
    reconstructed = OSIToSigmaConverter().convert(document).output
    assert normalize(reconstructed) == normalize(spec)


def test_roundtrip_fixture_b_is_byte_identical():
    spec = load_fixture("fixtureB_sigma.json")
    document = SigmaToOSIConverter().convert(spec).output
    reconstructed = OSIToSigmaConverter().convert(document).output
    assert normalize(reconstructed) == normalize(spec)


def test_foreign_origin_document_synthesizes_valid_spec():
    """An Ossie document never touched by Sigma (no SIGMA custom_extensions) must
    still convert to a structurally valid Sigma spec, with synthesized ids and
    formulas best-effort translated from ANSI SQL."""
    document = OSIDocument.model_validate(
        yaml.safe_load((EXAMPLES_DIR / "tpcds_semantic_model.yaml").read_text())
    )
    result = OSIToSigmaConverter().convert(document)
    spec = result.output

    assert spec["kind"] == "data-model"
    assert spec["pages"]
    element_names = {e["name"] for p in spec["pages"] for e in p["elements"]}
    assert "store_sales" in element_names

    store_sales = next(e for p in spec["pages"] for e in p["elements"] if e["name"] == "store_sales")
    assert all("id" in c and "formula" in c for c in store_sales["columns"])
    # Plain passthrough columns get no explicit `name` (matches Sigma's own convention).
    plain_column = next(c for c in store_sales["columns"] if c["formula"] == "[ss_sold_date_sk]")
    assert "name" not in plain_column

    # Single-dataset metrics are attached to their owning element ...
    assert any(m["name"] == "total_sales" for m in store_sales.get("metrics", []))
    # ... while genuinely cross-dataset metrics are dropped with a recorded issue,
    # not silently discarded and not incorrectly attached to one dataset.
    issue_types = {i.issue_type for i in result.issues}
    assert ConverterIssueType.CROSS_DATASET_METRIC_DROPPED in issue_types


def test_ids_are_deterministic_across_repeated_conversions():
    document = OSIDocument.model_validate(
        yaml.safe_load((EXAMPLES_DIR / "tpcds_semantic_model.yaml").read_text())
    )
    spec_1 = OSIToSigmaConverter().convert(document).output
    spec_2 = OSIToSigmaConverter().convert(document).output
    assert normalize(spec_1) == normalize(spec_2)


def test_synthesized_ids_are_stable_across_processes():
    """uuid5 over a fixed namespace, not hash()/uuid4 — a re-export that minted new ids
    would silently orphan every Sigma object referencing the old ones, so this pins the
    exact values rather than only asserting two in-process runs agree (PYTHONHASHSEED
    randomization would not show up in a same-process comparison)."""
    assert _stable_id("element", "store_sales") == "83ae98f0bda0511baf98cd58fd394974"
    assert _stable_id("column", "store_sales", "ss_sold_date_sk") == "48b667ffa9535d08bcc1d0a48b878a99"
    assert _stable_id("metric", "store_sales", "total_sales") == "9d4cc3056b0c5c698664c4803f12dd72"


def test_untranslatable_expression_omits_the_column_instead_of_faking_a_formula():
    """`formula` is required on every Sigma column and the data model API validates the
    whole document before applying any of it, so a placeholder would fail the entire
    upload rather than degrade one column."""
    document = OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name="m",
                datasets=[
                    OSIDataset(
                        name="orders",
                        source="db.public.orders",
                        fields=[
                            OSIField(
                                name="ok",
                                expression=OSIExpression(
                                    dialects=[OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="amount")]
                                ),
                            ),
                            OSIField(
                                name="untranslatable",
                                expression=OSIExpression(
                                    dialects=[
                                        OSIDialectExpression(
                                            dialect=OSIDialect.ANSI_SQL,
                                            expression="SUM(amount) OVER (PARTITION BY region)",
                                        )
                                    ]
                                ),
                            ),
                            OSIField(
                                name="no_usable_dialect",
                                expression=OSIExpression(
                                    dialects=[OSIDialectExpression(dialect=OSIDialect.MDX, expression="[Measures].[X]")]
                                ),
                            ),
                        ],
                    )
                ],
                metrics=[
                    OSIMetric(
                        name="untranslatable_metric",
                        expression=OSIExpression(
                            dialects=[
                                OSIDialectExpression(
                                    dialect=OSIDialect.ANSI_SQL,
                                    expression="SUM(orders.amount) OVER (PARTITION BY orders.region)",
                                )
                            ]
                        ),
                    )
                ],
            )
        ]
    )

    result = OSIToSigmaConverter().convert(document)
    element = result.output["pages"][0]["elements"][0]

    assert [c["name"] for c in element["columns"]] == ["ok"]
    assert not element.get("metrics")
    assert all(c.get("formula") for c in element["columns"])
    assert (
        sum(1 for i in result.issues if i.issue_type is ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE) == 3
    )


def test_synthesized_spec_carries_a_schema_version():
    """`schemaVersion` is required by the create/update endpoints."""
    document = OSIDocument.model_validate(
        yaml.safe_load((EXAMPLES_DIR / "tpcds_semantic_model.yaml").read_text())
    )
    assert OSIToSigmaConverter().convert(document).output["schemaVersion"] == 1


def test_datatypes_only_ever_emit_the_two_documented_format_kinds():
    document = OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name="m",
                datasets=[
                    OSIDataset(
                        name="t",
                        source="db.public.t",
                        fields=[
                            OSIField(
                                name=datatype.lower(),
                                datatype=datatype,
                                expression=OSIExpression(
                                    dialects=[
                                        OSIDialectExpression(
                                            dialect=OSIDialect.ANSI_SQL, expression=datatype.lower()
                                        )
                                    ]
                                ),
                            )
                            for datatype in (
                                "String",
                                "Integer",
                                "Decimal",
                                "Float",
                                "Boolean",
                                "Date",
                                "Time",
                                "DateTime",
                                "DateTimeTz",
                            )
                        ],
                    )
                ],
            )
        ]
    )

    columns = OSIToSigmaConverter().convert(document).output["pages"][0]["elements"][0]["columns"]
    kinds = {c["formula"].strip("[]"): c["format"]["kind"] for c in columns if "format" in c}
    assert kinds == {
        "integer": "number",
        "decimal": "number",
        "float": "number",
        "date": "date",
        "datetime": "date",
        "datetimetz": "date",
    }
    # String/Boolean/Time have no Sigma display format; emitting an invented `kind`
    # would be rejected by the data model API for the whole document.
    assert {c["formula"].strip("[]") for c in columns if "format" not in c} == {"string", "boolean", "time"}
