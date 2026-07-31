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

Converts between a Microsoft Power BI / Fabric semantic model — a TMSL `model.bim`
document — and an Apache Ossie (OSI) semantic model, in both directions. The conversion
is a pure offline transform on parsed documents: no connection to Power BI or Fabric is
required, and the package depends only on PyYAML.

## Installation

```bash
cd converters/microsoft
uv sync
```

## Usage

### Command line

```bash
ossie-microsoft import -i model.bim  -o model.yaml   # Power BI -> Apache Ossie
ossie-microsoft export -i model.yaml -o model.bim    # Apache Ossie -> Power BI
```

With no `-o`, the result is written to stdout.

Everything the converter could not carry across faithfully is reported on stderr:

```
$ ossie-microsoft import -i model.bim -o model.yaml
[model] row-level security roles ('roles') has no Apache Ossie counterpart; preserved
in the Power BI stash for round trip, but not represented in the Apache Ossie document
[table 'Sales' measure 'Total Sales'] a KPI ('kpi') has no Apache Ossie counterpart; ...
3 construct(s) could not be converted faithfully; see the messages above.
```

| Flag | Effect |
| --- | --- |
| `--strict` | exit non-zero if anything could not be converted faithfully |
| `-q`, `--quiet` | suppress the report |

`--strict` is the useful mode in a pipeline that must not silently degrade a model. The
output file is still written, so a failing run can be inspected. Both flags are accepted
before or after the subcommand.

### Library

```python
import json

import yaml

from ossie_microsoft import convert_ossie_to_semantic_model, convert_semantic_model_to_ossie

with open("model.bim", encoding="utf-8-sig") as fh:
    ossie_yaml = convert_semantic_model_to_ossie(json.load(fh))

bim = convert_ossie_to_semantic_model(yaml.safe_load(ossie_yaml))
```

Each report is emitted twice, because the two channels answer different questions:

- a `UserWarning`, so `warnings.simplefilter("error")` gives a hard, programmatic
  guarantee that a conversion was lossless;
- a record on the `ossie_microsoft` logger, for applications that consume a log rather
  than installing warning filters.

```python
import logging

logging.getLogger("ossie_microsoft").addHandler(logging.StreamHandler())
```

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
| everything else | `custom_extensions` (vendor `POWER_BI`) |

## Design rule: expressions are never rewritten

Power BI evaluates DAX. Apache Ossie field expressions are usually SQL. The two languages
do not share an evaluation model — DAX aggregates resolve against a filter context that
has no SQL equivalent — so this converter carries expressions across in the dialect they
were authored in and **never machine-translates one into the other**.

Concretely:

- A Power BI measure becomes a metric with a `DAX` dialect expression, not a SQL one.
- On export, a metric becomes a measure only if it carries a `DAX` expression. A metric
  with only a SQL expression is reported and skipped.
- A field expression becomes a TMSL `sourceColumn` only when it is a plain column
  reference, which is exactly what `sourceColumn` means. A computed SQL expression is
  reported and skipped. (A `sourceColumn` the source query spells with spaces, a hyphen
  or a leading digit is preserved and replayed verbatim, so it is not mistaken for a
  computed expression.)

The reason is that a partial rewrite fails silently. An unrecognized function passed
through unchanged, a `CAST` quietly dropped, or an aggregate distributed across a join
all produce a model that loads and returns a number — just the wrong number. A missing
measure is a bug a modeler notices; a plausible wrong one is not.

If you need SQL-to-DAX translation, do it deliberately as a separate step and author the
result into the `DAX` dialect before exporting.

## Losslessness

Nothing is discarded silently. The converter follows the two rules in
[`converters/README.md`](../README.md):

1. **Preserve.** Every TMSL property the Apache Ossie mapping does not consume —
   annotations, partitions, hierarchies, roles, perspectives, cultures, query groups,
   format strings, display folders, KPIs, cross-filter behaviour, relationship
   cardinalities, `rowNumber` columns — is stored in a versioned JSON blob in a
   `POWER_BI` `custom_extensions` entry, alongside excluded tables and skipped
   relationships. This is a deny-list, not an allow-list: a TMSL property this converter
   has never heard of is preserved too, rather than silently dropped. The export
   direction replays it all, so a `model.bim` converted to Apache Ossie and back is the
   same model.

2. **Report.** Anything that genuinely cannot be represented raises a `UserWarning`
   naming the object and the reason. Callers who need a hard guarantee can escalate:

   ```python
   import warnings

   with warnings.catch_warnings():
       warnings.simplefilter("error")
       ossie_yaml = convert_semantic_model_to_ossie(bim)  # raises if anything is lost
   ```

A model that uses no Power BI-specific features converts to clean, vendor-neutral Apache
Ossie with no `custom_extensions` at all.

## Data type notes

TMSL's `dataType` vocabulary is the Tabular Object Model `DataType` enum. It has **no**
date-only, time-only or timezone-aware member: every temporal value is stored as
`dateTime`.

| TMSL | Apache Ossie | Note |
|------|--------------|------|
| `string` | `String` | |
| `int64` | `Integer` | 64-bit in the model, but report visuals are only exact to 2^53 − 1 |
| `decimal` | `Decimal` | "Fixed Decimal Number": exact, 19 digits, 4 after the point |
| `double` | `Float` | "Decimal Number": 64-bit floating point, approximate |
| `boolean` | `Boolean` | |
| `dateTime` | `DateTime`, or `Date` when the format string has no time part | |
| `binary`, `variant` | `Opaque` | no portable equivalent; reported |
| `automatic`, `unknown` | *(omitted)* | the engine has not resolved a type |

Consequences worth knowing:

- **Date-only intent lives in the format string.** A `dateTime` column formatted with
  only date tokens is imported as `Date`; an exported `Date` field gets a date-only
  format string back. Quoted literals and backslash escapes are stripped before tokens
  are detected, so the `h` in `"month" mmm yyyy` is display text, not an hour token. Note
  also that in these VBA-style format strings `m` means *month* unless it directly
  follows an hour token, which is why minutes are written `n`.
- **`Time` and `DateTimeTz` do not survive export.** They collapse onto `dateTime`,
  gaining a date part or losing a UTC offset respectively. Both cases are reported.
- **`double` is not `Decimal`.** Mapping Power BI's approximate "Decimal Number" onto an
  exact decimal type would overstate its precision.
- Values outside years 1900–9999 are outside the Power BI `dateTime` range, and time is
  stored at 1/300 second (about 3.33 ms) granularity.

## Constructs with no equivalent

These are reported rather than approximated, in either direction:

| Construct | Why |
|-----------|-----|
| Inactive relationships | Apache Ossie has no inactive-join concept; keeping one would misrepresent the join graph |
| Many-to-many relationships | Apache Ossie relationships are many-to-one or one-to-one |
| Composite relationships | A TMSL relationship joins exactly one column pair |
| Composite primary/unique keys | TMSL marks a single key column per table |
| `Opaque` fields | Power BI has no untyped data type |
| Metrics without a DAX expression | see [Design rule](#design-rule-expressions-are-never-rewritten) |
| Computed non-DAX field expressions | as above |
| Other vendors' `custom_extensions` | not interpretable by Power BI |

On import, private tables, calculation groups, auto-generated date tables
(`LocalDateTable_*`, `DateTableTemplate_*`) and `rowNumber` columns are left out of the
vendor-neutral model — they are Power BI implementation details — but preserved in the
stash so export restores them. One-to-many relationships are flipped so the many side is
`from`, and the original orientation and cardinalities are recorded.

A data type with no portable equivalent (`binary`, `variant`, `automatic`, `unknown`) is
also kept in the stash, so the export restores the original TMSL type rather than
guessing one from the portable model.

On export, a dataset whose Power BI partition was not preserved gets a placeholder
partition containing an M `error` expression that names the missing source. A refresh
then fails with an actionable message rather than a query that quietly loads nothing.

### Preserved, but not modelled

These Power BI constructs survive a round trip in the stash, so nothing is lost when the
destination is Power BI again. But Apache Ossie has no way to express them, so they are
invisible to any other consumer of the document — and each one is reported on import.

| Level | Construct |
| --- | --- |
| Model | row-level security roles, perspectives, translations/cultures, shared Power Query expressions and parameters, data sources, query groups |
| Table | hierarchies, calculation groups, incremental refresh policies, detail rows definitions |
| Column | date table variations, sort-by-column |
| Measure | KPIs, detail rows definitions |

Purely presentational properties (`isHidden`, `displayFolder`, `formatString`) are
preserved just as faithfully but are not reported: a warning on every cosmetic property
would bury the ones that matter.

In the other direction, Apache Ossie's `ai_context` and `label` are **dropped** rather
than preserved — a TMSL document has nowhere to record them — and are reported as such.

## Testing

```bash
cd converters/microsoft
uv run ruff check .
uv run pytest --cov
```

Coverage is enforced at 95%. The converter's contract is that every lossy branch reports
itself, and an unexercised branch is an unproven report.

## Roadmap

- TMDL as an alternative serialization alongside TMSL `model.bim`.
- Optional, explicitly opt-in SQL-to-DAX translation for the subset of aggregates that
  can be translated soundly, with a hard failure on the rest.
- An end-to-end smoke test that deploys emitted TMSL to an Analysis Services instance,
  so the output is validated by the engine itself and not only by these tests.
