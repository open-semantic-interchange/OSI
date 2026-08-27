Tableau BI SQL examples
=======================

Real SQL that [Tableau](https://www.tableau.com/) generated while querying metrics
defined outside the tool. The proof of concept ran against a development build of
Databricks Metric Views, but the approach should work with a SQL interface to Ossie.

The queries in this folder are captured for their SQL shape. `setup.sql` shows the model
shape generally and is not runnable; the proof of concept used Databricks Metric
View DDL for the actual model.

How the SQL was produced
------------------------

Tableau queried the model through its existing SQL generation pipeline, with two
workarounds because this Tableau build does not query these natively:

- **Measures**: the POC used Tableau's
  [RAWSQLAGG](https://help.tableau.com/current/pro/desktop/en-us/functions_functions_passthrough.htm)
  functions so that it could invoke the measure function. Every measure is
  evaluated on the engine side.
- **Multi-table models**: built by hand in Tableau using joins and
  relationships.
