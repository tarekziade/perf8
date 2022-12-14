#
# Licensed to Elasticsearch B.V. under one or more contributor
# license agreements. See the NOTICE file distributed with
# this work for additional information regarding copyright
# ownership. Elasticsearch B.V. licenses this file to you under
# the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# 	http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
import os
import shutil
import tempfile
import sys

from perf8.cli import main


def test_main():
    target_dir = tempfile.mkdtemp()
    os.environ["RANGE"] = "100"

    args = [
        "perf8",
        "--all",
        "-t",
        target_dir,
        "-c",
        os.path.join(os.path.dirname(__file__), "demo.py"),
    ]

    old_sys = sys.argv
    sys.argv = args
    try:
        main()
        assert os.path.exists(os.path.join(target_dir, "index.html"))
    finally:
        sys.argv = old_sys
        shutil.rmtree(target_dir)
