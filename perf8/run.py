import sys
import subprocess
import asyncio
import time
import importlib
import argparse
import shlex

from perf8 import __version__


def _parser():
    parser = argparse.ArgumentParser(
        description="Python Performance Tracking.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--psutil",
        action="store_true",
        default=False,
        help="Track system info",
    )

    parser.add_argument(
        "-c",
        "--command",
        default="dummy.py --with-option -a=2",
        type=str,
        nargs=argparse.REMAINDER,
        help="Command to run",
    )

    parser.add_argument(
        "--version",
        action="store_true",
        default=False,
        help="Displays version and exits.",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help=(
            "Verbosity level. -v will display "
            "tracebacks. -vv requests and responses."
        ),
    )

    return parser


def main(args=None):
    if args is None:
        parser = _parser()
        args = parser.parse_args()

    if args.version:
        print(__version__)
        sys.exit(0)

    p = WatchedProcess(args)
    asyncio.run(p.run())
    return 0


class WatchedProcess:
    def __init__(self, args, event_timer=10, plugins=None, options=None):
        self.args = args
        self.cmd = args.command
        self.proc = self.pid = None
        self.every = event_timer
        if options is None:
            options = {}
        self.options = options
        if plugins is None:
            plugins = ["perf8._psutil:ResourceWatcher"]
        self.plugins = [self._create_plugin(plugin) for plugin in plugins]

    async def _probe(self):
        while self.proc.poll() is not None:
            for plugin in self.plugins:
                await plugin.probe(self.pid)
            if self.proc.poll() is None:
                break
            await asyncio.sleep(self.every)

    def start(self):
        print(f"[perf8] Plugins: {', '.join([p.name for p in self.plugins])}")
        for plugin in self.plugins:
            plugin.start(self.pid)

    def stop(self):
        for plugin in self.plugins:
            plugin.stop(self.pid)

    async def run(self):
        start = time.time()
        print(f"[perf8] Running {shlex.join(self.cmd)}")
        try:
            self.proc = subprocess.Popen(self.cmd)
            while self.proc.pid is None:
                await asyncio.sleep(1.0)
            self.pid = self.proc.pid
            self.start()

            await self._probe()
        finally:
            self.stop()

        self.proc.wait()
        print(f"[perf8] Total seconds {time.time()-start}")

    def _create_plugin(self, fqn):
        # XXX filter options by plugins
        module_name, klass_name = fqn.split(":")
        module = importlib.import_module(module_name)
        return getattr(module, klass_name)(**self.options)


if __name__ == "__main__":
    main()
