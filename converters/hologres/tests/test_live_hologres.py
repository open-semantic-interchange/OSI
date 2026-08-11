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

"""End-to-end verification against a real Hologres instance.

Skipped unless the connection environment variables are set, so CI and a plain
`uv run pytest` stay hermetic:

    export HOLOGRES_HOST=<endpoint>
    export HOLOGRES_PORT=80
    export HOLOGRES_USER='BASIC$account'   # single quotes: the $ is literal
    export HOLOGRES_PASSWORD='<password>'
    export HOLOGRES_DB=<database>
    uv sync --group live
    uv run pytest -m live -v

Credentials are only ever read from the environment. Nothing here, in the fixtures, or
in the README contains a real endpoint or password.

What this proves that the offline tests cannot: that the generated DDL is accepted by
Hologres, that the resulting view answers queries with the right numbers, and that
importing Hologres' own readback of that DDL reproduces the model we started from.
"""

import os
import warnings

import pytest
from _util import read_fixture
from ossie_hologres import convert_ossie_to_semantic_view, convert_semantic_view_to_ossie
from ossie_hologres._common import load_yaml

psycopg = pytest.importorskip("psycopg", reason="install the 'live' dependency group")

_ENV_VARS = ("HOLOGRES_HOST", "HOLOGRES_USER", "HOLOGRES_PASSWORD", "HOLOGRES_DB")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not all(os.environ.get(v) for v in _ENV_VARS),
        reason=f"set {', '.join(_ENV_VARS)} to run the live Hologres tests",
    ),
]

# Everything created here is namespaced so a failed run cannot be mistaken for, or
# collide with, anything else in the database.
SCHEMA = "ossie_hologres_it"
VIEW = "it_sales_sv"
REEXPORT_VIEW = "it_sales_sv_reexport"

# Minimal star schema matching tests/fixtures/fixtureB_ossie.yaml. Rows are inserted so
# the assertions can check numbers, not just that the DDL parses -- in particular that
# order revenue is not multiplied by the joined order_items rows.
_BASE_TABLES = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.svacc_customers (
  customer_id int PRIMARY KEY, city text, credit_limit numeric(18,2));
CREATE TABLE IF NOT EXISTS {SCHEMA}.svacc_orders (
  order_id int PRIMARY KEY, customer_id int, region text, status text,
  amount numeric(18,2));
CREATE TABLE IF NOT EXISTS {SCHEMA}.svacc_order_items (
  item_id int PRIMARY KEY, order_id int, quantity int);
"""

_ROWS = f"""
INSERT INTO {SCHEMA}.svacc_customers VALUES
  (1, 'Beijing', 1000.00), (2, 'Shanghai', 2000.00), (3, 'Beijing', 1500.00);
INSERT INTO {SCHEMA}.svacc_orders VALUES
  (101, 1, 'east', 'completed', 100.00), (102, 1, 'east', 'completed', 200.00),
  (103, 2, 'west', 'completed', 150.00), (104, 2, 'west', 'pending', 50.00);
INSERT INTO {SCHEMA}.svacc_order_items VALUES
  (1001, 101, 1), (1002, 101, 2), (1003, 102, 1),
  (1004, 102, 1), (1005, 103, 3), (1006, 104, 1);
"""


def _export(ossie_yaml, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return convert_ossie_to_semantic_view(ossie_yaml, **kwargs)


def _retarget(ossie_yaml, source_prefix):
    """Point a fixture's dataset sources at the integration test schema.

    The --schema option deliberately never overrides a schema already written into a
    `source`, and these fixtures name one, so the rewrite has to happen in the input.
    """
    return ossie_yaml.replace(source_prefix, f"{os.environ['HOLOGRES_DB']}.{SCHEMA}.")


@pytest.fixture(scope="module")
def conn():
    """An autocommit connection, or a skip if the instance is too old."""
    dsn = psycopg.conninfo.make_conninfo(
        host=os.environ["HOLOGRES_HOST"],
        port=os.environ.get("HOLOGRES_PORT", "80"),
        user=os.environ["HOLOGRES_USER"],
        password=os.environ["HOLOGRES_PASSWORD"],
        dbname=os.environ["HOLOGRES_DB"],
    )
    with psycopg.connect(dsn, autocommit=True) as connection:
        version = connection.execute("SELECT hg_version()").fetchone()[0]
        if not version.startswith("Hologres ") or version.split()[1] < "5.0.0":
            pytest.skip(f"Semantic Views need Hologres V5.0.0 or later, got: {version}")
        yield connection


@pytest.fixture(scope="module")
def star(conn):
    """Create the base tables and rows, and remove everything afterwards."""
    try:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        conn.execute(_BASE_TABLES)
        conn.execute(f"TRUNCATE {SCHEMA}.svacc_customers")
        conn.execute(f"TRUNCATE {SCHEMA}.svacc_orders")
        conn.execute(f"TRUNCATE {SCHEMA}.svacc_order_items")
        conn.execute(_ROWS)
        yield conn
    finally:
        for view in (VIEW, REEXPORT_VIEW):
            conn.execute(f"DROP SEMANTIC VIEW IF EXISTS {SCHEMA}.{view}")
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.fixture(scope="module")
def created_view(star):
    """Export fixtureB to DDL, execute it, and return the DDL that was run.

    This is the assertion that matters most: if the generated grammar, the aggregate
    whitelist, or the REFERENCES/PRIMARY KEY rules were wrong, this fails.
    """
    ossie = _retarget(
        read_fixture("fixtureB_ossie.yaml").replace(
            "name: svacc_sales_sv", f"name: {VIEW}"
        ),
        "retail.public.",
    )
    ddl = _export(ossie, schema=SCHEMA, drop_if_exists=True)
    star.execute(ddl)
    return ddl


def _model_yaml(conn, view_name):
    row = conn.execute(
        """
        SELECT property_value
        FROM hologres.hg_semantic_view_properties
        WHERE schema_name = %s AND view_name = %s AND property_key = 'model_yaml'
        """,
        (SCHEMA, view_name),
    ).fetchone()
    assert row is not None, f"Hologres published no model_yaml for {view_name}"
    return row[0]


class TestGeneratedDdlIsAccepted:
    def test_the_view_exists_after_running_the_ddl(self, star, created_view):
        keys = star.execute(
            """
            SELECT property_key FROM hologres.hg_semantic_view_properties
            WHERE schema_name = %s AND view_name = %s
            """,
            (SCHEMA, VIEW),
        ).fetchall()
        assert {"ddl_text", "model_yaml"} <= {k for (k,) in keys}

    def test_the_tpcds_snowflake_ddl_is_also_accepted(self, star):
        # A wider shape than fixtureB: five tables, a composite primary key, four
        # relationships, and a computed dimension needing parentheses.
        from _util import EXAMPLES

        stubs = f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.store_sales (
          ss_item_sk int, ss_ticket_number int, ss_sold_date_sk int, ss_customer_sk int,
          ss_store_sk int, ss_quantity int, ss_sales_price numeric(7,2),
          ss_ext_sales_price numeric(7,2), ss_net_profit numeric(7,2),
          PRIMARY KEY (ss_item_sk, ss_ticket_number));
        CREATE TABLE IF NOT EXISTS {SCHEMA}.date_dim (
          d_date_sk int PRIMARY KEY, d_date date, d_year int, d_quarter_name text,
          d_month_name text);
        CREATE TABLE IF NOT EXISTS {SCHEMA}.customer (
          c_customer_sk int PRIMARY KEY, c_customer_id text, c_first_name text,
          c_last_name text, c_email_address text);
        CREATE TABLE IF NOT EXISTS {SCHEMA}.item (
          i_item_sk int PRIMARY KEY, i_item_id text, i_item_desc text, i_brand text,
          i_category text, i_current_price numeric(7,2));
        CREATE TABLE IF NOT EXISTS {SCHEMA}.store (
          s_store_sk int PRIMARY KEY, s_store_id text, s_store_name text, s_city text,
          s_state text, s_number_employees int);
        """
        star.execute(stubs)
        ossie = _retarget(
            (EXAMPLES / "tpcds_semantic_model.yaml").read_text(encoding="utf-8"),
            "tpcds.public.",
        )
        ddl = _export(
            ossie, schema=SCHEMA, drop_if_exists=True, skip_unsupported_metrics=True
        )
        star.execute(ddl)
        star.execute(f"DROP SEMANTIC VIEW IF EXISTS {SCHEMA}.tpcds_retail_model")


class TestGeneratedViewAnswersQueries:
    def test_single_metric_group_aggregation(self, star, created_view):
        rows = star.execute(
            f"SELECT region_dim, AGG(total) FROM {SCHEMA}.{VIEW} "
            f"GROUP BY region_dim ORDER BY region_dim"
        ).fetchall()
        assert rows == [("east", 300), ("west", 200)]

    def test_metrics_are_not_inflated_by_a_fan_out_join(self, star, created_view):
        # Each order has several order_items rows. A naive join-then-aggregate would
        # multiply revenue; Hologres aggregates each metric group in its own subtree.
        rows = star.execute(
            f"SELECT city_dim, AGG(total), AGG(credit), AGG(item_qty) FROM {SCHEMA}.{VIEW} "
            f"GROUP BY city_dim ORDER BY city_dim"
        ).fetchall()
        # Beijing credit is 2500 because customer 3 has no orders yet still contributes
        # at customer grain -- the point of aggregating before joining.
        assert rows == [("Beijing", 300, 2500, 5), ("Shanghai", 200, 2000, 4)]

    def test_where_only_dimension_filters_without_changing_the_grain(self, star, created_view):
        rows = star.execute(
            f"SELECT region_dim, AGG(total) FROM {SCHEMA}.{VIEW} "
            f"WHERE status_dim = 'completed' GROUP BY region_dim ORDER BY region_dim"
        ).fetchall()
        assert rows == [("east", 300), ("west", 150)]

    def test_global_aggregation_and_having(self, star, created_view):
        total, count = star.execute(
            f"SELECT AGG(total), AGG(order_count) FROM {SCHEMA}.{VIEW}"
        ).fetchone()
        assert (total, count) == (500, 4)

        rows = star.execute(
            f"SELECT city_dim, AGG(total) FROM {SCHEMA}.{VIEW} "
            f"GROUP BY city_dim HAVING AGG(total) > 250"
        ).fetchall()
        assert rows == [("Beijing", 300)]


class TestFullRoundTrip:
    def test_importing_the_readback_reproduces_the_original_model(self, star, created_view):
        # Ossie -> DDL -> Hologres -> model_yaml -> Ossie, closed against the fixture.
        imported = load_yaml(convert_semantic_view_to_ossie(_model_yaml(star, VIEW)))
        expected = load_yaml(
            _retarget(
                read_fixture("fixtureB_ossie.yaml").replace(
                    "name: svacc_sales_sv", f"name: {VIEW}"
                ),
                "retail.public.",
            )
        )
        assert imported == expected

    def test_re_exporting_produces_an_equivalent_view(self, star, created_view):
        # Compare Hologres' normalization of our DDL with its normalization of the DDL we
        # regenerate from its own readback. Comparing our text to theirs would only
        # measure their formatting choices.
        ossie = convert_semantic_view_to_ossie(_model_yaml(star, VIEW))
        ddl = _export(
            ossie.replace(f"name: {VIEW}", f"name: {REEXPORT_VIEW}"),
            schema=SCHEMA,
            drop_if_exists=True,
        )
        star.execute(ddl)

        first = _model_yaml(star, VIEW).replace(VIEW, "X")
        second = _model_yaml(star, REEXPORT_VIEW).replace(REEXPORT_VIEW, "X")
        assert load_yaml(first) == load_yaml(second)

    def test_the_checked_in_model_yaml_fixture_still_matches_the_instance(self, star, created_view):
        # If Hologres changes the shape it emits, this fails and says to refresh the
        # offline fixture, instead of the offline tests quietly testing a stale format.
        live = load_yaml(_model_yaml(star, VIEW))
        fixture = load_yaml(read_fixture("fixtureB_model_yaml.yaml"))

        live["name"] = fixture["name"] = "X"
        for doc in (live, fixture):
            for table in doc["tables"]:
                # Only the location differs: the fixture was captured from public.
                table["base_table"] = table["base_table"]["table"]
        assert live == fixture, (
            "Hologres' model_yaml no longer matches tests/fixtures/fixtureB_model_yaml.yaml; "
            "refresh the fixture from the instance"
        )
