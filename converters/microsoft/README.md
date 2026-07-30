<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->

# Apache Ossie Microsoft Converter

Converts a Microsoft Power BI / Fabric semantic model — a TMSL `model.bim` document — into
an Apache Ossie (OSI) semantic model. The conversion is a pure offline transform: it reads a
parsed `model.bim` and returns OSI YAML, with no connection to Power BI or Fabric required.

Only the import direction (Power BI -> Ossie) is implemented today. See
[Limitations](#limitations) for what is dropped and
[Roadmap](#roadmap) for the export direction.

## Installation

```bash
cd converters/microsoft
uv sync
```

## Usage

### Library

```python
import json
from ossie_microsoft import convert_semantic_model_to_ossie

with open("model.bim", encoding="utf-8-sig") as fh:
    bim_file = json.load(fh)

ossie_yaml = convert_semantic_model_to_ossie(bim_file)
```

### Command line

```bash
ossie-microsoft import -i model.bim -o model.yaml
```

With no `-o`, the OSI YAML is written to stdout.

## Mapping

| Power BI (TMSL) | Apache Ossie |
|-----------------|--------------|
| `name` / `model.description` | `semantic_model.name` / `.description` |
| `model.tables[]` | `datasets[]` |
| table partition source (`entity`, `m`, `query`, `calculated`) | `dataset.source` |
| `table.columns[]` | `dataset.fields[]` |
| `column.sourceColumn` | field `expression` (`ANSI_SQL` dialect) |
| calculated `column.expression` | field `expression` (`DAX` dialect) |
| `column.dataType` | field `datatype` |
| `column.isKey` / `column.isUnique` | `primary_key` / `unique_keys` |
| temporal `dataType` or `dataCategory: Time` | `field.dimension.is_time` |
| `table.measures[]` | `metrics[]` (`DAX` dialect) |
| `model.relationships[]` | `relationships[]` |

DAX expressions are emitted under the `DAX` dialect rather than translated to SQL, so no
expression semantics are invented during conversion. Consumers that need SQL should
translate the `DAX` dialect expression themselves.

## Limitations

Skipped model objects:

- Private tables (`isPrivate`), calculation groups, and auto-generated date tables
  (`LocalDateTable_*`, `DateTableTemplate_*`).
- `rowNumber` columns, which are storage-engine artifacts.
- Inactive relationships (`isActive: false`) — OSI has no inactive-join concept, so keeping
  one would misrepresent the active join graph.
- Many-to-many relationships — OSI relationships are many-to-one or one-to-one only.
  One-to-many relationships are flipped so the many side becomes `from`.

Dropped Power BI concepts that have no OSI counterpart: format strings, display folders,
perspectives, row-level and object-level security, KPIs, hierarchies, storage and partition
modes, cross-filter direction, translations, calculation items, and annotations.

Power BI data types `automatic`, `unknown`, and `variant` have no portable OSI equivalent and
are left unmapped.

## Roadmap

- Export direction (Ossie -> TMSL/TMDL).
- Preserve dropped Power BI metadata in `custom_extensions` under vendor `POWER_BI` so that a
  round trip is lossless.
- Optional SQL-to-DAX and DAX-to-SQL expression translation.

## Testing

```bash
cd converters/microsoft
uv run pytest
```
