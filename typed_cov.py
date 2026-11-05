#!/usr/bin/env python3
"""typed-cov: how much of a Python project has type hints, and exactly what does not.

Reads the ``.py`` files below a folder with ``ast.parse`` (nothing is imported or
run), counts the parameters and the return values of the functions and methods,
and reports which of them have an annotation and which do not, with the file and
the line of each one that is missing. Python 3.11, standard library only.
"""

import ast
import json
import os
import re
import sys
import warnings
from dataclasses import dataclass, field
from fractions import Fraction

VERSION = "0.1.0"
TOOL = "typed-cov"

SKIPPED_FOLDERS = frozenset(
    {".venv", "venv", "__pycache__", ".git", "node_modules", "build", "dist", "site-packages"}
)

USAGE = (
    "usage: python3 typed_cov.py [folder] [--exclude <path>]... [--include-private] "
    "[--include-magic] [--strict-any] [--fail-under <percent>] [--json]"
)

PERCENT_RE = re.compile(r"[0-9]+(\.[0-9]+)?", re.ASCII)

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


class UsageError(Exception):
    """A command line that cannot be run; its text is printed on stderr."""


@dataclass
class Options:
    """The parsed command line."""

    folder: str = "."
    excludes: list[tuple[str, ...]] = field(default_factory=list)
    include_private: bool = False
    include_magic: bool = False
    strict_any: bool = False
    fail_under: Fraction | None = None
    fail_under_text: str = ""
    as_json: bool = False


@dataclass
class Missing:
    """One counted slot that has no annotation."""

    line: int
    kind: str
    name: str
    slot: str
    parameter: str | None


@dataclass
class FileReport:
    """The result for one file: its counts and its missing slots."""

    path: str
    annotated: int = 0
    counted: int = 0
    missing: list[Missing] = field(default_factory=list)


def parse_args(argv: list[str]) -> Options:
    """Turn the arguments into Options, or raise UsageError."""
    opts = Options()
    folder_seen = False
    fail_seen = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--exclude", "--fail-under"):
            if i + 1 >= len(argv):
                raise UsageError(f"option {arg} needs a value")
            value = argv[i + 1]
            i += 2
            if arg == "--exclude":
                parts = tuple(p for p in value.split("/") if p not in ("", "."))
                if not parts:
                    raise UsageError("--exclude needs a path that is not empty")
                opts.excludes.append(parts)
            else:
                if fail_seen:
                    raise UsageError("--fail-under was given twice")
                fail_seen = True
                if PERCENT_RE.fullmatch(value) is None:
                    raise UsageError(f"--fail-under: {value!r} is not a percentage (digits, optionally .digits)")
                try:
                    percent = Fraction(value)
                except ValueError:
                    raise UsageError(f"--fail-under: {value!r} is too long to be a percentage") from None
                if percent > 100:
                    raise UsageError(f"--fail-under: {value!r} is more than 100")
                opts.fail_under = percent
                opts.fail_under_text = value
            continue
        i += 1
        if arg == "--include-private":
            opts.include_private = True
        elif arg == "--include-magic":
            opts.include_magic = True
        elif arg == "--strict-any":
            opts.strict_any = True
        elif arg == "--json":
            opts.as_json = True
        elif arg.startswith("-"):
            raise UsageError(f"unknown option {arg!r}")
        else:
            if folder_seen:
                raise UsageError(f"more than one folder given: {arg!r}")
            folder_seen = True
            opts.folder = arg
    if not os.path.isdir(opts.folder):
        raise UsageError(f"not an existing folder: {opts.folder!r}")
    return opts


def is_excluded(parts: tuple[str, ...], excludes: list[tuple[str, ...]]) -> bool:
    """True when the relative path (as parts) is, or is under, an excluded path."""
    for ex in excludes:
        if parts[: len(ex)] == ex:
            return True
    return False


def find_files(folder: str, excludes: list[tuple[str, ...]], problems: list[tuple[str, str]]) -> list[str]:
    """Return the relative paths (with ``/``) of the files to read, sorted as strings."""
    found: list[str] = []
    stack: list[tuple[str, tuple[str, ...]]] = [(folder, ())]
    while stack:
        real, parts = stack.pop()
        try:
            with os.scandir(real) as it:
                entries = list(it)
        except OSError as exc:
            shown = "/".join(parts) if parts else "."
            problems.append((shown, f"{shown}:1:1: cannot read folder: {exc.strerror or exc}"))
            continue
        for entry in entries:
            name = entry.name
            child = parts + (name,)
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if name in SKIPPED_FOLDERS or name.startswith("."):
                        continue
                    if is_excluded(child, excludes):
                        continue
                    stack.append((entry.path, child))
                elif entry.is_file(follow_symlinks=False) and name.endswith(".py"):
                    if not is_excluded(child, excludes):
                        found.append("/".join(child))
            except OSError:
                continue
    found.sort()
    return found


def is_magic(name: str) -> bool:
    """A magic name: at least 5 characters, starting and ending with two underscores."""
    return len(name) >= 5 and name.startswith("__") and name.endswith("__")


def is_private(name: str) -> bool:
    """A private name: starts with an underscore and is not magic."""
    return name.startswith("_") and not is_magic(name)


def left_out(name: str, opts: Options) -> bool:
    """True when a function or class of this name is left out by the current options."""
    if is_magic(name):
        return not opts.include_magic
    if is_private(name):
        return not opts.include_private
    return False


def has_decorator(node: FunctionNode, names: tuple[str, ...], attributes: tuple[str, ...]) -> bool:
    """True when a decorator is one of the given names, or an attribute with one of the given names."""
    for deco in node.decorator_list:
        if isinstance(deco, ast.Name) and deco.id in names:
            return True
        if isinstance(deco, ast.Attribute) and deco.attr in attributes:
            return True
    return False


def always_left_out(node: FunctionNode) -> bool:
    """True for a function with an overload decorator or a property setter or deleter."""
    return has_decorator(node, ("overload",), ("overload", "setter", "deleter"))


def is_static(node: FunctionNode) -> bool:
    """True for a function decorated with ``staticmethod`` (a name, or an attribute of anything)."""
    return has_decorator(node, ("staticmethod",), ("staticmethod",))


def is_any(annotation: ast.expr) -> bool:
    """True when the annotation is the name ``Any`` or an attribute named ``Any``."""
    if isinstance(annotation, ast.Name):
        return annotation.id == "Any"
    return isinstance(annotation, ast.Attribute) and annotation.attr == "Any"


def child_blocks(node: ast.stmt) -> list[list[ast.stmt]]:
    """The statement blocks of a compound statement the walk goes into, in source order."""
    if isinstance(node, ast.If):
        return [node.body, node.orelse]
    if isinstance(node, (ast.Try, ast.TryStar)):
        blocks = [node.body]
        blocks.extend(handler.body for handler in node.handlers)
        blocks.append(node.orelse)
        blocks.append(node.finalbody)
        return blocks
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [node.body]
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        return [node.body, node.orelse]
    if isinstance(node, ast.Match):
        return [case.body for case in node.cases]
    return []


def collect(
    body: list[ast.stmt],
    prefix: str,
    in_class: bool,
    opts: Options,
    out: list[tuple[FunctionNode, str, str]],
) -> None:
    """Walk a block in source order, appending (node, kind, dotted name) for each counted function."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if always_left_out(node) or left_out(node.name, opts):
                continue
            out.append((node, "method" if in_class else "function", prefix + node.name))
        elif isinstance(node, ast.ClassDef):
            if left_out(node.name, opts):
                continue
            collect(node.body, prefix + node.name + ".", True, opts, out)
        else:
            for block in child_blocks(node):
                collect(block, prefix, in_class, opts, out)


def parameter_slots(node: FunctionNode, skip_first: bool) -> list[tuple[ast.arg, str]]:
    """The parameters of a function in signature order, each with its printed name."""
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    slots: list[tuple[ast.arg, str]] = []
    for index, arg in enumerate(positional):
        if skip_first and index == 0:
            continue
        slots.append((arg, arg.arg))
    if args.vararg is not None:
        slots.append((args.vararg, "*" + args.vararg.arg))
    for arg in args.kwonlyargs:
        slots.append((arg, arg.arg))
    if args.kwarg is not None:
        slots.append((args.kwarg, "**" + args.kwarg.arg))
    return slots


def analyse(tree: ast.Module, path: str, opts: Options) -> FileReport:
    """Count the slots of the functions of one parsed file."""
    report = FileReport(path)
    found: list[tuple[FunctionNode, str, str]] = []
    collect(tree.body, "", False, opts, found)
    for node, kind, name in found:
        is_method = kind == "method"
        skip_first = is_method and not is_static(node)
        entries: list[tuple[int, str, str | None, ast.expr | None]] = []
        for arg, shown in parameter_slots(node, skip_first):
            entries.append((arg.lineno, "parameter", shown, arg.annotation))
        if not (is_method and node.name == "__init__"):
            entries.append((node.lineno, "return", None, node.returns))
        for line, slot, parameter, annotation in entries:
            report.counted += 1
            if annotation is None or (opts.strict_any and is_any(annotation)):
                report.missing.append(Missing(line, kind, name, slot, parameter))
            else:
                report.annotated += 1
    return report


def parse_file(real_path: str, shown: str) -> ast.Module | str:
    """Parse one file; return the tree, or the one-line problem to print on stderr."""
    try:
        with open(real_path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return f"{shown}:1:1: cannot read file: {exc.strerror or exc}"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(data, shown)
    except SyntaxError as exc:
        line = exc.lineno if isinstance(exc.lineno, int) and exc.lineno else 1
        column = exc.offset if isinstance(exc.offset, int) and exc.offset else 1
        message = " ".join(str(exc.msg).split())
        return f"{shown}:{line}:{column}: {message}"
    except Exception as exc:  # not a SyntaxError: null bytes, recursion limits, ...
        message = " ".join(str(exc).split()) or type(exc).__name__
        return f"{shown}:1:1: {message}"


def tenths(annotated: int, counted: int) -> int | None:
    """The percentage in tenths, rounded down, or None when nothing was counted."""
    if counted == 0:
        return None
    return annotated * 1000 // counted


def plain_percent(value: int | None) -> str:
    """The percentage as printed in the plain form: one decimal and ``%``, or ``-``."""
    if value is None:
        return "-"
    return f"{value // 10}.{value % 10}%"


def json_percent(value: int | None) -> float | None:
    """The percentage as a JSON number, or None for null."""
    if value is None:
        return None
    return value / 10


def render_plain(reports: list[FileReport], annotated: int, counted: int) -> str:
    """The plain output: missing lines, one line per file, then the total."""
    lines: list[str] = []
    for report in reports:
        for item in report.missing:
            what = "return" if item.parameter is None else f"parameter {item.parameter}"
            lines.append(f"{report.path}:{item.line}: {item.kind} {item.name}: {what}")
    for report in reports:
        pct = plain_percent(tenths(report.annotated, report.counted))
        lines.append(f"{report.path}: {report.annotated}/{report.counted} {pct}")
    lines.append(f"total: {annotated}/{counted} {plain_percent(tenths(annotated, counted))}")
    return "\n".join(lines) + "\n"


def render_json(reports: list[FileReport], annotated: int, counted: int, ascii_only: bool) -> str:
    """The JSON document, followed by a newline."""
    doc = {
        "tool": TOOL,
        "version": VERSION,
        "total": {
            "annotated": annotated,
            "counted": counted,
            "percent": json_percent(tenths(annotated, counted)),
        },
        "files": [
            {
                "file": r.path,
                "annotated": r.annotated,
                "counted": r.counted,
                "percent": json_percent(tenths(r.annotated, r.counted)),
                "missing": [
                    {
                        "line": m.line,
                        "kind": m.kind,
                        "name": m.name,
                        "slot": m.slot,
                        "parameter": m.parameter,
                    }
                    for m in r.missing
                ],
            }
            for r in reports
        ],
    }
    return json.dumps(doc, indent=2, ensure_ascii=ascii_only) + "\n"


def write_stream(stream_name: str, text: str, errors: str) -> None:
    """Write text to stdout or stderr as UTF-8, whatever the locale."""
    stream = getattr(sys, stream_name)
    data = text.encode("utf-8", errors)
    buffer = getattr(stream, "buffer", None)
    try:
        if buffer is not None:
            stream.flush()
            buffer.write(data)
            buffer.flush()
        else:
            stream.write(data.decode("utf-8", "replace"))
            stream.flush()
    except BrokenPipeError:
        pass


def main(argv: list[str] | None = None) -> int:
    """Run typed-cov with the given arguments (default ``sys.argv[1:]``) and return the exit code."""
    args = sys.argv[1:] if argv is None else list(argv)
    try:
        opts = parse_args(args)
    except UsageError as exc:
        write_stream("stderr", f"typed-cov: error: {exc}\n{USAGE}\n", "backslashreplace")
        return 2

    problems: list[tuple[str, str]] = []
    paths = find_files(opts.folder, opts.excludes, problems)
    reports: list[FileReport] = []
    for path in paths:
        real = os.path.join(opts.folder, *path.split("/"))
        result = parse_file(real, path)
        if isinstance(result, str):
            problems.append((path, result))
            continue
        try:
            reports.append(analyse(result, path, opts))
        except RecursionError:
            problems.append((path, f"{path}:1:1: too deeply nested"))
    if problems:
        problems.sort(key=lambda item: item[0])
        write_stream("stderr", "".join(line + "\n" for _, line in problems), "backslashreplace")
        return 2

    annotated = sum(r.annotated for r in reports)
    counted = sum(r.counted for r in reports)
    if opts.as_json:
        try:
            text = render_json(reports, annotated, counted, False)
            text.encode("utf-8")
        except UnicodeEncodeError:
            text = render_json(reports, annotated, counted, True)
    else:
        text = render_plain(reports, annotated, counted)
    write_stream("stdout", text, "surrogateescape")

    if opts.fail_under is not None and counted > 0 and Fraction(annotated * 100) < opts.fail_under * counted:
        write_stream(
            "stderr",
            f"typed-cov: total coverage {plain_percent(tenths(annotated, counted))} "
            f"is below --fail-under {opts.fail_under_text}\n",
            "backslashreplace",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
