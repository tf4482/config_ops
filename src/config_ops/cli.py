# Shared CLI frame: help, options, subcommands, interrupt handling, exit codes.
# Copy unchanged; the app supplies HELP, OPTIONS, COMMANDS, and execute in app.py.
from __future__ import annotations

import sys
from collections.abc import Sequence

from .app import COMMANDS, HELP, OPTIONS, execute
from .arguments import (
    CommandLine,
    UsageError,
    parse_command_line,
    usage_error,
    usage_failure,
)
from .config import DEFAULT_CONFIG, parse_config
from .configfile import (
    PLACEHOLDER,
    ConfigCreated,
    ConfigError,
    load_config,
)
from .output import RED, YELLOW, Printer


# Help, argument check, colors, interrupt, exit code
def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["help"] or {"-h", "--help"} & set(arguments):
        print(HELP, end="")
        return 0
    # Options are checked before the banner
    try:
        command_line = parse_command_line(arguments, OPTIONS)
    except UsageError as error:
        return usage_failure(str(error))
    # Default run takes options only; a positional names a subcommand
    if command_line.positionals and command_line.positionals[0] not in COMMANDS:
        return usage_error(command_line.positionals[0])

    printer = Printer.create(sys.stdout)
    try:
        return _start(printer, command_line)
    except KeyboardInterrupt:
        printer.line("🛑", "Interrupted", YELLOW)
        return 130
    except Exception as error:
        # Type only; no traceback, no secrets
        printer.line("⚠️", f"Application error: {type(error).__name__}", RED)
        return 1


# Banner and configuration, then the default run or the named subcommand
def _start(printer: Printer, command_line: CommandLine) -> int:
    printer.header()
    try:
        _, config = load_config(DEFAULT_CONFIG, parse_config)
    except ConfigCreated as created:
        printer.line("📝", f"Created configuration: {created.path}", YELLOW)
        printer.line("✏️", f"Replace every {PLACEHOLDER} value, then rerun")
        return 1
    except ConfigError as error:
        return printer.fail("Configuration", error)
    if command_line.positionals:
        name, *rest = command_line.positionals
        return COMMANDS[name](printer, config, CommandLine(command_line.options, rest))
    return execute(printer, config, command_line.options)
