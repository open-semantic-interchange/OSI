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

## Development

```bash
uv sync
uv run pytest
```

The live tests against a real Hologres instance are skipped unless the `HOLOGRES_*`
environment variables are set. See the Development section below once implemented.
