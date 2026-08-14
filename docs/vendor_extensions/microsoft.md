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

# `MICROSOFT` vendor extension

This document defines what belongs in `custom_extensions` entries whose
`vendor_name` is `MICROSOFT`. It is a convention, not a normative part of the core
specification: `vendor_name` is a free-form string and this token is registered as a
well-known example in [`core-spec/spec.md`](../../core-spec/spec.md#vendor-names).

Everything below is reconstructible from public documentation. See
[References](#references).

## Why the token is `MICROSOFT` and not `POWER_BI`

One object model — the Tabular model, scripted as **TMSL** (JSON, `model.bim`),
authored as **TMDL** (text), and manipulated through the **Tabular Object Model
(TOM)** — is the storage format for all of:

- Power BI semantic models (Desktop and Service)
- Microsoft Fabric semantic models
- Azure Analysis Services (tabular)
- SQL Server Analysis Services (tabular mode)

A `model.bim` does not record which of these produced it, and a converter reading one
cannot tell. A token named after a single product would therefore mislabel the other
three. `MICROSOFT` is the only candidate token that is true for every input.

This matches how Ossie already registers vendors: tokens name the **organization**
(`SNOWFLAKE`, `DATABRICKS`, `GOODDATA`, `HONEYDEW`), and `SALESFORCE` already covers a
differently-branded product — the Tableau semantic layer — under the company token.

The cost of a company token is that it is broader than the payload: Microsoft ships
semantic surfaces that are not Tabular models. That is handled explicitly by the
`format` discriminator in the envelope below, rather than by minting a second vendor
token. This is a deliberate choice of the recoverable error over the unrecoverable one
— a payload that is too broadly labelled can be narrowed inside the envelope, whereas a
token that is too narrow cannot be widened once it is written into files in the wild.

**There is exactly one token.** `MICROSOFT` is canonical for both writing and reading.
Writers MUST NOT emit `POWER_BI`, `FABRIC`, `SSAS`, `AAS` or any other alias. Readers
MUST NOT accept aliases either: a reader that silently accepts two spellings makes it
impossible to notice that a writer disagrees, and the first symptom is a lost round
trip.

## What the extension is *not* for

The extension carries only what Ossie core cannot express. Anything with a core home
goes in the core field, and duplicating it here creates two sources of truth that drift.

In particular:

| Tabular construct | Ossie core home — do **not** put it in the extension |
|---|---|
| Measure DAX expression | `metrics[].expression.dialects[]` with `dialect: DAX` |
| Calculated column DAX | `fields[].expression.dialects[]` with `dialect: DAX` |
| Column `sourceColumn` | `fields[].expression` (an `ANSI_SQL` plain column reference) |
| Table | `datasets[]` |
| Column | `datasets[].fields[]` |
| Measure | `metrics[]` |
| Column `dataType` | `fields[].datatype` |
| Column `isKey` / `isUnique` | `datasets[].primary_key` / `.unique_keys` |
| Relationship endpoints | `relationships[]` |
| Object `description` | `description` |
| Time-role column | `fields[].dimension.is_time` |

> **Dialect dependency.** The `DAX` dialect member is added by
> [apache/ossie#250](https://github.com/apache/ossie/pull/250). Until that lands,
> `$defs.Dialect` has no `DAX` member and `validation/validate.py` will hand a DAX
> expression to sqlglot as if it were SQL. This registration deliberately does not
> duplicate #250's change; it assumes it and defers to it. See
> [Sequencing](#sequencing).

## Envelope

`CustomExtension.data` is a **string** containing a JSON **object**.

```json
{
  "_v": 1,
  "format": "TMSL",
  "<tmslPropertyName>": "<value>"
}
```

| Key | Required | Meaning |
|---|---|---|
| `_v` | yes | Integer payload-format version. A reader that encounters a `_v` higher than it understands MUST fail loudly rather than interpret the payload, because a newer writer may have changed what an existing key means. |
| `format` | recommended | Which Microsoft metadata surface this payload describes. `"TMSL"` is the only value defined here. A payload with no `format` key is `"TMSL"`, for compatibility with payloads written before the key existed. A reader that does not understand a `format` value MUST preserve the entry untouched and MUST NOT interpret its other keys. |

Every other key is a **TMSL property name, spelled exactly as TMSL spells it**
(camelCase, e.g. `formatString`, `displayFolder`, `crossFilteringBehavior`). No
synonyms, no re-casing, no invented names — so any key in a payload can be looked up
directly in the public TMSL reference. Values keep their TMSL shape and type.

Two consequences worth stating:

- **Preservation is a deny-list, not an allow-list.** A converter should carry across
  every TMSL property it did not consume, including properties it has never heard of,
  rather than only the ones enumerated below. New TMSL properties appear over time; an
  allow-list turns each one into silent data loss.
- **Preserved is not the same as modelled.** These constructs survive a round trip back
  to a Tabular model, but they are opaque to every other Ossie consumer. A converter
  should report them rather than let a user assume Ossie understood them.

## Placement

The extension is attached to the Ossie object that corresponds to the Tabular object,
so the payload never has to re-state where it belongs.

| Ossie object | Carries the leftovers of |
|---|---|
| `semantic_model[]` | `model`, plus anything database-level |
| `semantic_model[].datasets[]` | `table` |
| `semantic_model[].datasets[].fields[]` | `column` |
| `semantic_model[].metrics[]` | `measure` |
| `semantic_model[].relationships[]` | `relationship` |

Model-level constructs that have no Ossie object at all (roles, perspectives, cultures,
shared expressions) live in the model-level payload, keyed by their TMSL collection
name.

## Model level

| Key | Construct | Why core cannot hold it |
|---|---|---|
| `compatibilityLevel` | Database compatibility level (e.g. `1550`, `1600`). Gates which constructs the model may use — calculation groups and Direct Lake each require a minimum level. | No versioning concept in Ossie. |
| `defaultMode` | Default storage mode for partitions that do not state one. | See *Partitions and storage mode*. |
| `directLakeBehavior` | `automatic`, `directLakeOnly`, or `directQueryOnly` — what the engine does when a Direct Lake query cannot be served from the delta tables. | Execution policy, not structure. |
| `culture`, `sourceQueryCulture`, `collation` | Model locale and collation. Affects how format strings and sorting are interpreted. | No locale concept. |
| `discourageImplicitMeasures` | Whether report clients may build implicit aggregations over columns. Required to be `true` for calculation groups. | No client-behaviour concept. |
| `defaultPowerBIDataSourceVersion` | Which data-source generation the model uses. | Physical binding detail. |
| `roles` | Row-level and object-level security. See *Security roles*. | Ossie has no security model. |
| `perspectives` | Named subsets of the model. See *Perspectives*. | No subsetting concept. |
| `cultures` | Translations (captions, descriptions, display folders) and the linguistic schema used by Q&A. | Ossie objects have a single name. |
| `expressions` | Shared Power Query (M) expressions and parameters referenced by partitions. | Not a semantic construct. |
| `queryGroups` | Authoring folders for Power Query queries. | Authoring-tool organization. |
| `dataSources` | Legacy data source definitions. | Physical connection detail. |
| `annotations` | Arbitrary `name`/`value` pairs written by tools. Present on every Tabular object, not only the model. | Free-form tool metadata. |

## Dataset (table) level

| Key | Construct |
|---|---|
| `isHidden` | Table hidden from report clients. Visibility, not structure — a hidden table is still queryable by name. |
| `isPrivate` | Table hidden from *all* clients, typically an implementation detail of another construct. |
| `dataCategory` | Semantic role hint, e.g. `Time` for a date table. |
| `excludeFromModelRefresh` | Table skipped by refresh. |
| `partitions` | See *Partitions and storage mode*. |
| `hierarchies` | See *Hierarchies*. |
| `calculationGroup` | See *Calculation groups*. |
| `refreshPolicy` | Incremental refresh policy: rolling window, incremental granularity, and the `policyRange` partitions the engine generates from it. |
| `alternateOf` | Aggregation table mapping onto a detail table. |
| `defaultDetailRowsDefinition` | DAX table expression returning the rows behind an aggregate. |
| `lineageTag`, `sourceLineageTag` | See *Lineage tags*. |
| `annotations` | Tool metadata. |

## Field (column) level

| Key | Construct |
|---|---|
| `formatString` | See *Format strings*. |
| `displayFolder` | See *Display folders*. |
| `isHidden` | See *Visibility*. |
| `summarizeBy` | Default aggregation a report client applies when the column is dropped on a visual (`sum`, `average`, `count`, `none`, …). Ossie models explicit metrics, not client defaults. |
| `sortByColumn` | Another column in the same table that supplies the sort order — the standard "sort month name by month number" construct. Lost sorting is invisible until a chart is drawn in the wrong order. |
| `dataCategory` | Semantic role hint, e.g. `Address`, `WebUrl`, `Latitude`, `ImageUrl`. |
| `type` | Column kind: `data`, `calculated`, `calculatedTableColumn`, or `rowNumber`. |
| `dataType` | Retain when the TMSL type has no portable Ossie equivalent (`binary`, `variant`) or is unresolved (`automatic`, `unknown`), so export restores the original rather than guessing from the portable type. |
| `isNullable`, `isDefaultLabel`, `isDefaultImage`, `isAvailableInMdx` | Nullability and client-behaviour flags. |
| `encodingHint` | Storage encoding hint (`value`, `hash`, `default`). |
| `variations` | Date-table variations that let a column navigate to a date table. |
| `lineageTag`, `sourceLineageTag` | See *Lineage tags*. |
| `annotations` | Tool metadata. |

## Metric (measure) level

| Key | Construct |
|---|---|
| `formatString` | See *Format strings*. |
| `formatStringDefinition` | A **dynamic** format string: a DAX expression evaluated per cell that returns the format to use. |
| `displayFolder` | See *Display folders*. |
| `isHidden` | See *Visibility*. |
| `dataCategory` | Semantic role hint. |
| `kpi` | KPI wrapper: target expression, status expression and graphic. |
| `detailRowsDefinition` | DAX table expression returning the rows behind the aggregate. |
| `dataType` | The engine-inferred return type, when Ossie's `datatype` cannot express it. |
| `lineageTag`, `sourceLineageTag` | See *Lineage tags*. |
| `annotations` | Tool metadata. |

The measure's DAX expression itself does **not** go here — see
[What the extension is *not* for](#what-the-extension-is-not-for).

## Relationship level

| Key | Construct |
|---|---|
| `isActive` | Whether the relationship participates in filter propagation by default. Ossie relationships have no inactive state; an inactive relationship recorded as an ordinary Ossie relationship would misrepresent the join graph. |
| `crossFilteringBehavior` | `oneDirection`, `bothDirections`, or `automatic`. |
| `securityFilteringBehavior` | How row-level security filters propagate across the relationship. |
| `fromCardinality`, `toCardinality` | `one`, `many`, or `none`. Records many-to-many, which Ossie cannot express, and records the original orientation when a converter flipped the endpoints to put the many side on `from`. |
| `joinOnDateBehavior` | How a date/time join treats the time part. |
| `relyOnReferentialIntegrity` | Permits the engine to assume referential integrity for DirectQuery join elimination. |
| `lineageTag` | See *Lineage tags*. |

## Constructs in detail

### DAX expressions

A measure is DAX and only DAX. It belongs in the metric's `expression` under the `DAX`
dialect added by [#250](https://github.com/apache/ossie/pull/250):

```yaml
metrics:
  - name: Sales Amount
    expression:
      dialects:
        - dialect: DAX
          expression: SUM('Sales'[Extended Amount])
```

A metric MAY additionally carry an `ANSI_SQL` dialect when a portable equivalent
genuinely exists, and consumers pick a dialect per
[`core-spec/expression_language.md`](../../core-spec/expression_language.md).

Two rules keep this honest, and both matter more than they look:

1. **Never machine-translate between DAX and SQL and present the result as authored.**
   DAX aggregates resolve against a filter context with no SQL equivalent. A partial
   rewrite produces a model that loads, refreshes and returns a number — the wrong one.
   A missing metric is noticed; a plausible wrong one is not.
2. **A calculated column is not a measure.** A calculated column evaluates in row
   context, so an expression that is correct as a measure (`SUM('T'[X])`) returns the
   whole-column total on every row when used as a column.

### Format strings

`formatString` is a Microsoft custom format string (the VBA-derived syntax, plus the
named forms such as `Short Date` and `Currency`). It is the only place a Tabular model
records **date-only intent**: the `dataType` vocabulary has no date-only, time-only or
timezone-aware member, so every temporal value is stored as `dateTime` and a date-only
column is one whose format string has no time-of-day tokens.

A consumer that drops the format string therefore does not just lose presentation — it
loses the distinction between Ossie's `Date` and `DateTime`.

`formatStringDefinition` (on measures and calculation items) is a DAX expression
producing the format per cell, and takes precedence over the static `formatString`.

### Display folders

`displayFolder` groups columns, measures and hierarchies in the field list. Nesting uses
a backslash (`Sales\KPIs`), and an object can appear in several folders. It is purely
organizational, but for a large model it is most of what makes the field list navigable,
so dropping it is a real usability loss even though nothing computes differently.

Note that in JSON a nested folder is written with an escaped backslash: `"Sales\\KPIs"`.

### Visibility

`isHidden` marks an object as hidden from report clients. Hidden is **not** private and
not secured: a hidden column is still fully queryable by name, and hiding it is a
curation signal, not access control. Ossie has no visibility concept, so an AI consumer
reading an Ossie document sees a hidden surrogate key exactly as it sees a curated
business column unless this flag is preserved and surfaced.

Table-level `isPrivate` is stronger — hidden from all clients — and generally marks an
implementation detail such as an auto-generated date table.

### Hierarchies

A `hierarchy` is an ordered navigation path over columns of one table (`Year` →
`Quarter` → `Month` → `Date`), each `level` carrying `name`, `ordinal` and `column`,
with its own `isHidden`, `displayFolder` and lineage tags. Ossie has no ordered
drill path, and the ordering is the entire content of the construct — a set of the same
columns is not the same thing.

### Calculation groups

A `calculationGroup` on a table replaces its columns with `calculationItems`, each a
named DAX expression that **rewrites** the measure in scope via `SELECTEDMEASURE()` —
the standard way to express time intelligence (`YTD`, `PY`, `YoY %`) once instead of
per measure. An item may carry its own `formatStringDefinition`, and the group carries a
`precedence` that orders it against other calculation groups.

There is no Ossie equivalent, and there is no faithful expansion either: a calculation
group is a *transformation over metrics*, not a metric. Preserve the whole
`calculationGroup` object, and note that the containing table is not a dataset in the
ordinary sense. Calculation groups require `discourageImplicitMeasures: true` on the
model and a sufficient `compatibilityLevel`.

### Security roles

`roles` holds `ModelRole` objects. Each carries a `modelPermission`
(`none`, `read`, `readRefresh`, `refresh`, `administrator`), optional `members`, and
`tablePermissions`:

- **RLS** — `tablePermissions[].filterExpression` is a DAX boolean expression filtering
  the table's rows for members of the role.
- **OLS** — `tablePermissions[].columnPermissions[].metadataPermission` of `none` hides
  a column's *existence* from members of the role.

Ossie has no security model at all. Two things follow, and both should be said out loud
to anyone building on this:

- A document converted from a secured model describes the **unsecured** shape. It is not
  a security boundary and must not be treated as one.
- OLS in particular means the model a given user sees is not the model in the document.

Role membership may name identities. Whether membership should be preserved at all, or
dropped as unnecessary personal data, is an open question — see below.

### Partitions and storage mode

`partitions` on a table each carry a `mode` and a `source`:

| `mode` | Behaviour |
|---|---|
| `import` | Data is loaded into the in-memory engine at refresh. |
| `directQuery` | Queries are pushed to the source at query time. |
| `dual` | The engine chooses per query. |
| `directLake` | Delta tables in OneLake are read directly, with no import step. |
| `push` | Rows are pushed in through the API. |

Source types include `m` (Power Query), `query` (native query against a data source),
`entity` (a table in a Direct Lake or dataflow source), `calculated` (a DAX calculated
table), `calculationGroup`, and `policyRange` (a partition generated by an incremental
refresh policy).

The mode is not presentation. It determines whether the model holds data or delegates,
which functions are available, and what a refresh means. Ossie's `dataset.source` is a
single string naming the underlying table or query, so the mode, the partitioning scheme
and multi-partition tables all have to be preserved here.

### Perspectives

A `perspective` is a named subset of tables, columns, measures and hierarchies — a lens
for a report author or an AI consumer, not a security boundary (everything outside a
perspective remains queryable). Ossie documents the whole model, so perspectives are the
only record that a curated subset was intended.

### Lineage tags

`lineageTag` is a stable GUID identifying an object across edits, and `sourceLineageTag`
identifies the corresponding object in the upstream source. They are what let TMDL
diffing, Git integration and Direct Lake source mapping recognise a renamed object as
the same object.

They are the least interesting keys to read and among the most damaging to drop: a round
trip that loses lineage tags turns every rename into a delete plus an add.

## Round-trip rules

1. **One token, one entry.** At most one `MICROSOFT` entry per Ossie object. A writer
   that finds an existing entry merges into it rather than appending a second.
2. **Preserve foreign entries.** Other vendors' `custom_extensions` are not
   interpretable and MUST be carried through untouched.
3. **Omit when empty.** A Tabular model that uses no Microsoft-specific construct
   converts to clean, vendor-neutral Ossie with no `MICROSOFT` entry at all. An entry
   containing only `_v` and `format` is noise.
4. **Refuse, don't guess.** Malformed JSON, a non-object payload, a non-integer `_v`, a
   `_v` from the future, or an unknown `format` are all errors or explicit
   pass-throughs, never best-effort interpretation.

## Sequencing

This registration is intentionally decoupled from the converter implementation and from
the `DAX` dialect:

- The vendor token and these conventions stand on their own and can merge in any order
  relative to [#250](https://github.com/apache/ossie/pull/250).
- The `DAX` dialect member in `$defs.Dialect`, the `DAX` row in the spec's dialect table
  and the `DAX` entry in `validation/validate.py`'s skip list all come from #250 and are
  **not** duplicated here.
- Until #250 merges, an Ossie document that uses `dialect: DAX` fails schema validation
  (the enum has no such member) and, if it passed, would be handed to sqlglot as SQL.
  Simple DAX happens to parse as SQL; realistic DAX (`VAR` / `RETURN`) does not. This is
  why [`examples/microsoft_extension.yaml`](../../examples/microsoft_extension.yaml)
  carries no DAX expression yet.

## References

All public:

- [Tabular Model Scripting Language (TMSL) reference](https://learn.microsoft.com/analysis-services/tmsl/tabular-model-scripting-language-tmsl-reference)
- [Tabular Model Definition Language (TMDL)](https://learn.microsoft.com/analysis-services/tmdl/tmdl-overview)
- [Tabular Object Model (TOM)](https://learn.microsoft.com/analysis-services/tom/introduction-to-the-tabular-object-model-tom-in-analysis-services-amo)
- [Tabular model compatibility levels](https://learn.microsoft.com/analysis-services/tabular-models/compatibility-level-for-tabular-models-in-analysis-services)
- [Data Analysis Expressions (DAX) reference](https://learn.microsoft.com/dax/)
- [Calculation groups](https://learn.microsoft.com/analysis-services/tabular-models/calculation-groups)
- [Row-level security (RLS) with Power BI](https://learn.microsoft.com/power-bi/enterprise/service-admin-rls)
- [Object-level security (OLS)](https://learn.microsoft.com/analysis-services/tabular-models/object-level-security)
- [Direct Lake overview](https://learn.microsoft.com/fabric/fundamentals/direct-lake-overview)
- [Semantic Link Labs](https://github.com/microsoft/semantic-link-labs) — a public
  Python library that manipulates these same objects, useful as a worked reference.
