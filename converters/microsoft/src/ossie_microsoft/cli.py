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

"""Command-line interface for the Microsoft Power BI <-> Apache Ossie converter.

    ossie-microsoft import -i model.bim   [-o model.yaml]
    ossie-microsoft export -i model.yaml  [-o model.bim]

With no ``-o``, the result is written to stdout.
"""

import argparse
import json
import sys

import yaml

from ._common import ConversionError
from .ossie_to_semantic_model import convert_ossie_to_semantic_model
from .semantic_model_to_ossie import convert_semantic_model_to_ossie


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="ossie-microsoft",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    imp = sub.add_parser(
        "import", help="Power BI semantic model (TMSL model.bim) -> Apache Ossie semantic model"
    )
    imp.add_argument("-i", "--input", required=True, help="model.bim (TMSL JSON) file")
    imp.add_argument("-o", "--output", help="output Apache Ossie YAML (default: stdout)")

    exp = sub.add_parser(
        "export", help="Apache Ossie semantic model -> Power BI semantic model (TMSL model.bim)"
    )
    exp.add_argument("-i", "--input", required=True, help="Apache Ossie YAML file")
    exp.add_argument("-o", "--output", help="output model.bim (default: stdout)")
    return parser


def _run_import(args):
    with open(args.input, encoding="utf-8-sig") as fh:
        return convert_semantic_model_to_ossie(json.load(fh))


def _run_export(args):
    with open(args.input, encoding="utf-8-sig") as fh:
        document = yaml.safe_load(fh)
    # Power BI writes model.bim as UTF-8 JSON with non-ASCII characters left as-is.
    return json.dumps(
        convert_ossie_to_semantic_model(document), indent=2, ensure_ascii=False
    ) + "\n"


def main(argv=None):
    args = _build_parser().parse_args(argv)
    handler = _run_import if args.command == "import" else _run_export
    try:
        out = handler(args)
    except (ConversionError, TypeError, ValueError, OSError, yaml.YAMLError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
