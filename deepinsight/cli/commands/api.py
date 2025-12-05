"""
API Server Command

Provides a CLI subcommand to start the DeepInsight backend API server.
"""

import argparse
import os
import sys
import subprocess
from typing import Optional


class ApiCommand:
    """CLI command handler for starting the backend API server."""

    def __init__(self):
        self.version = "1.0.0"

    def _create_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="deepinsight api",
            description="Start the DeepInsight backend API server",
        )
        subparsers = parser.add_subparsers(dest="subcommand", help="Operations")

        start_parser = subparsers.add_parser("start", help="Start API server")
        start_parser.add_argument(
            "--config",
            type=str,
            required=False,
            help="Path to config.yaml (defaults to ./config.yaml or $DEEPINSIGHT_CONFIG)",
        )
        start_parser.add_argument(
            "--expert-config",
            type=str,
            required=False,
            help="Path to experts.yaml (optional)",
        )
        start_parser.add_argument(
            "--env",
            action="append",
            default=[],
            help="Extra environment variables in KEY=VALUE form (can be repeated)",
        )
        return parser

    def execute(self, args: argparse.Namespace) -> int:
        parser = self._create_parser()
        parsed = parser.parse_args(sys.argv[2:])

        if parsed.subcommand != "start":
            parser.print_help()
            return 1

        return self._handle_start(parsed)

    def _handle_start(self, args: argparse.Namespace) -> int:
        # Resolve config and expert paths
        cfg_path: Optional[str] = args.config or os.getenv("DEEPINSIGHT_CONFIG")
        # Build the command to run the API module as a script
        cmd = [sys.executable, os.path.join("deepinsight", "api", "app.py")]
        if cfg_path:
            cmd.extend(["--config", cfg_path])
        if getattr(args, "expert_config", None):
            cmd.extend(["--expert_config", args.expert_config])

        # Prepare environment: propagate current env and optional overrides
        env = os.environ.copy()
        for kv in args.env or []:
            if "=" in kv:
                k, v = kv.split("=", 1)
                env[k] = v

        try:
            # Start the server in the foreground; user can Ctrl-C to stop.
            proc = subprocess.Popen(cmd, env=env)
            proc.wait()
            return proc.returncode or 0
        except KeyboardInterrupt:
            return 130
        except Exception as e:
            print(f"Failed to start API server: {e}")
            return 1