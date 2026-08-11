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

# Apache Ossie Hologres Converter

Converts between Apache Ossie semantic models and [Alibaba Cloud Hologres](https://www.alibabacloud.com/product/hologres)
Semantic Views, available in Hologres V5.0.0 and later.

The two directions are deliberately asymmetric, because Hologres publishes and consumes
its Semantic View definitions in different formats:

- **Export (Ossie -> Hologres)** produces `CREATE SEMANTIC VIEW` **SQL DDL text**.
  Hologres has no YAML import function, so the DDL is the only way to create a
  Semantic View.
- **Import (Hologres -> Ossie)** consumes the **`model_yaml`** that Hologres publishes
  for every Semantic View in the `hologres.hg_semantic_view_properties` system table.

## Installation

```bash
uv sync
```

## Usage

### Command line

Export an Apache Ossie model to DDL and run it:

```bash
ossie-hologres export -i model.yaml -o view.sql
psql -h <endpoint> -p 80 -U <user> -d <db> -f view.sql
```

Import an existing Semantic View back into Apache Ossie. Hologres publishes the
structured model for every Semantic View in a system table:

```bash
psql -h <endpoint> -p 80 -U <user> -d <db> -At -c \
  "SELECT property_value FROM hologres.hg_semantic_view_properties
   WHERE schema_name = current_schema()
     AND view_name = 'sales_sv' AND property_key = 'model_yaml';" > model_yaml.yaml

ossie-hologres import -i model_yaml.yaml -o model.yaml
```

Export options:

| Option | Purpose |
|--------|---------|
| `--schema` | Schema for the view, and a default for datasets whose `source` has none. Never overrides a schema already written into a `source`. |
| `--database` | Assert the database the dataset sources belong to. |
| `--drop-if-exists` | Prefix a `DROP SEMANTIC VIEW IF EXISTS`. Hologres has no `CREATE OR REPLACE` or `ALTER`, so this is how a definition is changed. |
| `--metric-owner METRIC=DATASET` | Name the table a metric belongs to, for metrics whose expression has no qualified column to infer it from (`COUNT(*)`). Repeatable. |
| `--skip-unsupported-metrics` | Warn about and skip metrics with no Semantic View form instead of failing. |

### Python API

```python
from ossie_hologres import convert_ossie_to_semantic_view, convert_semantic_view_to_ossie

ddl = convert_ossie_to_semantic_view(ossie_yaml, schema="public")
ossie_yaml = convert_semantic_view_to_ossie(model_yaml)
```

## Development

```bash
uv sync
uv run pytest
```

### Live tests

A `CREATE SEMANTIC VIEW` statement can only really be validated by a Hologres server, so
the suite includes end-to-end tests that create a view, query it, and read the model back.
They are skipped unless the connection environment variables are set, which keeps CI and a
plain `uv run pytest` hermetic:

```bash
export HOLOGRES_HOST=<endpoint>
export HOLOGRES_PORT=80
export HOLOGRES_USER='BASIC$account'   # single quotes: the $ is literal
export HOLOGRES_PASSWORD='<password>'
export HOLOGRES_DB=<database>

uv sync --group live
uv run pytest -m live -v
```

The tests create everything inside an `ossie_hologres_it` schema and drop it afterwards.
Credentials are read only from the environment; none are stored in this repository.

The `live` dependency group holds the PostgreSQL driver and is excluded from
`default-groups`, so CI never installs a database driver it cannot use.
