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

"""Tests for what the Palantir converter admits into the model, and why.

Two things decide it. The first is *status*: an export marks each object type,
property and relation as active, experimental, deprecated and so on, and only
some of those belong in a converted model. The defaults here suit an ontology in
production use — active work, plus the endorsed and intermediary entities it
leans on. They are class attributes read through ``allowed_*_statuses()``
methods rather than inline checks, so a caller with a different definition of
"finished" (a draft ontology, say, where nearly everything is still
experimental) subclasses and widens them. These tests pin both the defaults and
the overriding.

The second is *column matching*: an export's two halves routinely disagree on
the case of a physical column name, so a primary key naming ``LOCNO`` has to
find a dataset field called ``locno``. Exact matches still win.

Each test builds the smallest export that shows the behaviour and runs it
through the real parser, so the fixtures stay in the JSON shapes an export
actually uses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import pytest

from ossie_ontology.converter.palantir_to_ossie.converter import PalantirToOssieConverter
from ossie_ontology.external.palantir.model import Status
from ossie_ontology.external.palantir.parser import PalantirParser
from ossie_ontology.model import ConceptMapping, DatasetField, OssieOntology


# ----- export builders --------------------------------------------------

def _prop(
    rid: str,
    id_: str,
    *,
    status: str | None = None,
    column: str | None = None,
    pk_columns: dict[str, str] | None = None,
) -> dict[str, Any]:
    prop: dict[str, Any] = {"rid": rid, "id": id_, "baseType": {"type": "STRING"}}
    if status:
        prop["status"] = {"type": status}
    if column:
        prop["column"] = column
    if pk_columns:
        # Keyed by the backing dataset's rid: a primary key can sit in a
        # differently named column in each dataset that feeds the object type.
        prop["primaryKeyMapping"] = {
            ds_rid: {"columnName": name} for ds_rid, name in pk_columns.items()
        }
    return prop


def _object_type(
    key: str,
    name: str,
    *,
    status: str | None = None,
    properties: Iterable[dict[str, Any]] = (),
    primary_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    """An object type identified by ``<key>_id``, plus any *properties* given.

    Carries no ``datasources`` entry, so the parser falls back to matching a
    dataset whose ``mainDatasetId`` is the object type's own rid — which is what
    :func:`_dataset` builds.
    """
    props = [_prop(f"ri.p.{key}_id", f"{key}_id"), *properties]
    ot: dict[str, Any] = {
        "rid": _ot_rid(key),
        "id": key,
        "displayName": name,
        "properties": props,
        "primaryKeys": list(primary_keys) if primary_keys is not None else [f"{key}_id"],
    }
    if status:
        ot["status"] = {"type": status}
    return ot


def _many_to_one(
    key: str,
    *,
    one: str,
    many: str,
    key_map: dict[str, str],
    status: str | None = None,
) -> dict[str, Any]:
    """A M:1 relation from the *many* object type to the *one* object type.

    ``key_map`` maps a primary-key property rid on the one side to the property
    rid holding it on the many side.
    """
    relation: dict[str, Any] = {
        "rid": f"ri.rel.{key}",
        "id": key,
        "definition": {
            "type": "oneToMany",
            "oneToMany": {
                "objectTypeRidOneSide": _ot_rid(one),
                "objectTypeRidManySide": _ot_rid(many),
                "oneSidePrimaryKeyToManySidePropertyMapping": key_map,
            },
        },
    }
    if status:
        relation["status"] = {"type": status}
    return relation


def _dataset(key: str, columns: Iterable[str]) -> dict[str, Any]:
    """The dataset backing object type *key*, with the columns named."""
    return {
        "mainDatasetId": _ot_rid(key),
        "datasetName": f"{key}_table",
        "datasetSchema": [{"name": column, "type": "STRING"} for column in columns],
    }


def _ot_rid(key: str) -> str:
    return f"ri.ot.{key}"


def _convert(
    tmp_path: Path,
    object_types: Iterable[dict[str, Any]],
    *,
    relations: Iterable[dict[str, Any]] = (),
    datasets: Iterable[dict[str, Any]] = (),
    converter: type[PalantirToOssieConverter] = PalantirToOssieConverter,
) -> OssieOntology:
    """Write an extracted-folder export, parse it, and convert it.

    Both files are rewritten on every call, so a test that converts the same
    export under two different policies can call this twice.
    """
    export = tmp_path / "export"
    (export / "data_sets").mkdir(parents=True, exist_ok=True)
    (export / "ontology.json").write_text(
        json.dumps({"objectTypes": list(object_types), "relations": list(relations)})
    )
    (export / "data_sets" / "ds.json").write_text(json.dumps(list(datasets)))
    return converter().convert(PalantirParser().parse(export))


# ----- assertion helpers ------------------------------------------------

def _concept_names(model: OssieOntology) -> set[str]:
    return {c.name for c in model.ontology.concepts(exclude_builtin=True)}


def _relationship_names(model: OssieOntology) -> set[str]:
    return {r.full_name for r in model.ontology.relationships}


def _concept_mapping(model: OssieOntology, concept_name: str) -> ConceptMapping:
    mappings = [
        cm
        for om in model.ontology_mappings
        for cm in om.concept_mappings
        if cm.concept.name == concept_name
    ]
    assert len(mappings) == 1, f"expected one mapping for '{concept_name}', got {len(mappings)}"
    return mappings[0]


def _mapped_relationship_names(model: OssieOntology, concept_name: str) -> set[str]:
    """Names of the relationships a concept's mapping actually populates."""
    names = set()
    for lm in _concept_mapping(model, concept_name).link_mappings:
        for child in lm.children or []:
            assert child.relationship is not None
            names.add(child.relationship.full_name)
    return names


def _identifier_columns(model: OssieOntology, concept_name: str) -> list[str]:
    """The dataset fields a concept's mapping identifies its instances by."""
    [object_mapping] = _concept_mapping(model, concept_name).object_mappings
    columns = []
    for rm in object_mapping.referent_mappings or []:
        assert isinstance(rm.expression, DatasetField)
        columns.append(rm.expression.name)
    return columns


# ----- object type statuses ---------------------------------------------

def test_default_policy_admits_active_endorsed_and_intermediary(tmp_path: Path):
    model = _convert(tmp_path, [
        _object_type("alpha", "Alpha", status="active"),
        _object_type("bravo", "Bravo", status="endorsed"),
        _object_type("charlie", "Charlie", status="intermediary"),
        _object_type("delta", "Delta", status="experimental"),
        _object_type("echo", "Echo", status="deprecated"),
    ])
    assert _concept_names(model) == {"Alpha", "Bravo", "Charlie"}


def test_object_type_statuses_can_be_widened_by_a_subclass(tmp_path: Path):
    """The extension point a draft-ontology converter is built on."""

    class DraftConverter(PalantirToOssieConverter):
        OBJECT_TYPE_STATUSES = PalantirToOssieConverter.OBJECT_TYPE_STATUSES | {Status.EXPERIMENTAL}

    object_types = [
        _object_type("alpha", "Alpha", status="active"),
        _object_type("delta", "Delta", status="experimental"),
    ]
    assert _concept_names(_convert(tmp_path, object_types)) == {"Alpha"}

    widened = _convert(tmp_path, object_types, converter=DraftConverter)
    assert _concept_names(widened) == {"Alpha", "Delta"}


# ----- property statuses ------------------------------------------------

def _widget_export() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One object type with a property per status, and its backing dataset."""
    object_types = [
        _object_type(
            "widget",
            "Widget",
            status="active",
            properties=[
                _prop("ri.p.label", "label", status="active", column="label"),
                _prop("ri.p.note", "note", status="experimental", column="note"),
                _prop("ri.p.legacy", "legacy", status="deprecated", column="legacy"),
            ],
        )
    ]
    return object_types, [_dataset("widget", ["widget_id", "label", "note", "legacy"])]


def test_experimental_property_is_declared_but_not_mapped(tmp_path: Path):
    """An experimental property is part of the ontology, but nothing populates it.

    The two policies differ on purpose: the relationship is worth declaring
    while the work is in progress, but pointing a mapping at a column that is
    still being reshaped is not.
    """
    object_types, datasets = _widget_export()
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert "Widget.note" in _relationship_names(model)
    assert _mapped_relationship_names(model, "Widget") == {"Widget.label"}


def test_deprecated_property_is_not_converted_at_all(tmp_path: Path):
    object_types, datasets = _widget_export()
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert "Widget.legacy" not in _relationship_names(model)


def test_mapping_property_statuses_can_be_widened_by_a_subclass(tmp_path: Path):
    class DraftConverter(PalantirToOssieConverter):
        MAPPING_PROPERTY_STATUSES = (
            PalantirToOssieConverter.MAPPING_PROPERTY_STATUSES | {Status.EXPERIMENTAL}
        )

    object_types, datasets = _widget_export()
    model = _convert(tmp_path, object_types, datasets=datasets, converter=DraftConverter)

    assert _mapped_relationship_names(model, "Widget") == {"Widget.label", "Widget.note"}


# ----- relation statuses ------------------------------------------------

def _linked_export(
    *, ot_status: str, relation_status: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Two object types and the M:1 relation between them, at the given statuses."""
    object_types = [
        _object_type("widget", "Widget", status=ot_status),
        _object_type(
            "gadget",
            "Gadget",
            status=ot_status,
            properties=[_prop("ri.p.gadget_widget_id", "gadget_widget_id", column="widget_id")],
        ),
    ]
    relations = [
        _many_to_one(
            "gadget_widget",
            one="widget",
            many="gadget",
            key_map={"ri.p.widget_id": "ri.p.gadget_widget_id"},
            status=relation_status,
        )
    ]
    return object_types, relations


def test_relation_statuses_gate_conversion(tmp_path: Path):
    object_types, relations = _linked_export(ot_status="active", relation_status="deprecated")
    model = _convert(tmp_path, object_types, relations=relations)

    assert "Gadget.gadget_widget" not in _relationship_names(model)


def test_experimental_relation_between_active_object_types_is_converted(tmp_path: Path):
    """Experimental relations ride in on their endpoints rather than their own status."""
    object_types, relations = _linked_export(ot_status="active", relation_status="experimental")
    model = _convert(tmp_path, object_types, relations=relations)

    assert "Gadget.gadget_widget" in _relationship_names(model)


def test_experimental_relation_needs_its_endpoints_admitted_too(tmp_path: Path):
    """Widening object types alone leaves their experimental relations behind.

    The endpoint check is separate from the object-type policy, so a converter
    that admits experimental object types still drops every experimental
    relation between them until it widens the endpoint statuses as well.
    """

    class WidenedObjectTypes(PalantirToOssieConverter):
        OBJECT_TYPE_STATUSES = PalantirToOssieConverter.OBJECT_TYPE_STATUSES | {Status.EXPERIMENTAL}

    class WidenedEndpoints(WidenedObjectTypes):
        RELATION_ENDPOINT_STATUSES = (
            PalantirToOssieConverter.RELATION_ENDPOINT_STATUSES | {Status.EXPERIMENTAL}
        )

    object_types, relations = _linked_export(
        ot_status="experimental", relation_status="experimental"
    )

    partial = _convert(tmp_path, object_types, relations=relations, converter=WidenedObjectTypes)
    assert _concept_names(partial) == {"Widget", "Gadget"}
    assert "Gadget.gadget_widget" not in _relationship_names(partial)

    full = _convert(tmp_path, object_types, relations=relations, converter=WidenedEndpoints)
    assert "Gadget.gadget_widget" in _relationship_names(full)


# ----- inheritance ------------------------------------------------------

# Palantir has no subtype construct: inheritance appears as a M:1 relation whose
# property map pairs primary key to primary key, the one side being the subtype.
# Which of those the converter honours is the same status question as everywhere
# else, decided by allowed_subtype_relation_statuses() and the endpoint policy.

def _subtype_export(
    *, ot_status: str, relation_status: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Gadget as a subtype of Widget, at the given statuses."""
    object_types = [
        _object_type("widget", "Widget", status=ot_status),
        _object_type("gadget", "Gadget", status=ot_status),
    ]
    relations = [
        _many_to_one(
            "gadget_widget",
            one="gadget",
            many="widget",
            key_map={"ri.p.gadget_id": "ri.p.widget_id"},
            status=relation_status,
        )
    ]
    return object_types, relations


def _supertype_names(model: OssieOntology, concept_name: str) -> list[str]:
    concept = model.ontology.lookup_concept(concept_name)
    assert concept is not None, f"concept '{concept_name}' was not converted"
    return [parent.name for parent in concept.extends]


def test_primary_key_to_primary_key_relation_becomes_inheritance(tmp_path: Path):
    object_types, relations = _subtype_export(ot_status="active", relation_status="active")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == ["Widget"]


def test_relation_that_does_not_map_primary_keys_is_not_inheritance(tmp_path: Path):
    """The structural half of the rule: a plain foreign key stays a reference."""
    object_types, relations = _linked_export(ot_status="active", relation_status="active")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == []
    assert "Gadget.gadget_widget" in _relationship_names(model)


def test_deprecated_subtype_relation_is_not_read_as_inheritance(tmp_path: Path):
    object_types, relations = _subtype_export(ot_status="active", relation_status="deprecated")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == []


def test_experimental_subtype_relation_between_active_object_types_is_read(tmp_path: Path):
    """Same endpoint rule as any other experimental relation."""
    object_types, relations = _subtype_export(ot_status="active", relation_status="experimental")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == ["Widget"]


def test_subtype_statuses_follow_the_converter_policy(tmp_path: Path):
    """Inheritance keeps step with what the converter admits as a concept.

    Widening object types alone leaves an all-experimental hierarchy flat, for
    the same reason it leaves experimental relations behind: the endpoints are
    checked separately.
    """

    class WidenedObjectTypes(PalantirToOssieConverter):
        OBJECT_TYPE_STATUSES = PalantirToOssieConverter.OBJECT_TYPE_STATUSES | {Status.EXPERIMENTAL}

    class WidenedEndpoints(WidenedObjectTypes):
        RELATION_ENDPOINT_STATUSES = (
            PalantirToOssieConverter.RELATION_ENDPOINT_STATUSES | {Status.EXPERIMENTAL}
        )

    object_types, relations = _subtype_export(
        ot_status="experimental", relation_status="experimental"
    )

    partial = _convert(tmp_path, object_types, relations=relations, converter=WidenedObjectTypes)
    assert _supertype_names(partial, "Gadget") == []

    full = _convert(tmp_path, object_types, relations=relations, converter=WidenedEndpoints)
    assert _supertype_names(full, "Gadget") == ["Widget"]


def test_subtype_relation_statuses_can_be_widened_on_their_own(tmp_path: Path):
    """The narrower default is a policy, not a fact about the export."""

    class IntermediarySubtypes(PalantirToOssieConverter):
        SUBTYPE_RELATION_STATUSES = (
            PalantirToOssieConverter.SUBTYPE_RELATION_STATUSES | {Status.INTERMEDIARY}
        )

    object_types, relations = _subtype_export(ot_status="active", relation_status="intermediary")

    assert _supertype_names(_convert(tmp_path, object_types, relations=relations), "Gadget") == []

    widened = _convert(
        tmp_path, object_types, relations=relations, converter=IntermediarySubtypes
    )
    assert _supertype_names(widened, "Gadget") == ["Widget"]


# ----- primary key column matching --------------------------------------

def _location_export(
    *, pk_column: str, dataset_columns: Iterable[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A Location whose primary key names *pk_column* in its backing dataset."""
    object_types = [
        {
            "rid": _ot_rid("location"),
            "id": "location",
            "displayName": "Location",
            "properties": [
                _prop("ri.p.locno", "locno", pk_columns={_ot_rid("location"): pk_column})
            ],
            "primaryKeys": ["locno"],
        }
    ]
    return object_types, [_dataset("location", dataset_columns)]


def test_primary_key_column_is_matched_case_insensitively(tmp_path: Path):
    """`primaryKeyMapping` names the warehouse column, the dataset the API spelling."""
    object_types, datasets = _location_export(pk_column="LOCNO", dataset_columns=["locno"])
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert _identifier_columns(model, "Location") == ["locno"]


def test_exact_primary_key_column_match_wins(tmp_path: Path):
    """A case-sensitive source that carries both spellings keeps its own."""
    object_types, datasets = _location_export(
        pk_column="LOCNO", dataset_columns=["locno", "LOCNO"]
    )
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert _identifier_columns(model, "Location") == ["LOCNO"]


def test_primary_key_column_absent_from_dataset_warns(tmp_path: Path):
    """A column that is missing outright is still reported, not folded away."""
    object_types, datasets = _location_export(pk_column="MISSING", dataset_columns=["locno"])

    with pytest.warns(UserWarning, match="does not contain a field named 'MISSING'"):
        model = _convert(tmp_path, object_types, datasets=datasets)

    # Nothing identifies the concept, so the mapping is dropped along with it.
    assert not [
        cm for om in model.ontology_mappings for cm in om.concept_mappings
    ]