# typed-cov

**typed-cov** reports how much of a Python project has type hints, and exactly which parameter and which return value does not, with its file and line.

```sh
pipx run --spec git+https://github.com/tick-ai/typed-cov typed-cov
```

```console
$ typed-cov --fail-under 80
shop/cart.py:7: method Cart.add: parameter item
shop/cart.py:7: method Cart.add: return
shop/cart.py:11: function checkout: parameter *items
shop/util.py:3: function money: parameter **opts
shop/__init__.py: 0/0 -
shop/cart.py: 3/6 50.0%
shop/util.py: 3/4 75.0%
total: 6/10 60.0%
typed-cov: total coverage 60.0% is below --fail-under 80
$ echo $?
1
```

(From a clone, the same is `python3 typed_cov.py`.) It only parses files: it never imports or runs your code, needs nothing but Python 3.11, and gives the same answer on every run.

## Why it matters

Teams adopt a type checker one module at a time, and agents add code fast. Nobody can say how far the typing has got: which functions still take untyped parameters, which return values are not annotated, which files are done. typed-cov lists every gap as `file:line: kind name: slot`, per file and in total, so you can fix them. Every parameter and every return is counted by one stated rule below and nothing is guessed, so `--fail-under` can gate a pull request in CI while you adopt a type checker.

## Run it

Not on PyPI yet, so `pip install typed-cov` and `pipx install typed-cov` do not work today. Run it straight from GitHub:

```sh
pipx run --spec git+https://github.com/tick-ai/typed-cov typed-cov [folder] [options]
```

or from a clone:

```sh
git clone https://github.com/tick-ai/typed-cov
cd typed-cov
python3 typed_cov.py path/to/project
```

It is one file, `typed_cov.py`: Python 3.11, standard library only, no network.

```
python3 typed_cov.py [folder] [--exclude <path>]... [--include-private] [--include-magic] [--strict-any] [--fail-under <percent>] [--json]
```

## Which files are read

Starting at `folder` (default `.`), typed-cov walks every folder below it and reads each file whose name **ends in `.py`**, at any depth (`.hidden.py` is read). Skipped:

* a folder named `.venv`, `venv`, `__pycache__`, `.git`, `node_modules`, `build`, `dist` or `site-packages`, and any folder whose name starts with a dot, at any depth below `folder`, with everything under it (`folder` itself is never skipped, whatever its name);
* what `--exclude` names;
* symbolic links, to files and to folders: they are not followed and not read.

The **path** of a file is relative to `folder`, parts separated by `/`. Files are reported in the order of their paths as plain strings, code point by code point: `Zed.py`, `__init__.py`, `alpha.py`; and `pkg.py`, `pkg/a.py`, `pkg_a.py`.

Each file is read as bytes and parsed with `ast.parse`, so coding declarations, a byte-order mark and CRLF line endings are handled as Python handles them.

## What is counted

A file's **functions** are the `def` and `async def` statements found by walking its statements in source order. The walk goes into `if`/`elif`/`else`, `try`/`except`/`else`/`finally`, `with`, `for`, `while` and `match`/`case` blocks at any depth, and into the body of a class, so a function under `if TYPE_CHECKING:` or in a `try` is found. It does **not** go into the body of a function or a lambda: whatever is defined inside a function (a function, a class, a method of that class) is *nested* and never counted. Lambdas are never counted, and neither are classes: only the functions in them.

* A `def` that is not in a class body is a **function**; one in a class body (directly, or under an `if`/`try`... inside it, in a class at any depth) is a **method**. Async or not, the kind is the same.
* Its **name** is the dotted names of the classes around it and its own: `Outer.Inner.run`, or `run` at the top of a file.
* A function defined twice (both branches of an `if`) is counted twice.

### Slots

Each counted function has **slots**, in the order of its signature:

1. each positional-only parameter (before `/`);
2. each regular parameter;
3. the `*args` parameter, if any (a bare `*` is not a parameter);
4. each keyword-only parameter;
5. the `**kwargs` parameter, if any;
6. the **return**.

Two exceptions:

* **The first parameter of a method is left out**, whatever its name (`self`, `cls`, `mcs`, `x`): the first positional-only parameter if there is one, otherwise the first regular parameter. A method with no positional parameter (`def m(*args)`, `def m()`) leaves nothing out. A method decorated with `staticmethod` (the name `staticmethod`, or an attribute named `staticmethod` of any expression, such as `@builtins.staticmethod`) leaves nothing out: its first parameter is a slot. `classmethod`, property getters and every other method leave the first parameter out. A function that is not in a class leaves out no parameter, even one named `self`.
* **The return of `__init__` is left out**: a method named `__init__` has no return slot. Its parameters are slots like those of any method. A function named `__init__` outside a class has a return slot.

### What counts as annotated

A slot is **annotated** when its parameter has an annotation (`x: int`, `*args: str`, `**kw: Any`), or, for the return, when the `def` has a `->` annotation. **Any annotation expression counts**: `int`, `"Foo"` (a string, not parsed or judged), `None` (`-> None` is an annotation), `list[int]`, `int | None`, `Callable[..., Any]`, `"List[int]"`, a string of any content. A slot with no annotation is **missing**.

* A default value is not an annotation: `x=1` is missing, `x: int = 1` is annotated.
* A type comment (`# type: int`) is not an annotation.
* **`Any`** counts as an annotation unless `--strict-any` is given. With `--strict-any`, a slot whose annotation is, as an expression, the name `Any` or an attribute named `Any` of any expression (`x: Any`, `x: typing.Any`, `-> t.Any`) is missing. `"Any"` (a string), `Optional[Any]`, `list[Any]`, `Any | None` and every other expression are annotated whatever the option says.

### Left out by default

Judged on the function's own name (the last part), and on the name of every class around it:

* **magic** names, at least 5 characters, starting and ending with `__` (`__init__`, `__repr__`, `__getattr__`, `__x__`), unless `--include-magic`;
* **private** names, starting with `_` and not magic (`_helper`, `__mangled`, `__a_`, `___`), unless `--include-private`;
* a class that is left out leaves out **every function inside it**, whatever the names of the functions and of the classes between; when the option puts the class back, its methods are judged by their own names.

The names of files and folders (`_internal/`, `__init__.py`) do not matter: the functions of such a file are counted like any other. So `__init__` methods are left out unless `--include-magic` is given.

### Always left out

No option puts these back:

* a function with an `overload` decorator: the name `overload` or an attribute named `overload` of any expression (`@overload`, `@typing.overload`);
* a function with a property accessor decorator: an attribute named `setter` or `deleter` of any expression (`@value.setter`, `@pkg.value.deleter`). The `@property` getter is counted.

The implementation after some `@overload` stubs is an ordinary function and is counted. A decorator that is a call (`@overload()`) is not one of these.

### Not a finding

Functions in a skipped folder, an excluded path, a symbolic link or a file not named `*.py`; nested functions; lambdas; classes; private and magic functions unless asked for; overload stubs and property setters and deleters; the first parameter of a method (unless static); the return of `__init__`; variables, attributes and assignments of any kind (an unannotated `x = 1` is not counted); comments and type comments.

## The percentage

For a file and for the total over all files read: `annotated` is the number of counted slots that are annotated, `counted` the number of counted slots. The percentage is `annotated * 100 / counted` **rounded down** to one decimal, computed in integers as `annotated * 1000 // counted` tenths: 2 of 3 is `66.6`, 1 of 3 is `33.3`, 1999 of 2000 is `99.9`, and `100.0` only when every slot counted is annotated. With nothing counted there is no percentage: `-` in plain output, `null` in JSON.

`--fail-under <percent>` compares the **exact** ratio, not the rounded figure: it fails when something was counted and `annotated * 100 < percent * counted`. So 1999 of 2000 fails `--fail-under 100` and `99.96` and passes `99.95`; 2 of 3 passes `66.6` and fails `66.7`. With nothing counted it never fails.

## Options

| Option | Meaning |
| --- | --- |
| `folder` | The project's root; default `.`; at most one; must be an existing folder. |
| `--exclude <path>` | Leave out a file or folder and everything under it. `<path>` is relative to `folder`, written with `/`; empty and `.` parts are dropped (`./tests/` is `tests`); matched part by part from the start, so `tests` leaves out `<folder>/tests` and not `<folder>/pkg/tests`; no wildcards; a path that does not exist leaves out nothing; a path that is empty once those parts are dropped is a usage error. May be repeated. |
| `--include-private` | Count private functions. |
| `--include-magic` | Count magic functions. |
| `--strict-any` | An annotation that is only `Any` does not count as an annotation. |
| `--fail-under <percent>` | Exit 1 when the total is below `<percent>`: digits, optionally `.` and more digits (`80`, `92.5`, `100.0`), at most 100. Once only. |
| `--json` | Print the JSON document instead of the plain form. |

The flags take no value and may be given more than once. An option's value is always the next argument, whatever it looks like (`--exclude --json` excludes a path named `--json`). `--fail-under=80` is not a form (it is an unknown option). Arguments may come in any order. There is no other option, and no `--help`.

## Output

Output goes to stdout as UTF-8 whatever the locale, each line ended by `\n`. When the exit code is 0 nothing is written to stderr.

### Plain (default)

1. One line per **missing** slot, all files in path order; in a file, the functions in source order, and in a function its slots in signature order, the return last:
   * `<path>:<line>: <kind> <name>: parameter <p>` for a parameter,
   * `<path>:<line>: <kind> <name>: return` for the return,

   where `<kind>` is `function` or `method`, `<name>` is the dotted name, and `<p>` is the parameter's name, with its stars for the variadic ones (`x`, `*args`, `**kwargs`). `<line>` is the line of the parameter's name, or, for the return, the line of the word `def` (or `async`), never the line of a decorator. (The return is on the line of the `def`, so the lines are not always increasing within a function.)
2. One line per file read, **including files with nothing to count**: `<path>: <annotated>/<counted> <percentage>`.
3. The last line: `total: <annotated>/<counted> <percentage>`.

The percentage is written with exactly one decimal and a `%`, or `-` when nothing was counted.

Example. `m.py` is

```python
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
```

`load` has 6 slots (`path`, `mode`, `*rest`, `strict`, `**extra`, the return), 2 annotated; `Box.__init__` is magic and left out; `Box.grow` has 3 slots (`self` is left out; `by`, `extra`, the return), 1 annotated; `Box.make` has 2 slots (it is a static method, so `n` is a slot, and the return), both annotated. So 5 of 11:

```
m.py:1: function load: parameter path
m.py:1: function load: parameter *rest
m.py:1: function load: parameter strict
m.py:1: function load: parameter **extra
m.py:8: method Box.grow: parameter by
m.py:8: method Box.grow: return
m.py: 5/11 45.4%
total: 5/11 45.4%
```

With no `.py` file read the whole output is exactly `total: 0/0 -`. A file with nothing to count (an empty file, a file with no function, a file whose functions are all left out) has the line `<path>: 0/0 -` and no missing line.

### `--json`

One JSON document and `\n`:

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

(Cut to two `missing` entries; the real document holds all six, in the order of the plain lines.)

`files` has one object per file read, in path order, those with nothing to count too (`"annotated": 0, "counted": 0, "percent": null, "missing": []`). `kind` is `"function"` or `"method"`; `slot` is `"parameter"` or `"return"`; `parameter` is the string `<p>` of the plain line (with its stars) for a parameter and `null` for the return. With no file read the document is `{"tool": "typed-cov", "version": "0.1.0", "total": {"annotated": 0, "counted": 0, "percent": null}, "files": []}`. No other key is present; the options never change the shape.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Done, and `--fail-under` was not given or the total is not below it. Missing annotations alone are exit 0. |
| `1` | `--fail-under` was given, something was counted, and the total is below it. Stdout is as at 0; one line on stderr. |
| `2` | A usage error (unknown option, missing value, second folder, bad or repeated `--fail-under`, empty `--exclude`, folder that does not exist), or at least one file could not be parsed. Nothing on stdout. |

Usage errors are found before any file is read. A file that cannot be parsed (syntax error, unreadable or undecodable file) is reported on stderr as `<path>:<line>:<column>: <message>` (1-based, the `lineno` and `offset` of the `SyntaxError`; both are 1 where Python gives none), for every such file in path order, and the run exits 2 whatever else was found and whatever `--fail-under` says.

## In CI

```yaml
- name: Type hint coverage
  run: pipx run --spec git+https://github.com/tick-ai/typed-cov typed-cov --exclude tests --fail-under 80
```

The step fails when the total is below 80%, with the missing slots listed in the log. Raise the number as the typing grows; use `--json` to feed another tool.

## What it does not do

* It is **not a type checker**: it never judges whether an annotation is right, or even whether it is a type. `x: banana` and `x: "?"` count as annotated.
* It does not import, compile or run your code; it only parses it.
* It does not count nested functions, lambdas, classes, variables, attributes, comments or type comments.
* It does not read files whose name does not end in `.py` (no `.pyi`, no notebooks), and does not follow symbolic links.
* It has no wildcards in `--exclude`, no configuration file and no `--help`.
* It is not on PyPI yet.

## Tests

```sh
python3 -m unittest discover -s tests
```

## Where it came from

Built by agents on TickHub, as TickHub job `467b3fb2-5c7c-4521-8a39-a92c38cef875`. The job, the hand-ins and the receipt are on TickHub's public log: https://tick.company/hub/#/job/467b3fb2-5c7c-4521-8a39-a92c38cef875. `docs/the-job.md` is the job as it was given.

AI does the work. Tick makes it trustworthy. https://tick.company

## Licence

Apache-2.0, Copyright 2026 Parsa Esmaeili, see `LICENSE`.
