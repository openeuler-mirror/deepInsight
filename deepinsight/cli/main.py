#!/usr/bin/env python3
"""
DeepInsight CLI Main Entry Point

This script provides the main command-line interface for DeepInsight,
supporting subcommands for different functionalities.
"""

import argparse
import sys
import dotenv
from typing import List, Optional

# Added: unified rich logging configuration to reduce noisy outputs
import logging
from rich.logging import RichHandler
from rich import get_console


dotenv.load_dotenv(override=True)

# Configure Rich logging and suppress noisy third-party loggers
_console = get_console()
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=_console, show_time=False, rich_tracebacks=False, markup=True)],
)
# Reduce verbosity from common noisy libraries
for _noisy in [
    "lightrag",
    "transformers",
    "httpx",
    "uvicorn",
    "sqlalchemy",
    "asyncio",
    "torch",
]:
    logging.getLogger(_noisy).setLevel(logging.WARNING)
# Keep our own app logger at INFO (others default to root level set above)
logging.getLogger("deepinsight").setLevel(logging.INFO)


class DeepInsightCLI:
    """Main CLI class for DeepInsight."""
    
    def __init__(self):
        self.parser = self._create_parser()
        self._command_instances = {}
    
    def _get_command(self, command_name: str):
        if command_name not in self._command_instances:
            if command_name == 'resch':
                from deepinsight.cli.commands.research import ResearchCommand
                self._command_instances[command_name] = ResearchCommand()
            elif command_name == 'conf':
                from deepinsight.cli.commands.conference import ConferenceCommand
                self._command_instances[command_name] = ConferenceCommand()
            elif command_name == 'api':
                from deepinsight.cli.commands.api import ApiCommand
                self._command_instances[command_name] = ApiCommand()
            else:
                return None
        return self._command_instances[command_name]
    
    def _create_parser(self) -> argparse.ArgumentParser:
        """Create the main argument parser."""
        parser = argparse.ArgumentParser(
            prog='di',
            description='DeepInsight CLI - AI-powered research and knowledge management tool',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  di conf gen --name "ICLR 2025" --files-src ./docs
  di conf chat --name "ICLR 2025" --files-src ./docs --question "今年最佳论文有哪些创新点？"
  di resch gen --topic "ICLR 2025"
  di --version

For more information on a specific command, run:
  di <command> --help
            """
        )
        
        parser.add_argument(
            '--version',
            action='version',
            version='DeepInsight CLI 1.0.0'
        )
        
        parser.add_argument(
            '--verbose', '-v',
            action='store_true',
            help='Enable verbose output'
        )
        
        # Add subparsers for commands
        subparsers = parser.add_subparsers(
            dest='command',
            help='Available commands',
            metavar='<command>'
        )
        

        
        # Research command
        resch_parser = subparsers.add_parser(
            'resch',
            help='Deep research',
            description='Usage: di resch gen --topic "<research topic>"',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog='Examples:\n  di resch gen --topic "ICLR 2025"\n  di resch gen --topic "AI trends"'
        )
        resch_parser.add_argument(
            'args',
            nargs=argparse.REMAINDER,
            help='Use "di resch gen --topic \"...\""'
        )
        
        # Conference management command
        conf_parser = subparsers.add_parser(
            'conf',
            help='Top conference management',
            description='Manage top conference information via CLI'
        )
        conf_parser.add_argument(
            'args',
            nargs=argparse.REMAINDER,
            help='Arguments for conference subcommands (parsed by ConferenceCommand)'
        )

        # API server command
        api_parser = subparsers.add_parser(
            'api',
            help='Start backend API server',
            description='Start DeepInsight backend API service'
        )
        api_parser.add_argument(
            'args',
            nargs=argparse.REMAINDER,
            help='Arguments for api subcommands (parsed by ApiCommand)'
        )
        return parser

    def run(self, args: Optional[List[str]] = None) -> int:
        """Run the CLI with the given arguments."""
        if args is None:
            args = sys.argv[1:]
        
        try:
            parsed_args = self.parser.parse_args(args)
            
            if not parsed_args.command:
                self.parser.print_help()
                return 1
            
            # Forward research help to subcommand parser for better UX
            if parsed_args.command == 'resch':
                rest = getattr(parsed_args, 'args', [])
                if not rest or '--help' in rest or '-h' in rest:
                    from deepinsight.cli.commands.research import ResearchCommand
                    ResearchCommand()._create_parser().print_help()
                    return 0
            if parsed_args.command == 'api':
                rest = getattr(parsed_args, 'args', [])
                if not rest or '--help' in rest or '-h' in rest:
                    from deepinsight.cli.commands.api import ApiCommand
                    ApiCommand()._create_parser().print_help()
                    return 0

            # Get the appropriate command handler
            command = self._get_command(parsed_args.command)
            if not command:
                print(f"Error: Unknown command '{parsed_args.command}'")
                return 1
            
            # Execute the command
            return command.execute(parsed_args)
            
        except KeyboardInterrupt:
            print("\nOperation cancelled by user.")
            return 130
        except Exception as e:
            if parsed_args.verbose if 'parsed_args' in locals() else False:
                import traceback
                traceback.print_exc()
            else:
                print(f"Error: {e}")
            return 1


def main() -> int:
    """Main entry point for the CLI."""
    cli = DeepInsightCLI()
    return cli.run()


if __name__ == '__main__':
    sys.exit(main())
