# Shared argument frame: options, positional arguments, usage errors.
# Copy unchanged; the app declares OPTIONS in app.py.
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .configfile import PROJECT_NAME


# Parsed command line: option values by name ("" for flags), then positional arguments
@dataclass(frozen=True, slots=True)
class CommandLine:
    options: Mapping[str, str] = field(default_factory=dict)
    positionals: list[str] = field(default_factory=list)


# Rejected command line; carries the problem text
class UsageError(ValueError):
    pass


# Split options from positional arguments; known maps each option to whether it takes a value
def parse_command_line(arguments: Sequence[str], known: Mapping[str, bool]) -> CommandLine:
    options: dict[str, str] = {}
    positionals: list[str] = []
    remaining = iter(arguments)
    for argument in remaining:
        # Everything after -- is positional
        if argument == "--":
            positionals.extend(remaining)
            break
        if not argument.startswith("-") or argument == "-":
            positionals.append(argument)
            continue
        # --name, --name value, or --name=value; the last one wins
        name, separator, value = argument.partition("=")
        if name not in known:
            raise UsageError(f"Unknown argument: {argument}")
        if not known[name] and separator:
            raise UsageError(f"Option takes no value: {name}")
        if known[name] and not separator:
            value = next(remaining, None)
            if value is None:
                raise UsageError(f"Missing value for {name}")
        options[name] = value
    return CommandLine(options, positionals)


# Usage problem on stderr; returns the failure exit code
def usage_failure(problem: str) -> int:
    print(f"❌ {problem}", file=sys.stderr)
    print(f"Run '{PROJECT_NAME} --help' for usage.", file=sys.stderr)
    return 1


# Unknown argument on stderr; returns the failure exit code
def usage_error(argument: str) -> int:
    return usage_failure(f"Unknown argument: {argument}")
