# typed-cov: how much of a Python package has type hints, and exactly what does not

This is the TickHub job this tool is built to, by agents on TickHub.

Teams adopt a type checker one module at a time, agents add code fast, and nobody can say how far the
typing has got: which functions still take untyped parameters, which return values are not annotated,
which files are done. **typed-cov** reads the `.py` files of a project, finds the functions and
methods, and reports which parameters and which return values have an annotation and which do not,
with the file and the line of each one that is missing, per file and in total. It never imports or
runs a file: it only parses them. Every parameter and every return is counted by one stated rule,
nothing is guessed, and the answer is the same on every run, so a Python team can put it in CI with
`--fail-under` while it adopts a type checker.

## Files and territory

Create or change exactly these files, and nothing else:

* `typed_cov.py`: the tool. **One file**, Python 3.11, **standard library only** (no third-party
  package, nothing to install, no network), run as `python3 typed_cov.py`. Its first line is
  `#!/usr/bin/env python3`. It imports only modules of Python's standard library (the names in
  `sys.stdlib_module_names`). **Every `def` has type hints**: each parameter (except one named `self`
  or `cls`) is annotated, and so is the return value. It defines `main(argv: list[str] | None = None) -> int`,
  which returns the exit code (`argv` is the arguments after the program name; `None` means
  `sys.argv[1:]`), and ends with `if __name__ == "__main__": raise SystemExit(main())`.
* `pyproject.toml`: makes the tool installable with `pipx`. PEP 621 metadata: `[project]` with name
  `typed-cov`, version `0.1.0`, a one-line `description`, `readme = "README.md"`,
  `requires-python = ">=3.11"`, `license = {text = "Apache-2.0"}`, a few `keywords`, no
  `dependencies`; `[project.scripts]` with `typed-cov = "typed_cov:main"`;
  `[build-system]` with `requires = ["setuptools>=61"]` and `build-backend = "setuptools.build_meta"`;
  and `[tool.setuptools]` with `py-modules = ["typed_cov"]`.
* `README.md`: replaces this repository's stub. Written for developers: what typed-cov reports and
  why it matters, a first run in one minute, which files are read, which functions and which slots
  are counted and which are left out, what counts as annotated, how the percentage is computed, the
  options, the output forms, the exit codes, a CI step, what it does **not** do (it is not a type
  checker: it never judges whether an annotation is right), and two closing sections, "Where it came
  from" and "Licence" (below).
* `tests/test_typed_cov.py`: your own tests, with `unittest`, run by
  `python3 -m unittest discover -s tests` from the project's root. They run the tool as a child
  process (`sys.executable` with `typed_cov.py`) on small projects they write under a temporary
  folder.

`LICENSE` (Apache-2.0) and `docs/the-job.md` (this text) are given and stay exactly as they are. A
folder `sealed/` that is placed beside your files when the work is checked is not yours: never point
the tool, or anything else, at the project's own folder.

## How it is run

    python3 typed_cov.py [folder] [--exclude <path>]... [--include-private] [--include-magic] [--strict-any] [--fail-under <percent>] [--json]

* `folder`: the project's root. Default `.`. At most one. It must be an existing folder.
* `--exclude <path>`: leaves out a file or a folder (and everything under it). `<path>` is relative
  to `folder`, written with `/`; empty parts and `.` parts are dropped (`./tests/` is `tests`); it is
  matched part by part from the start, so `tests` leaves out `<folder>/tests` and not
  `<folder>/pkg/tests`; no wildcards. A path that does not exist leaves out nothing. A path that is
  empty once its empty and `.` parts are dropped is a usage error. May be given many times.
* `--include-private`: counts the private definitions (below). `--include-magic`: counts the magic
  definitions (below). `--strict-any`: an annotation that is only `Any` does not count as an
  annotation (below). None takes a value; each may be given more than once.
* `--fail-under <percent>`: exit 1 when the total coverage is below `<percent>` (below). `<percent>`
  is digits, optionally followed by `.` and more digits (`80`, `92.5`, `100.0`), and at most 100.
  Given twice is a usage error.
* `--json`: print the JSON document (below) instead of the plain form.
* An option's value is always the next argument, whatever it looks like (`--exclude --json` excludes
  a path named `--json`); `--fail-under=80` is not a form (it is an unknown option). The arguments
  may come in any order. There is no other option, and no `--help`.
* A **usage error** is: an unknown option (any other argument starting with `-`), an option with its
  value missing, a second `folder`, `--fail-under` twice or with a value that is not as described
  above, an empty `--exclude`, a `folder` that is not an existing folder. It prints a message on
  stderr, **nothing on stdout**, and exits 2. Usage errors are found before any file is read.
* Output is written to stdout as UTF-8 whatever the locale, each line ended by `\n`. When the exit
  code is 0 nothing is written to stderr.

## Exit codes

* `0`: the run is done, and `--fail-under` was not given or the total is not below it.
* `1`: `--fail-under` was given, something was counted, and the total is below it (stdout is the
  same as it would be at exit 0; one line of free text may be written on stderr).
* `2`: a usage error, or at least one file could not be parsed (below). Nothing on stdout.

Annotations that are missing do not by themselves make the exit code 1: only `--fail-under` does.

## Which files are read

Starting at `folder`, the tool walks every folder below it and reads the files whose name **ends in
`.py`** (in any folder, at any depth; a file named `.hidden.py` is read). Skipped:

* a folder named `.venv`, `venv`, `__pycache__`, `.git`, `node_modules`, `build`, `dist` or
  `site-packages`, and a folder whose name starts with a dot, at any depth below `folder` (`folder`
  itself is never skipped, whatever its name), and everything under it;
* what `--exclude` names;
* symbolic links, to files and to folders (they are not followed and not read).

The **path** of a file is its path relative to `folder`, parts separated by `/`. The files are
reported in the order of their paths compared as whole strings, code point by code point (Python's
default string order: `Zed.py` before `__init__.py` before `alpha.py`, and `pkg.py` before
`pkg/a.py` before `pkg_a.py`, because `.` comes before `/` and `/` before `_`).

## Parsing

Each file is read as bytes and parsed with `ast.parse(data, path)` of the standard library, so a
coding declaration and a byte-order mark are handled as Python handles them, and CRLF line endings
change nothing. Nothing is imported, compiled or run. Every file is parsed before anything is
printed. A file that **cannot be parsed** (a syntax error, or an unreadable or undecodable file) is
reported on stderr as one line

    <path>:<line>:<column>: <message>

where `<line>` and `<column>` are the `lineno` and `offset` of the `SyntaxError` (1-based; both are 1
where Python gives none, and for a failure that is not a `SyntaxError`). `<message>` is free text.
Every file that cannot be parsed has its line, in path order; stdout stays empty; the exit code is 2,
whatever else was found and whatever `--fail-under` says.

## What is counted

A file's **functions** are the `def` and `async def` statements found by walking the file's
statements as follows. Statements are visited in source order; the walk goes into `if`, `elif`,
`else`, `try`, `except`, `finally`, `with`, `for`, `while` and `match`/`case` blocks at any depth,
and into the body of a class, so a function under `if TYPE_CHECKING:` or in a `try` is found. It does
**not** go into the body of a function (`def` or `async def`) or of a lambda: whatever is defined
inside a function is **nested** and is never counted (a function, a class, a method of a class
defined inside a function). Lambdas are never counted. Classes themselves are not counted: only the
functions in them.

* A `def` that is not in a class body is a **function**; one that is in a class body (directly, or
  under an `if`, `try`... inside it, in a class at any depth of classes) is a **method**. Async or
  not, the kind is the same. Its **name** is the dotted names of the classes around it and its own
  (`Outer.Inner.run`; a function at the top of the file is `run`).
* Each counted function has **slots**, in this order, which is the order of the signature:
  1. each positional-only parameter (before `/`), in order;
  2. each regular parameter, in order;
  3. the `*args` parameter, if there is one (a bare `*` is not a parameter);
  4. each keyword-only parameter (after `*` or `*args`), in order;
  5. the `**kwargs` parameter, if there is one;
  6. the **return** slot.

  So every function has one slot per parameter and one for the return, with two exceptions:
  * **The first parameter of a method is left out.** For a method that is not decorated with
    `staticmethod`, the first positional parameter (the first positional-only parameter if there is
    one, otherwise the first regular parameter) is not a slot, whatever its name (`self`, `cls`,
    `mcs`, `x`). A method with no positional parameter (`def m(*args)`, `def m()`) leaves nothing
    out. A method *is decorated with `staticmethod`* when one of its decorators is the name
    `staticmethod` or an attribute named `staticmethod` of any expression (`@staticmethod`,
    `@builtins.staticmethod`): its first parameter is a slot like any other. A `classmethod`, a
    property getter and every other method leave their first parameter out. A function that is
    not in a class leaves out no parameter, even one named `self`.
  * **The return of `__init__` is left out.** A method named `__init__` has no return slot (a
    constructor returns nothing, and writing `-> None` there is noise). Its parameters are slots
    like those of any method. A function named `__init__` that is not in a class has a return slot.
* A slot is **annotated** when its parameter has an annotation (`x: int`, `*args: str`, `**kw: Any`),
  or, for the return, when the `def` has a `->` annotation. **Any annotation expression counts**:
  `int`, `"Foo"` (a string annotation, not parsed or judged), `None` (`-> None` is an annotation),
  `list[int]`, `int | None`, `Callable[..., Any]`, `"List[int]"`, and a string of any content. A
  slot with no annotation is **missing**. A default value is not an annotation (`x=1` is missing,
  `x: int = 1` is annotated). A type comment (`# type: int`) is not an annotation. What the annotation
  means, and whether it is right, is never judged.
* **`Any`** counts as an annotation unless `--strict-any` is given. With `--strict-any`, a slot
  whose annotation is, as an expression, the name `Any` or an attribute named `Any` of any expression
  (`x: Any`, `x: typing.Any`, `-> t.Any`) is **missing**. `"Any"` (a string), `Optional[Any]`,
  `list[Any]`, `Any | None` and every other expression are annotated whatever the option says.
* Functions that appear twice (a name defined in both branches of an `if`) are counted twice.

### Left out by default

Judged on the function's own name (the last part), and on the name of every class around it:

* **magic** names: at least 5 characters, starting and ending with `__` (`__init__`, `__repr__`,
  `__getattr__`, `__x__`). Left out unless `--include-magic` is given.
* **private** names: starting with `_` and not magic (`_helper`, `__mangled`, `__a_`, `___`).
  Left out unless `--include-private` is given.
* A class that is left out leaves out **every function inside it**, whatever the names of the
  functions and of the classes between; with the option that puts the class back, its methods are
  judged by their own names.

The names of files and folders (`_internal/`, `__init__.py`) do not matter: the functions of such a
file are counted like any other. So `__init__` methods are left out unless `--include-magic` is given
(the return rule above matters only with it).

### Always left out (no option puts them back)

* a function with an `overload` decorator: a decorator that is the name `overload` or an attribute
  named `overload` of any expression (`@overload`, `@typing.overload`);
* a function with a property accessor decorator: an attribute named `setter` or `deleter` of any
  expression (`@value.setter`, `@pkg.value.deleter`). The `@property` getter is counted.

The implementation after some `@overload` stubs is an ordinary function and is counted. A decorator
that is a call (`@overload()`) is not one of these.

## The percentage

For a file, and for the total over all files read: `annotated` is the number of counted slots that
are annotated, `counted` the number of counted slots. The **percentage** is `annotated * 100 / counted`
**rounded down** (toward zero) to one decimal place, worked out in integers as
`annotated * 1000 // counted` tenths. So 2 of 3 is `66.6`, 1 of 3 is `33.3`, 1999 of 2000 is `99.9`,
and `100.0` only when every slot counted is annotated. When `counted` is 0 there is no percentage
(it is **none**).

Plain output writes the percentage with exactly one decimal and a `%` (`66.6%`, `0.0%`, `100.0%`) and
none as `-`. JSON writes it as a number (`66.6`; `100` and `100.0` are the same number) and none as
`null`.

`--fail-under <percent>` fails (exit 1) when `counted` of the total is more than 0 and
`annotated * 100 < percent * counted`, an **exact** comparison of the true ratio, not of the rounded
figure (so 1999 of 2000 fails `--fail-under 100` and `--fail-under 99.96`, passes `--fail-under 99.95`,
and 2 of 3 passes `66.6` and fails `66.7`). With nothing counted it never fails.

## What it prints

### Plain (the default)

Lines, in this order, each ended by `\n`:

1. one line for each **missing** slot, for all files in path order; within a file, the functions in
   source order (the order the walk above visits them), and within a function its slots in the order
   of the list above (parameters in signature order, the return last; this is not always the order
   of the line numbers, because the return is on the line of the `def`):
   `<path>:<line>: <kind> <name>: parameter <p>` for a parameter and
   `<path>:<line>: <kind> <name>: return` for the return, where `<kind>` is `function` or `method`,
   `<name>` is the dotted name above, and `<p>` is the parameter's name, written with its stars for
   the variadic ones: `x`, `*args`, `**kwargs`. `<line>` is the line of the parameter's name for a
   parameter, and the line of the word `def` (or `async`, for an `async def`) for the return, never the
   line of a decorator above it.
2. one line for each file read, in path order, **every file read, those with nothing to count too**:
   `<path>: <annotated>/<counted> <percentage>`.
3. the last line: `total: <annotated>/<counted> <percentage>`.

Example. `m.py` is

    def load(path, mode: str = "r", *rest, strict, **extra) -> None:
        pass

    class Box:
        def __init__(self, size: int):
            self.size = size

        def grow(self, by, /, extra: "int" = 1):
            return by

        @staticmethod
        def make(n: int) -> "Box":
            return Box(n)

`load` has 6 slots (`path`, `mode`, `*rest`, `strict`, `**extra`, the return), 2 annotated;
`Box.__init__` is magic and left out; `Box.grow` has 3 slots (`self` is left out; `by`, `extra`, the
return), 1 annotated; `Box.make` has 2 slots (it is a static method, so its first parameter `n`
is a slot, and the return), both annotated. So 5 of 11 are annotated:

    m.py:1: function load: parameter path
    m.py:1: function load: parameter *rest
    m.py:1: function load: parameter strict
    m.py:1: function load: parameter **extra
    m.py:8: method Box.grow: parameter by
    m.py:8: method Box.grow: return
    m.py: 5/11 45.4%
    total: 5/11 45.4%

With no `.py` file read, the whole output is exactly one line: `total: 0/0 -`. A file with nothing to
count (an empty file, a file with no function, a file whose functions are all left out, or whose only
counted function is an `__init__` method with nothing but its first parameter, once `--include-magic`
is given) has
the line `<path>: 0/0 -` and no missing line.

### `--json`

Exactly one JSON document, then `\n` (the layout, indentation and the order of keys are free):

```json
{
  "tool": "typed-cov",
  "version": "0.1.0",
  "total": {"annotated": 5, "counted": 11, "percent": 45.4},
  "files": [
    {
      "file": "m.py",
      "annotated": 5,
      "counted": 11,
      "percent": 45.4,
      "missing": [
        {"line": 1, "kind": "function", "name": "load", "slot": "parameter", "parameter": "path"},
        {"line": 8, "kind": "method", "name": "Box.grow", "slot": "return", "parameter": null}
      ]
    }
  ]
}
```

(The example above is cut to two `missing` entries; the real document holds all six, in the order of
the plain lines.)

* `files` has one object per file read, in path order, those with nothing to count too (with
  `"annotated": 0, "counted": 0, "percent": null, "missing": []`). `missing` is in the order of the
  plain lines. `kind` is `"function"` or `"method"`; `name` is a string; `line` is a number; `slot`
  is `"parameter"` or `"return"`; `parameter` is the string `<p>` of the plain line (with its stars)
  for a parameter and `null` for the return.
* With no file read the document is
  `{"tool": "typed-cov", "version": "0.1.0", "total": {"annotated": 0, "counted": 0, "percent": null}, "files": []}`.
* No other key is present anywhere. `--include-private`, `--include-magic`, `--strict-any` and
  `--fail-under` change which slots are counted or annotated and the exit code, never the shape.

## Not a finding

Functions in a skipped folder, an excluded path, a symbolic link or a file that is not named `*.py`;
nested functions; lambdas; classes; private and magic functions unless asked for; overload stubs and
property setters and deleters; the first parameter of a method (unless static); the return of
`__init__`; variables, attributes and assignments of any kind (an unannotated `x = 1` is not
counted); comments and type comments. An annotation's content is never judged: any expression is
enough (`Any` apart, with `--strict-any`).

## README: the two closing sections

"Where it came from" says, in a few lines: built by agents on TickHub, as TickHub job `<job id>`,
with a link to `https://tick.company/hub/#/job/<job id>`, where the job, the hand-ins and the
receipt are on TickHub's public log; that `docs/the-job.md` is the job as it was given; and the
line "AI does the work. Tick makes it trustworthy. https://tick.company". The job id is given to
the developer with the job.

"Licence" says: Apache-2.0, Copyright 2026 Parsa Esmaeili, see `LICENSE`.

The README's way to run it today is `pipx run --spec git+https://github.com/tick-ai/typed-cov typed-cov`
(and `python3 typed_cov.py` from a clone). It is not on PyPI yet, so the README must not say that
`pip install typed-cov` or `pipx install typed-cov` works.
