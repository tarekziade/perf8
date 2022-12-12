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
import sys
import subprocess
import asyncio
import time
import importlib
import argparse
import shlex
import signal
import os
import json
from collections import defaultdict
import datetime

from perf8 import __version__
from perf8.plugins.base import get_registered_plugins

HERE = os.path.dirname(__file__)
MENU_TEMPLATE = os.path.join(HERE, "templates", "menu.html.tmpl")
INDEX_TEMPLATE = os.path.join(HERE, "templates", "index.html.tmpl")
SUMMARY_TEMPLATE = os.path.join(HERE, "templates", "summary.html.tmpl")


def parser():
    aparser = argparse.ArgumentParser(
        description="Python Performance Tracking.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    for plugin in get_registered_plugins():
        aparser.add_argument(
            f"--{plugin.name}",
            action="store_true",
            default=False,
            help=plugin.description,
        )
        # XXX ask the plugin for its arguments and set them in a group

    aparser.add_argument(
        "-t",
        "--target-dir",
        default=os.path.join(os.getcwd(), "perf8-report"),
        type=str,
        help="target dir for results",
    )
    aparser.add_argument(
        "--title",
        default="Performance Report",
        type=str,
        help="String used for the report title",
    )
    aparser.add_argument(
        "-r",
        "--report",
        default="report.html",
        type=str,
        help="report file",
    )
    aparser.add_argument(
        "--refresh-rate",
        type=int,
        default=5,
        help="Refresh rate",
    )
    aparser.add_argument(
        "-c",
        "--command",
        default=os.path.join(HERE, "tests", "demo.py"),
        type=str,
        nargs=argparse.REMAINDER,
        help="Command to run",
    )

    aparser.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Use all plugins",
    )

    aparser.add_argument(
        "--version",
        action="store_true",
        default=False,
        help="Displays version and exits.",
    )

    aparser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help=(
            "Verbosity level. -v will display "
            "tracebacks. -vv requests and responses."
        ),
    )

    return aparser


def main(args=None):
    os.environ["PERF8_ARGS"] = "::".join(sys.argv[1:])

    if args is None:
        aparser = parser()
        args = aparser.parse_args()

    if args.version:
        print(__version__)
        sys.exit(0)

    if args.memray and args.gprof2dot:
        raise Exception("You can't use --memray and --gprof2dot at the same time")

    if args.all:
        for plugin in get_registered_plugins():
            if plugin.name == "cprofile":
                args.gprof2dot = False
            else:
                setattr(args, plugin.name.replace("-", "_"), True)

    p = WatchedProcess(args)
    asyncio.run(p.run())
    return 0


class WatchedProcess:
    def __init__(self, args):
        self.args = args
        self.cmd = args.command
        if not isinstance(self.cmd, str):
            self.cmd = shlex.join(self.cmd)
        self.proc = self.pid = None
        self.every = args.refresh_rate
        self.plugins = [
            plugin for plugin in get_registered_plugins() if getattr(args, plugin.name)
        ]
        self.out_plugins = [
            plugin(self.args) for plugin in self.plugins if not plugin.in_process
        ]
        self.reports = defaultdict(list)
        os.makedirs(self.args.target_dir, exist_ok=True)
        signal.signal(signal.SIGINT, self.exit)
        signal.signal(signal.SIGTERM, self.exit)

    def exit(self, signum, frame):
        print(f"We got a {signum} signal, passing it along")
        os.kill(self.proc.pid, signum)

    async def _probe(self):
        while self.proc.poll() is None:
            for plugin in self.out_plugins:
                await plugin.probe(self.pid)
            if self.proc.poll() is not None:
                break
            await asyncio.sleep(self.every)

    def start(self):
        print(f"[perf8] Plugins: {', '.join([p.name for p in self.plugins])}")
        for plugin in self.out_plugins:
            plugin.start(self.pid)

    def stop(self):
        for plugin in self.out_plugins:
            self.reports[plugin.name].extend(plugin.stop(self.pid))

    async def run(self):
        start = time.time()
        plugins = [plugin.fqn for plugin in self.plugins if plugin.in_process]

        # XXX pass-through perf8 args so the plugins can pick there options
        cmd = [sys.executable, "-m", "perf8.runner", "-t", self.args.target_dir]

        if len(plugins) > 0:
            cmd.extend(["--plugins", ",".join(plugins)])

        cmd.extend(["-s", self.cmd])

        cmd = [str(item) for item in cmd]

        print(f"[perf8] Running {shlex.join(cmd)}")
        try:
            self.proc = subprocess.Popen(cmd)
            while self.proc.pid is None:
                await asyncio.sleep(1.0)
            self.pid = self.proc.pid
            self.start()

            await self._probe()
        finally:
            self.stop()

        self.proc.wait()
        print(f"[perf8] Total seconds {time.time()-start}")

        # read report.json to extend the list
        with open(os.path.join(self.args.target_dir, "report.json")) as f:
            reports = json.loads(f.read())

            for report in reports["reports"]:
                self.reports[report["name"]].append(report)

        print("[perf8] Reports generated:")
        for plugin in self.plugins:
            if plugin.name not in self.reports:
                continue
            print(
                f"[perf8] Plugin {plugin.name} generated {len(self.reports[plugin.name])} report(s)"
            )

        # generating meta remport
        with open(MENU_TEMPLATE) as f:
            render = f.read()

        reports = []
        artifacts = []

        for reporter in self.reports.values():
            for report in reporter:
                relative = os.path.basename(report["file"])
                if report["type"] == "artifact":
                    artifacts.append(
                        f"<li><a href='{relative}' download='{relative}' target='_blank'>{report['label']}</a></li>"
                    )

                    continue

                reports.append(
                    f"<li><a href='{relative}' target='content'>{report['label']}</a></li>"
                )

        html_report = os.path.join(self.args.target_dir, "menu.html")
        with open(html_report, "w") as f:
            f.write(
                render
                % {
                    "reports": "\n".join(reports),
                    "artifacts": "\n".join(artifacts),
                    "version": __version__,
                    "created_at": datetime.datetime.now().strftime("%d-%m-%y %H:%M:%S"),
                    "title": self.args.title,
                }
            )

        with open(INDEX_TEMPLATE) as f:
            render = f.read()
        html_report = os.path.join(self.args.target_dir, "index.html")
        with open(html_report, "w") as f:
            f.write(render % {"default_page": "summary.html", "title": self.args.title})

        # summary
        plugins = [
            f"<li>{plugin.name} -- {plugin.description}</li>" for plugin in self.plugins
        ]
        with open(SUMMARY_TEMPLATE) as f:
            render = f.read()
        summary_page = os.path.join(self.args.target_dir, "summary.html")
        with open(summary_page, "w") as f:
            f.write(
                render
                % {
                    "command": " ".join(self.args.command),
                    "title": self.args.title,
                    "plugins": "\n".join(plugins),
                }
            )

        print(f"Find the full report at {html_report}")

    def _plugin_klass(self, fqn):
        module_name, klass_name = fqn.split(":")
        module = importlib.import_module(module_name)
        return getattr(module, klass_name)


if __name__ == "__main__":
    main()
