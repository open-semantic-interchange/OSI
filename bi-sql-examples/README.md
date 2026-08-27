BI SQL examples
===============

Real SQL that BI tools generate when they query, via SQL, metrics defined outside
the tool. The
goal is to study these query shapes to ensure that Ossie can fully and safely
cover the complex cases BI tools produce.

The examples read measures with the `MEASURE()` function, but that is only for
illustration: any proposal will require the BI tool to have some function for
querying measures.

Layout
------

One subfolder per BI tool. Each subfolder holds a `setup.sql` that shows the
shape of the tables and metrics, one `.sql` file per feature area, and a
`README.md`.
