# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "jsonschema>=4.26.0",
#     "pyyaml>=6.0.3",
#     "sqlglot>=30.12.0",
# ]
# ///

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from validate import UniqueKeyLoader


class UniqueKeyLoaderTest(unittest.TestCase):
    def load(self, content: str):
        return yaml.load(content, Loader=UniqueKeyLoader)

    def assert_duplicate_key(self, content: str, key: str):
        with self.assertRaisesRegex(
            yaml.constructor.ConstructorError,
            rf"found duplicate key {key!r}",
        ):
            self.load(content)

    def test_rejects_duplicate_top_level_key(self):
        self.assert_duplicate_key(
            "version: 0.1.0\nversion: 0.2.0.dev0\n",
            "version",
        )

    def test_rejects_duplicate_nested_key(self):
        self.assert_duplicate_key(
            "dataset:\n  name: orders\n  source: staging.orders\n  source: production.orders\n",
            "source",
        )

    def test_rejects_quoted_equivalent_key(self):
        self.assert_duplicate_key(
            'name: sales\n"name": finance\n',
            "name",
        )

    def test_rejects_explicitly_tagged_equivalent_key(self):
        self.assert_duplicate_key(
            "name: sales\n!!str name: finance\n",
            "name",
        )

    def test_rejects_duplicate_json_object_key(self):
        self.assert_duplicate_key(
            '{"name": "sales", "name": "finance"}',
            "name",
        )

    def test_rejects_duplicate_collection_key(self):
        self.assert_duplicate_key(
            "datasets:\n  - name: orders\ndatasets:\n  - name: customers\n",
            "datasets",
        )

    def test_rejects_duplicate_that_would_hide_invalid_value(self):
        self.assert_duplicate_key(
            "source:\nsource: analytics.orders\n",
            "source",
        )

    def test_rejects_explicit_duplicate_after_merge(self):
        self.assert_duplicate_key(
            "dataset:\n"
            "  <<: &defaults\n"
            "    source: staging.orders\n"
            "  source: warehouse.orders\n"
            "  source: production.orders\n",
            "source",
        )

    def test_rejects_repeated_merge_key(self):
        self.assert_duplicate_key(
            "dataset:\n  <<: &first\n    source: staging.orders\n  <<: &second\n    name: orders\n",
            "<<",
        )

    def test_allows_same_key_in_separate_mappings(self):
        loaded = self.load(
            "datasets:\n"
            "  - name: orders\n"
            "    source: analytics.orders\n"
            "  - name: customers\n"
            "    source: analytics.customers\n"
        )

        self.assertEqual(loaded["datasets"][0]["name"], "orders")
        self.assertEqual(loaded["datasets"][1]["name"], "customers")

    def test_allows_aliases(self):
        loaded = self.load(
            "primary: &source analytics.orders\nbackup: *source\n"
        )

        self.assertEqual(loaded["primary"], "analytics.orders")
        self.assertEqual(loaded["backup"], "analytics.orders")

    def test_allows_merge_key_override(self):
        loaded = self.load(
            "defaults: &defaults\n  source: staging.orders\ndataset:\n  <<: *defaults\n  source: production.orders\n"
        )

        self.assertEqual(loaded["dataset"]["source"], "production.orders")

    def test_distinguishes_merge_key_from_quoted_literal(self):
        loaded = self.load(
            'defaults: &defaults\n  source: staging.orders\ndataset:\n  <<: *defaults\n  "<<": literal\n'
        )

        self.assertEqual(loaded["dataset"]["source"], "staging.orders")
        self.assertEqual(loaded["dataset"]["<<"], "literal")

    def test_duplicate_error_reports_both_locations(self):
        with self.assertRaises(yaml.constructor.ConstructorError) as caught:
            self.load("name: sales\nname: finance\n")

        error = caught.exception
        self.assertEqual(error.context_mark.line, 0)
        self.assertEqual(error.problem_mark.line, 1)


class ValidatorIntegrationTest(unittest.TestCase):
    def run_validator(self, content: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "model.yaml"
            model_path.write_text(content)
            return subprocess.run(
                [sys.executable, Path(__file__).with_name("validate.py"), model_path],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_duplicate_key_exits_nonzero(self):
        result = self.run_validator(
            "version: 0.2.0.dev0\n"
            "semantic_model:\n"
            "  - name: sales\n"
            "    name: finance\n"
            "    datasets:\n"
            "      - name: orders\n"
            "        source: analytics.orders\n"
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Error: Invalid YAML", result.stdout)
        self.assertIn("found duplicate key 'name'", result.stdout)

    def test_valid_model_still_passes(self):
        result = self.run_validator(
            "version: 0.2.0.dev0\n"
            "semantic_model:\n"
            "  - name: sales\n"
            "    datasets:\n"
            "      - name: orders\n"
            "        source: analytics.orders\n"
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Validation PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
