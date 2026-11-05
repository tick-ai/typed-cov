"""Tests for typed_cov.py: every run is a child process on a project written under a temp folder."""

import ast
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "typed_cov.py"


class Result:
    """What a run printed and its exit code."""

    def __init__(self, proc: "subprocess.CompletedProcess[bytes]") -> None:
        self.code = proc.returncode
        self.out = proc.stdout.decode("utf-8")
        self.err = proc.stderr.decode("utf-8")

    def lines(self) -> list[str]:
        return self.out.splitlines()

    def json(self) -> dict:
        return json.loads(self.out)


class Base(unittest.TestCase):
    """A temp project folder and helpers to fill and run it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()

    def write(self, rel: str, text: str | bytes = "") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
        return path

    def run_tool(self, *args: str, cwd: Path | None = None) -> Result:
        env = dict(os.environ, PYTHONIOENCODING="ascii", LC_ALL="C", PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, str(TOOL), *args],
            cwd=str(cwd or self.root),
            capture_output=True,
            env=env,
            timeout=60,
        )
        return Result(proc)

    def plain(self, source: str, *args: str) -> list[str]:
        self.write("m.py", source)
        res = self.run_tool(*args)
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.err, "")
        return res.lines()

    def missing(self, source: str, *args: str) -> list[str]:
        """The missing lines of one file m.py, without the 'm.py:' prefix."""
        return [ln[len("m.py:"):] for ln in self.plain(source, *args)[:-2]]

    def total(self, source: str, *args: str) -> str:
        return self.plain(source, *args)[-1]


class TestJobExample(Base):
    SRC = (
        'def load(path, mode: str = "r", *rest, strict, **extra) -> None:\n'
        "    pass\n"
        "\n"
        "class Box:\n"
        "    def __init__(self, size: int):\n"
        "        self.size = size\n"
        "\n"
        '    def grow(self, by, /, extra: "int" = 1):\n'
        "        return by\n"
        "\n"
        "    @staticmethod\n"
        '    def make(n: int) -> "Box":\n'
        "        return Box(n)\n"
    )

    def test_plain(self) -> None:
        self.assertEqual(
            self.plain(self.SRC),
            [
                "m.py:1: function load: parameter path",
                "m.py:1: function load: parameter *rest",
                "m.py:1: function load: parameter strict",
                "m.py:1: function load: parameter **extra",
                "m.py:8: method Box.grow: parameter by",
                "m.py:8: method Box.grow: return",
                "m.py: 5/11 45.4%",
                "total: 5/11 45.4%",
            ],
        )

    def test_json(self) -> None:
        self.write("m.py", self.SRC)
        res = self.run_tool("--json")
        self.assertEqual((res.code, res.err), (0, ""))
        self.assertTrue(res.out.endswith("}\n"))
        doc = res.json()
        self.assertEqual(set(doc), {"tool", "version", "total", "files"})
        self.assertEqual(doc["tool"], "typed-cov")
        self.assertEqual(doc["version"], "0.1.0")
        self.assertEqual(doc["total"], {"annotated": 5, "counted": 11, "percent": 45.4})
        self.assertEqual(len(doc["files"]), 1)
        f = doc["files"][0]
        self.assertEqual(set(f), {"file", "annotated", "counted", "percent", "missing"})
        self.assertEqual((f["file"], f["annotated"], f["counted"], f["percent"]), ("m.py", 5, 11, 45.4))
        self.assertEqual(
            f["missing"],
            [
                {"line": 1, "kind": "function", "name": "load", "slot": "parameter", "parameter": "path"},
                {"line": 1, "kind": "function", "name": "load", "slot": "parameter", "parameter": "*rest"},
                {"line": 1, "kind": "function", "name": "load", "slot": "parameter", "parameter": "strict"},
                {"line": 1, "kind": "function", "name": "load", "slot": "parameter", "parameter": "**extra"},
                {"line": 8, "kind": "method", "name": "Box.grow", "slot": "parameter", "parameter": "by"},
                {"line": 8, "kind": "method", "name": "Box.grow", "slot": "return", "parameter": None},
            ],
        )
        for entry in f["missing"]:
            self.assertEqual(set(entry), {"line", "kind", "name", "slot", "parameter"})
            self.assertIsInstance(entry["line"], int)

    def test_json_empty_cases(self) -> None:
        res = self.run_tool("--json")
        self.assertEqual(res.code, 0)
        self.assertEqual(
            res.json(),
            {"tool": "typed-cov", "version": "0.1.0",
             "total": {"annotated": 0, "counted": 0, "percent": None}, "files": []},
        )
        self.write("e.py", "")
        self.write("z.py", "def f(a: int) -> int: ...\n")
        res = self.run_tool("--json")
        doc = res.json()
        self.assertEqual(
            doc["files"][0],
            {"file": "e.py", "annotated": 0, "counted": 0, "percent": None, "missing": []},
        )
        self.assertEqual(doc["files"][1]["percent"], 100)
        self.assertEqual(doc["total"], {"annotated": 2, "counted": 2, "percent": 100.0})


class TestSlots(Base):
    def test_every_kind_of_parameter_in_signature_order(self) -> None:
        got = self.missing("def f(a, b, /, c, d=1, *args, e, g=2, **kw): pass\n")
        self.assertEqual(
            got,
            [f"1: function f: parameter {p}" for p in ("a", "b", "c", "d", "*args", "e", "g", "**kw")]
            + ["1: function f: return"],
        )

    def test_bare_star_and_slash_are_not_parameters(self) -> None:
        self.assertEqual(self.total("def f(a: int, /, *, b: int) -> int: ...\n"), "total: 3/3 100.0%")
        self.assertEqual(self.missing("def f(*, b): pass\n"), ["1: function f: parameter b", "1: function f: return"])

    def test_keyword_only_after_star_args(self) -> None:
        got = self.missing("def f(*args: int, k, **kw: int) -> None: ...\n")
        self.assertEqual(got, ["1: function f: parameter k"])

    def test_order_is_signature_order_not_line_order(self) -> None:
        src = (
            "def f(\n"
            "    a,\n"
            "    b: int,\n"
            "    *args,\n"
            "    k,\n"
            "    **kw\n"
            "):\n"
            "    pass\n"
        )
        self.assertEqual(
            self.missing(src),
            [
                "2: function f: parameter a",
                "4: function f: parameter *args",
                "5: function f: parameter k",
                "6: function f: parameter **kw",
                "1: function f: return",
            ],
        )

    def test_return_line_is_def_not_decorator(self) -> None:
        src = "@deco\n@deco2(1)\ndef f(): pass\n\n@d\nasync def g(): pass\n"
        self.assertEqual(self.missing(src), ["3: function f: return", "6: function g: return"])

    def test_function_named_self_keeps_its_first_parameter(self) -> None:
        self.assertEqual(self.missing("def f(self): pass\n"), ["1: function f: parameter self", "1: function f: return"])

    def test_method_first_parameter_left_out_whatever_the_name(self) -> None:
        src = (
            "class C:\n"
            "    def a(self): ...\n"
            "    def b(cls): ...\n"
            "    def c(mcs): ...\n"
            "    def d(x, y): ...\n"
            "    def e(x, /, y): ...\n"
            "    def f(*args): ...\n"
            "    def g(): ...\n"
            "    def h(*, k): ...\n"
            "    @classmethod\n"
            "    def i(cls, q): ...\n"
            "    @property\n"
            "    def j(self): ...\n"
        )
        got = self.missing(src)
        self.assertEqual(
            got,
            [
                "2: method C.a: return",
                "3: method C.b: return",
                "4: method C.c: return",
                "5: method C.d: parameter y",
                "5: method C.d: return",
                "6: method C.e: parameter y",
                "6: method C.e: return",
                "7: method C.f: parameter *args",
                "7: method C.f: return",
                "8: method C.g: return",
                "9: method C.h: parameter k",
                "9: method C.h: return",
                "11: method C.i: parameter q",
                "11: method C.i: return",
                "13: method C.j: return",
            ],
        )

    def test_staticmethod_keeps_its_first_parameter(self) -> None:
        src = (
            "import builtins\n"
            "class C:\n"
            "    @staticmethod\n"
            "    def a(x): ...\n"
            "    @builtins.staticmethod\n"
            "    def b(x): ...\n"
            "    @a.b.staticmethod\n"
            "    def c(x): ...\n"
            "    @staticmethod()\n"
            "    def d(x): ...\n"
            "    @other\n"
            "    @staticmethod\n"
            "    async def e(x, /): ...\n"
        )
        got = self.missing(src)
        self.assertEqual(
            got,
            [
                "4: method C.a: parameter x",
                "4: method C.a: return",
                "6: method C.b: parameter x",
                "6: method C.b: return",
                "8: method C.c: parameter x",
                "8: method C.c: return",
                "10: method C.d: return",
                "13: method C.e: parameter x",
                "13: method C.e: return",
            ],
        )

    def test_init_has_no_return_slot(self) -> None:
        src = "class C:\n    def __init__(self, a, b: int):\n        pass\n"
        self.assertEqual(self.missing(src, "--include-magic"), ["2: method C.__init__: parameter a"])
        self.assertEqual(self.total(src, "--include-magic"), "total: 1/2 50.0%")

    def test_init_with_nothing_but_self_has_nothing_to_count(self) -> None:
        got = self.plain("class C:\n    def __init__(self):\n        pass\n", "--include-magic")
        self.assertEqual(got, ["m.py: 0/0 -", "total: 0/0 -"])

    def test_init_outside_a_class_has_a_return_slot(self) -> None:
        got = self.plain("def __init__(a: int): ...\n", "--include-magic")
        self.assertEqual(got, ["m.py:1: function __init__: return", "m.py: 1/2 50.0%", "total: 1/2 50.0%"])

    def test_init_in_nested_class_and_async_init(self) -> None:
        src = "class A:\n    class B:\n        async def __init__(self, x): ...\n"
        self.assertEqual(self.missing(src, "--include-magic"), ["3: method A.B.__init__: parameter x"])

    def test_static_init_keeps_first_parameter_but_no_return(self) -> None:
        src = "class A:\n    @staticmethod\n    def __init__(x): ...\n"
        self.assertEqual(self.missing(src, "--include-magic"), ["3: method A.__init__: parameter x"])


class TestAnnotated(Base):
    def test_any_expression_counts(self) -> None:
        src = (
            "from typing import Callable\n"
            "def f(a: int, b: 'Foo', c: None, d: list[int], e: int | None, f: Callable[..., Any],\n"
            "      g: 'List[int]', h: '', i: 'not a type at all (', *j: str, k: int = 1, **l: object) -> 'x y': ...\n"
            "def g() -> None: ...\n"
        )
        self.assertEqual(self.total(src), "total: 14/14 100.0%")

    def test_default_is_not_an_annotation_and_type_comment_neither(self) -> None:
        src = "def f(x=1, y=None, z: int = 1):  # type: (int, int) -> None\n    pass\n"
        self.assertEqual(
            self.missing(src),
            ["1: function f: parameter x", "1: function f: parameter y", "1: function f: return"],
        )

    def test_type_comment_on_parameters_is_not_an_annotation(self) -> None:
        src = "def f(\n    a,  # type: int\n):\n    # type: (int) -> int\n    pass\n"
        self.assertEqual(self.missing(src), ["2: function f: parameter a", "1: function f: return"])

    def test_variables_attributes_and_assignments_are_not_counted(self) -> None:
        src = "x = 1\ny: int = 2\nclass C:\n    z = 3\n    w: int\n    def __init__(self):\n        self.a = 1\n"
        self.assertEqual(self.plain(src), ["m.py: 0/0 -", "total: 0/0 -"])

    def test_any_counts_by_default(self) -> None:
        src = "import typing as t\nfrom typing import Any\ndef f(a: Any, b: t.Any, *c: Any, **d: Any) -> Any: ...\n"
        self.assertEqual(self.total(src), "total: 5/5 100.0%")

    def test_strict_any(self) -> None:
        src = (
            "import typing as t\n"
            "from typing import Any, Optional\n"
            "def f(a: Any, b: t.Any, *c: Any, d: a.b.c.Any, **e: Any) -> t.Any: ...\n"
            "def g(a: 'Any', b: Optional[Any], c: list[Any], d: Any | None, e: Other, f: t.Anything, g: Any_) -> int: ...\n"
        )
        self.assertEqual(
            self.missing(src, "--strict-any"),
            [
                "3: function f: parameter a",
                "3: function f: parameter b",
                "3: function f: parameter *c",
                "3: function f: parameter d",
                "3: function f: parameter **e",
                "3: function f: return",
            ],
        )
        self.assertEqual(self.total(src, "--strict-any"), "total: 8/14 57.1%")
        self.assertEqual(self.total(src), "total: 14/14 100.0%")

    def test_strict_any_twice_is_fine(self) -> None:
        self.assertEqual(self.total("def f(a: Any): ...\n", "--strict-any", "--strict-any"), "total: 0/2 0.0%")

    def test_strict_any_does_not_count_missing_as_changed(self) -> None:
        self.assertEqual(self.total("def f(a): ...\n", "--strict-any"), "total: 0/2 0.0%")


class TestWalk(Base):
    def test_blocks_are_walked_nested_are_not(self) -> None:
        src = (
            "if X:\n"
            "    def a(): pass\n"
            "elif Y:\n"
            "    def b(): pass\n"
            "else:\n"
            "    def a(): pass\n"
            "try:\n"
            "    def c(): pass\n"
            "except E:\n"
            "    def d(): pass\n"
            "else:\n"
            "    def e(): pass\n"
            "finally:\n"
            "    def f(): pass\n"
            "with W:\n"
            "    def g(): pass\n"
            "for i in z:\n"
            "    def h(): pass\n"
            "else:\n"
            "    def i1(): pass\n"
            "while q:\n"
            "    def j(): pass\n"
            "else:\n"
            "    def j2(): pass\n"
            "match v:\n"
            "    case 1:\n"
            "        def k(): pass\n"
            "    case _:\n"
            "        def k2(): pass\n"
            "async def outer():\n"
            "    def nested(): pass\n"
            "    class N:\n"
            "        def m(self): pass\n"
            "    async with W:\n"
            "        def nested2(): pass\n"
            "f = lambda x: 0\n"
            "class C:\n"
            "    if X:\n"
            "        def meth(self): pass\n"
            "    try:\n"
            "        class Sub:\n"
            "            def deep(self): pass\n"
            "    finally:\n"
            "        pass\n"
            "    x = lambda y: 1\n"
        )
        got = self.missing(src)
        names = [ln.split(": ")[1] for ln in got]
        self.assertEqual(
            names,
            ["function a", "function b", "function a", "function c", "function d", "function e",
             "function f", "function g", "function h", "function i1", "function j", "function j2",
             "function k", "function k2", "function outer", "method C.meth", "method C.Sub.deep"],
        )
        lines = [int(ln.split(":")[0]) for ln in got]
        self.assertEqual(lines, sorted(lines))

    def test_try_star(self) -> None:
        src = "try:\n    pass\nexcept* ValueError:\n    def f(): ...\n"
        self.assertEqual(self.missing(src), ["4: function f: return"])

    def test_async_and_sync_kinds_and_dotted_names(self) -> None:
        src = (
            "class Outer:\n"
            "    class Inner:\n"
            "        async def run(self): ...\n"
            "    def go(self): ...\n"
            "async def top(): ...\n"
        )
        self.assertEqual(
            self.missing(src),
            [
                "3: method Outer.Inner.run: return",
                "4: method Outer.go: return",
                "5: function top: return",
            ],
        )

    def test_functions_defined_twice_are_counted_twice(self) -> None:
        src = "if X:\n    def f(a): ...\nelse:\n    def f(a): ...\n"
        self.assertEqual(self.total(src), "total: 0/4 0.0%")

    def test_file_order_inside_a_file_is_source_order(self) -> None:
        src = "def b(): ...\nclass C:\n    def m(self): ...\ndef a(): ...\n"
        self.assertEqual(
            self.missing(src),
            ["1: function b: return", "3: method C.m: return", "4: function a: return"],
        )


class TestLeftOut(Base):
    SRC = (
        "class Pub:\n"
        "    def _p(self): pass\n"
        "    def __m__(self): pass\n"
        "    def __mangled(self): pass\n"
        "    def __a_(self): pass\n"
        "    def ___(self): pass\n"
        "    def __x__(self): pass\n"
        "    def pub(self): pass\n"
        "def _helper(): pass\n"
        "def __init__(): pass\n"
        "def __repr__(): pass\n"
        "class _Priv:\n"
        "    def public(self): pass\n"
        "    class Deep:\n"
        "        def d(self): pass\n"
        "class __Magic__:\n"
        "    def mm(self): pass\n"
        "class Visible:\n"
        "    class _Hidden:\n"
        "        class Inner:\n"
        "            def x(self): pass\n"
        "    def shown(self): pass\n"
    )

    @staticmethod
    def names(lines: list[str]) -> list[str]:
        return [ln.split(": ")[1] for ln in lines if ln.endswith(": return")]

    def test_default(self) -> None:
        got = self.plain(self.SRC)
        self.assertEqual(self.names(got), ["method Pub.pub", "method Visible.shown"])

    def test_include_private(self) -> None:
        got = self.plain(self.SRC, "--include-private")
        self.assertEqual(
            self.names(got),
            [
                "method Pub._p", "method Pub.__mangled", "method Pub.__a_", "method Pub.___",
                "method Pub.pub", "function _helper", "method _Priv.public", "method _Priv.Deep.d",
                "method Visible._Hidden.Inner.x", "method Visible.shown",
            ],
        )

    def test_include_magic(self) -> None:
        got = self.plain(self.SRC, "--include-magic")
        self.assertEqual(
            self.names(got),
            [
                "method Pub.__m__", "method Pub.__x__", "method Pub.pub", "function __init__",
                "function __repr__", "method __Magic__.mm", "method Visible.shown",
            ],
        )

    def test_both(self) -> None:
        got = self.plain(self.SRC, "--include-magic", "--include-private", "--include-magic")
        self.assertEqual(len(self.names(got)), 15)

    def test_put_back_class_judges_methods_by_their_own_names(self) -> None:
        src = "class _C:\n    def _a(self): ...\n    def b(self): ...\n"
        self.assertEqual(self.names(self.plain(src, "--include-private")), ["method _C._a", "method _C.b"])
        src = "class _C:\n    def __a__(self): ...\n    def b(self): ...\n"
        self.assertEqual(self.names(self.plain(src, "--include-private")), ["method _C.b"])

    def test_magic_needs_five_characters(self) -> None:
        # '____' is 4 characters: private, not magic; '_____' is magic.
        src = "def ____(): ...\ndef _____(): ...\ndef __ab__(): ...\n"
        self.assertEqual(self.names(self.plain(src)), [])
        self.assertEqual(self.names(self.plain(src, "--include-private")), ["function ____"])
        self.assertEqual(self.names(self.plain(src, "--include-magic")), ["function _____", "function __ab__"])

    def test_names_of_files_and_folders_do_not_matter(self) -> None:
        self.write("_internal/__init__.py", "def f(): ...\n")
        self.write("__main__.py", "def g(): ...\n")
        res = self.run_tool()
        self.assertEqual(
            res.lines(),
            [
                "__main__.py:1: function g: return",
                "_internal/__init__.py:1: function f: return",
                "__main__.py: 0/1 0.0%",
                "_internal/__init__.py: 0/1 0.0%",
                "total: 0/2 0.0%",
            ],
        )

    def test_overload_setter_deleter_never_count(self) -> None:
        src = (
            "import typing\n"
            "from typing import overload\n"
            "class C:\n"
            "    @overload\n"
            "    def f(self, a): ...\n"
            "    @typing.overload\n"
            "    def f(self, a, b): ...\n"
            "    def f(self, *a): ...\n"
            "    @property\n"
            "    def value(self): ...\n"
            "    @value.setter\n"
            "    def value(self, v): ...\n"
            "    @pkg.value.deleter\n"
            "    def value(self): ...\n"
            "@overload\n"
            "def g(a): ...\n"
            "@a.b.overload\n"
            "async def g(a): ...\n"
            "def g(a): ...\n"
        )
        for extra in ([], ["--include-private", "--include-magic", "--strict-any"]):
            self.assertEqual(
                self.missing(src, *extra),
                [
                    "8: method C.f: parameter *a",
                    "8: method C.f: return",
                    "10: method C.value: return",
                    "19: function g: parameter a",
                    "19: function g: return",
                ],
            )

    def test_decorators_that_only_look_like_them_count(self) -> None:
        src = (
            "@overload()\n"
            "def a(): ...\n"
            "@setter\n"
            "def b(): ...\n"
            "@x.setter()\n"
            "def c(): ...\n"
            "@x.overloaded\n"
            "def d(): ...\n"
            "@deleter\n"
            "def e(): ...\n"
        )
        self.assertEqual(len(self.missing(src)), 5)


class TestPercentAndFailUnder(Base):
    def test_rounded_down_to_one_decimal(self) -> None:
        # 2 of 3, 1 of 3
        self.assertEqual(self.plain("def f(a: int, b): ...\n")[-1], "total: 1/3 33.3%")
        self.assertEqual(self.plain("def f(a: int, b) -> int: ...\n")[-1], "total: 2/3 66.6%")
        self.assertEqual(self.plain("def f(a: int) -> int: ...\n")[-1], "total: 2/2 100.0%")
        self.assertEqual(self.plain("def f(a): ...\n")[-1], "total: 0/2 0.0%")

    def big(self, missing: int = 1) -> None:
        funcs = [f"def f{i}(a: int) -> int: ...\n" for i in range(1000)]
        for i in range(missing):
            funcs[i] = f"def f{i}(a) -> int: ...\n"
        self.write("m.py", "".join(funcs))

    def test_1999_of_2000(self) -> None:
        self.big()
        res = self.run_tool()
        self.assertEqual(res.code, 0)
        self.assertEqual(res.lines()[-1], "total: 1999/2000 99.9%")
        self.assertEqual(res.lines()[-2], "m.py: 1999/2000 99.9%")
        self.assertEqual(self.run_tool("--json").json()["total"]["percent"], 99.9)

    def test_fail_under_is_exact(self) -> None:
        self.big()
        for value, code in (("100", 1), ("100.0", 1), ("99.96", 1), ("99.95", 0), ("99.9", 0), ("0", 0)):
            res = self.run_tool("--fail-under", value)
            self.assertEqual(res.code, code, value)
            self.assertEqual(res.lines()[-1], "total: 1999/2000 99.9%")
            if code == 1:
                self.assertEqual(len(res.err.strip().splitlines()), 1)
            else:
                self.assertEqual(res.err, "")

    def test_fail_under_two_thirds(self) -> None:
        self.write("m.py", "def f(a: int, b) -> int: ...\n")
        self.assertEqual(self.run_tool("--fail-under", "66.6").code, 0)
        self.assertEqual(self.run_tool("--fail-under", "66.7").code, 1)
        self.assertEqual(self.run_tool("--fail-under", "66.66666").code, 0)
        self.assertEqual(self.run_tool("--fail-under", "66.66667").code, 1)

    def test_fail_under_exit_1_keeps_stdout_and_json(self) -> None:
        self.write("m.py", "def f(a): ...\n")
        plain0 = self.run_tool()
        plain1 = self.run_tool("--fail-under", "50")
        self.assertEqual((plain0.code, plain1.code), (0, 1))
        self.assertEqual(plain0.out, plain1.out)
        j0, j1 = self.run_tool("--json"), self.run_tool("--json", "--fail-under", "50")
        self.assertEqual((j0.code, j1.code), (0, 1))
        self.assertEqual(j0.out, j1.out)

    def test_fail_under_boundaries(self) -> None:
        self.write("m.py", "def f(a: int) -> int: ...\n")
        for value in ("0", "00", "100", "100.0", "100.000", "0.0", "99.99999999999999999999", "007"):
            self.assertEqual(self.run_tool("--fail-under", value).code, 0, value)
        self.write("m.py", "def f(a: int) -> int: ...\ndef g(a): ...\n")
        self.assertEqual(self.run_tool("--fail-under", "50").code, 0)
        self.assertEqual(self.run_tool("--fail-under", "50.0000001").code, 1)

    def test_fail_under_with_nothing_counted_never_fails(self) -> None:
        self.assertEqual(self.run_tool("--fail-under", "100").code, 0)
        self.write("e.py", "x = 1\n")
        res = self.run_tool("--fail-under", "100")
        self.assertEqual((res.code, res.err), (0, ""))
        self.assertEqual(res.lines(), ["e.py: 0/0 -", "total: 0/0 -"])

    def test_missing_annotations_alone_exit_0(self) -> None:
        self.write("m.py", "def f(a): ...\n")
        res = self.run_tool()
        self.assertEqual((res.code, res.err), (0, ""))

    def test_fail_under_bad_values_are_usage_errors(self) -> None:
        self.write("m.py", "def f(a): ...\n")
        for value in ("100.1", "101", "abc", "", "-1", "+1", "1e2", ".5", "5.", "80%", " 80", "80 ", "1,5",
                      "0x10", "\u0663", "1_0", "inf", "nan", "100.0000001", "9" * 5000):
            res = self.run_tool("--fail-under", value)
            self.assertEqual((res.code, res.out), (2, ""), repr(value))
            self.assertNotEqual(res.err, "")

    def test_fail_under_twice_missing_value_and_equals_form(self) -> None:
        self.write("m.py", "def f(a): ...\n")
        for args in (
            ["--fail-under", "10", "--fail-under", "10"],
            ["--fail-under", "x", "--fail-under", "10"],
            ["--fail-under"],
            ["--fail-under=80"],
            ["--fail-under=80", "--json"],
        ):
            res = self.run_tool(*args)
            self.assertEqual((res.code, res.out), (2, ""), args)
            self.assertNotEqual(res.err, "")

    def test_fail_under_value_may_look_like_an_option(self) -> None:
        res = self.run_tool("--fail-under", "--json")
        self.assertEqual((res.code, res.out), (2, ""))

    def test_per_file_percent(self) -> None:
        self.write("a.py", "def f(a: int, b): ...\n")
        self.write("b.py", "def g(a: int, b: int) -> int: ...\n")
        self.assertEqual(
            self.run_tool().lines(),
            [
                "a.py:1: function f: parameter b",
                "a.py:1: function f: return",
                "a.py: 1/3 33.3%",
                "b.py: 3/3 100.0%",
                "total: 4/6 66.6%",
            ],
        )


class TestFiles(Base):
    def test_no_files(self) -> None:
        res = self.run_tool()
        self.assertEqual((res.code, res.out, res.err), (0, "total: 0/0 -\n", ""))

    def test_files_without_anything_to_count(self) -> None:
        self.write("a_empty.py", "")
        self.write("b_comment.py", "# nothing\n")
        self.write("c_nofunc.py", "x = 1\nclass C: pass\n")
        self.write("d_private.py", "def _f(a): ...\n")
        res = self.run_tool()
        self.assertEqual(
            res.lines(),
            ["a_empty.py: 0/0 -", "b_comment.py: 0/0 -", "c_nofunc.py: 0/0 -", "d_private.py: 0/0 -", "total: 0/0 -"],
        )

    def test_order_is_by_path_as_strings(self) -> None:
        for name in ("pkg_a.py", "pkg/a.py", "pkg.py", "alpha.py", "__init__.py", "Zed.py", "pkg/B.py", "pkg-x.py"):
            self.write(name, "")
        got = [ln.split(":")[0] for ln in self.run_tool().lines()[:-1]]
        self.assertEqual(
            got,
            ["Zed.py", "__init__.py", "alpha.py", "pkg-x.py", "pkg.py", "pkg/B.py", "pkg/a.py", "pkg_a.py"],
        )

    def test_missing_lines_follow_file_order_and_come_before_file_lines(self) -> None:
        self.write("b.py", "def f(): ...\n")
        self.write("a/z.py", "def g(): ...\n")
        self.write("a.py", "def h(): ...\n")
        self.assertEqual(
            self.run_tool().lines(),
            [
                "a.py:1: function h: return",
                "a/z.py:1: function g: return",
                "b.py:1: function f: return",
                "a.py: 0/1 0.0%",
                "a/z.py: 0/1 0.0%",
                "b.py: 0/1 0.0%",
                "total: 0/3 0.0%",
            ],
        )

    def test_only_names_ending_in_py(self) -> None:
        self.write("a.py", "")
        self.write(".hidden.py", "")
        self.write("a.pyi", "def f(): ...\n")
        self.write("a.pyc", b"\x00")
        self.write("a.py.txt", "def f(: \n")
        self.write("py", "")
        self.write("script", "def f(): ...\n")
        self.write("A.PY", "def f(): ...\n")
        (self.root / "dir.py").mkdir()
        self.write("dir.py/inner.py", "")
        got = [ln.split(":")[0] for ln in self.run_tool().lines()[:-1]]
        self.assertEqual(got, [".hidden.py", "a.py", "dir.py/inner.py"])

    def test_skipped_folders_at_any_depth(self) -> None:
        for d in (".venv", "venv", "__pycache__", ".git", "node_modules", "build", "dist", "site-packages",
                  ".mypy_cache", ".x", "pkg/.venv", "pkg/build", "pkg/.hidden/deep", "pkg/sub/dist"):
            self.write(f"{d}/bad.py", "def f(: broken\n")
        for d in ("venv2", "builds", "Build", "distro", "my.venv", "src/lib"):
            self.write(f"{d}/ok.py", "")
        got = [ln.split(":")[0] for ln in self.run_tool().lines()[:-1]]
        self.assertEqual(got, ["Build/ok.py", "builds/ok.py", "distro/ok.py", "my.venv/ok.py", "src/lib/ok.py", "venv2/ok.py"])

    def test_folder_itself_is_never_skipped(self) -> None:
        for name in (".venv", "build", ".dot"):
            folder = self.root / name
            folder.mkdir()
            (folder / "a.py").write_text("def f(): ...\n")
            res = self.run_tool(str(folder), cwd=self.root.parent)
            self.assertEqual(res.lines()[0], "a.py:1: function f: return")
            res = self.run_tool(name)
            self.assertEqual(res.lines()[-1], "total: 0/1 0.0%")

    def test_folder_argument_and_default(self) -> None:
        self.write("sub/a.py", "def f(): ...\n")
        res = self.run_tool("sub")
        self.assertEqual(res.lines(), ["a.py:1: function f: return", "a.py: 0/1 0.0%", "total: 0/1 0.0%"])
        res = self.run_tool(".", cwd=self.root / "sub")
        self.assertEqual(res.lines(), ["a.py:1: function f: return", "a.py: 0/1 0.0%", "total: 0/1 0.0%"])
        res = self.run_tool(cwd=self.root / "sub")
        self.assertEqual(res.lines()[-1], "total: 0/1 0.0%")
        res = self.run_tool(str(self.root) + "/sub/")
        self.assertEqual(res.lines()[-1], "total: 0/1 0.0%")

    def test_folder_may_come_in_any_position(self) -> None:
        self.write("sub/a.py", "def f(a: int) -> int: ...\n")
        res = self.run_tool("--json", "--exclude", "x", "sub", "--strict-any")
        self.assertEqual(res.code, 0)
        self.assertEqual(res.json()["files"][0]["file"], "a.py")

    def test_symbolic_links_are_not_followed_or_read(self) -> None:
        self.write("real/a.py", "def f(): ...\n")
        self.write("target.py", "def g(): ...\n")
        try:
            os.symlink(self.root / "real", self.root / "linkdir")
            os.symlink(self.root / "target.py", self.root / "link.py")
            os.symlink(self.root / "nowhere.py", self.root / "dangling.py")
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available")
        res = self.run_tool()
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(
            res.lines(),
            [
                "real/a.py:1: function f: return",
                "target.py:1: function g: return",
                "real/a.py: 0/1 0.0%",
                "target.py: 0/1 0.0%",
                "total: 0/2 0.0%",
            ],
        )

    def test_symbolic_link_as_the_folder_argument_is_the_folder(self) -> None:
        self.write("real/a.py", "")
        try:
            os.symlink(self.root / "real", self.root / "link")
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available")
        res = self.run_tool("link")
        self.assertEqual(res.lines(), ["a.py: 0/0 -", "total: 0/0 -"])

    def test_non_ascii_names(self) -> None:
        self.write("caf\u00e9/\u65e5\u672c.py", "def f(): ...\n")
        self.write("h\u00e9llo.py", "def g(a): ...\n")
        res = self.run_tool()
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(
            res.lines(),
            [
                "caf\u00e9/\u65e5\u672c.py:1: function f: return",
                "h\u00e9llo.py:1: function g: parameter a",
                "h\u00e9llo.py:1: function g: return",
                "caf\u00e9/\u65e5\u672c.py: 0/1 0.0%",
                "h\u00e9llo.py: 0/2 0.0%",
                "total: 0/3 0.0%",
            ],
        )
        res = self.run_tool("--json")
        self.assertEqual([f["file"] for f in res.json()["files"]], ["caf\u00e9/\u65e5\u672c.py", "h\u00e9llo.py"])
        res = self.run_tool("--exclude", "caf\u00e9")
        self.assertEqual(res.lines()[-1], "total: 0/2 0.0%")

    def test_non_ascii_names_of_functions_and_parameters(self) -> None:
        self.write("m.py", "def f\u00fc(\u00e4, \u03b2: int): ...\n")
        got = self.run_tool().lines()
        self.assertEqual(got[0], "m.py:1: function f\u00fc: parameter \u00e4")

    def test_output_is_utf8_whatever_the_locale(self) -> None:
        self.write("\u00e9.py", "def f(): ...\n")
        env = dict(os.environ, LC_ALL="C", PYTHONIOENCODING="latin-1", PYTHONUTF8="0")
        proc = subprocess.run([sys.executable, str(TOOL)], cwd=str(self.root), capture_output=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("\u00e9.py:1: function f: return\n".encode("utf-8"), proc.stdout)

    def test_same_answer_every_run(self) -> None:
        for i in range(5):
            self.write(f"p{i}/m{i}.py", "def f(a, b: int): ...\nclass C:\n    def g(self, x): ...\n")
        first = self.run_tool()
        for _ in range(3):
            self.assertEqual(self.run_tool().out, first.out)


class TestExclude(Base):
    def setUp(self) -> None:
        super().setUp()
        self.write("a.py", "")
        self.write("tests/t.py", "def f(): ...\n")
        self.write("tests/sub/u.py", "")
        self.write("pkg/tests/v.py", "")
        self.write("pkg/m.py", "")
        self.write("pkg/m2.py", "")

    def files(self, *args: str) -> list[str]:
        res = self.run_tool(*args)
        self.assertEqual((res.code, res.err), (0, ""), res.err)
        return [ln.split(": ")[0] for ln in res.lines()[:-1] if ln.count(": ") == 1 and ln.split(": ")[1][0].isdigit()]

    def test_folder_is_matched_from_the_start(self) -> None:
        self.assertEqual(self.files("--exclude", "tests"), ["a.py", "pkg/m.py", "pkg/m2.py", "pkg/tests/v.py"])

    def test_forms_of_the_same_path(self) -> None:
        want = ["a.py", "pkg/m.py", "pkg/m2.py", "pkg/tests/v.py"]
        for form in ("./tests/", "tests/", "./tests", "tests//", ".//tests/./", "./././tests"):
            self.assertEqual(self.files("--exclude", form), want, form)

    def test_nested_and_file_paths(self) -> None:
        self.assertEqual(self.files("--exclude", "pkg/tests"), ["a.py", "pkg/m.py", "pkg/m2.py", "tests/sub/u.py", "tests/t.py"])
        self.assertEqual(self.files("--exclude", "pkg/m.py"), ["a.py", "pkg/m2.py", "pkg/tests/v.py", "tests/sub/u.py", "tests/t.py"])
        self.assertEqual(self.files("--exclude", "tests/sub"), ["a.py", "pkg/m.py", "pkg/m2.py", "pkg/tests/v.py", "tests/t.py"])

    def test_parts_not_prefixes_of_names(self) -> None:
        self.assertEqual(len(self.files("--exclude", "pkg/m")), 6)
        self.assertEqual(len(self.files("--exclude", "tes")), 6)
        self.assertEqual(len(self.files("--exclude", "a")), 6)

    def test_many_and_nonexistent(self) -> None:
        self.assertEqual(self.files("--exclude", "tests", "--exclude", "pkg", "--exclude", "nope/x"), ["a.py"])
        self.assertEqual(len(self.files("--exclude", "nope")), 6)
        self.assertEqual(len(self.files("--exclude", "a.py/x")), 6)

    def test_no_wildcards(self) -> None:
        self.assertEqual(len(self.files("--exclude", "*")), 6)
        self.assertEqual(len(self.files("--exclude", "t*")), 6)
        self.assertEqual(len(self.files("--exclude", "**/*.py")), 6)

    def test_excluded_unparseable_file_is_not_read(self) -> None:
        self.write("tests/bad.py", "def (:\n")
        res = self.run_tool("--exclude", "tests")
        self.assertEqual(res.code, 0)

    def test_value_is_always_the_next_argument(self) -> None:
        self.write("--json/w.py", "def x(): ...\n")
        res = self.run_tool("--exclude", "--json")
        self.assertEqual(res.code, 0, res.err)
        self.assertNotIn("w.py", res.out)
        self.assertFalse(res.out.startswith("{"))
        res = self.run_tool("--exclude", "--json", "--json")
        self.assertTrue(res.out.startswith("{"))
        res = self.run_tool("--json")
        self.assertIn("--json/w.py", res.out)

    def test_exclude_changes_the_total(self) -> None:
        self.assertEqual(self.run_tool().lines()[-1], "total: 0/1 0.0%")
        self.assertEqual(self.run_tool("--exclude", "tests").lines()[-1], "total: 0/0 -")

    def test_empty_exclude_is_a_usage_error(self) -> None:
        for value in ("", ".", "./", "/", "//", "./.", "././/./"):
            res = self.run_tool("--exclude", value)
            self.assertEqual((res.code, res.out), (2, ""), repr(value))
            self.assertNotEqual(res.err, "")

    def test_exclude_without_value(self) -> None:
        res = self.run_tool("--exclude")
        self.assertEqual((res.code, res.out), (2, ""))
        res = self.run_tool("--json", "--exclude")
        self.assertEqual((res.code, res.out), (2, ""))


class TestUsageErrors(Base):
    def setUp(self) -> None:
        super().setUp()
        # An unparseable file proves usage errors are found before any file is read.
        self.write("bad.py", "def (:\n")

    def check(self, *args: str) -> None:
        res = self.run_tool(*args)
        self.assertEqual(res.out, "", args)
        self.assertEqual(res.code, 2, args)
        self.assertNotEqual(res.err.strip(), "", args)
        self.assertNotIn("bad.py", res.err, args)

    def test_unknown_options(self) -> None:
        for arg in ("--help", "-h", "-", "--", "-x", "--unknown", "--json=1", "--include-private=1", "--strict_any",
                    "--Json", "--exclude=tests", "-j", "---json"):
            self.check(arg)

    def test_unknown_option_after_good_ones(self) -> None:
        self.check("--json", "--nope")
        self.check(".", "--nope")

    def test_two_folders(self) -> None:
        (self.root / "other").mkdir()
        self.check(".", "other")
        self.check("other", "--json", ".")

    def test_bad_folders(self) -> None:
        self.write("file.txt", "x")
        self.check("nope")
        self.check("file.txt")
        self.check("")
        self.check("--json", "missing/dir")

    def test_flags_take_no_value(self) -> None:
        # a value after a flag is taken as the folder, and here it does not exist
        self.check("--json", "yes")
        self.check("--strict-any", "yes")

    def test_message_is_on_stderr_only_and_no_traceback(self) -> None:
        res = self.run_tool("--nope")
        self.assertNotIn("Traceback", res.err)
        self.assertIn("--nope", res.err)


class TestParseErrors(Base):
    def test_syntax_error_position_and_exit(self) -> None:
        self.write("ok.py", "def f(a): ...\n")
        self.write("bad.py", "x = 1\ndef f(:\n    pass\n")
        res = self.run_tool()
        self.assertEqual(res.code, 2)
        self.assertEqual(res.out, "")
        lines = res.err.splitlines()
        self.assertEqual(len(lines), 1)
        try:
            ast.parse("x = 1\ndef f(:\n    pass\n")
        except SyntaxError as exc:
            self.assertEqual((exc.lineno, exc.offset), (2, 7))
        self.assertTrue(lines[0].startswith("bad.py:2:7: "), lines[0])

    def test_every_bad_file_in_path_order_and_nothing_on_stdout(self) -> None:
        self.write("z.py", "def f(:\n")
        self.write("a/b.py", "x = = 1\n")
        self.write("a.py", "class\n")
        self.write("good.py", "def g(): ...\n")
        res = self.run_tool("--json", "--fail-under", "100")
        self.assertEqual((res.code, res.out), (2, ""))
        lines = res.err.splitlines()
        self.assertEqual([ln.split(":")[0] for ln in lines], ["a.py", "a/b.py", "z.py"])
        for ln in lines:
            path, line, column, message = ln.split(":", 3)
            self.assertTrue(line.isdigit() and column.isdigit() and int(line) >= 1 and int(column) >= 1, ln)
            self.assertTrue(message.strip())

    def test_line_and_column_are_those_of_the_syntax_error(self) -> None:
        self.write("m.py", "def ok(): ...\n\nif True:\nx = 1\n")
        res = self.run_tool()
        self.assertEqual(res.code, 2)
        try:
            ast.parse("def ok(): ...\n\nif True:\nx = 1\n")
        except SyntaxError as exc:
            self.assertTrue(res.err.startswith(f"m.py:{exc.lineno}:{exc.offset}: "), res.err)

    def test_indentation_error(self) -> None:
        self.write("m.py", "def f():\n    x = 1\n  y = 2\n")
        res = self.run_tool()
        self.assertEqual((res.code, res.out), (2, ""))
        self.assertTrue(res.err.startswith("m.py:3:"), res.err)

    def test_bad_file_in_subfolder_uses_slash_path(self) -> None:
        self.write("pkg/sub/m.py", "def (\n")
        res = self.run_tool()
        self.assertTrue(res.err.startswith("pkg/sub/m.py:1:"), res.err)

    def test_undecodable_file(self) -> None:
        self.write("m.py", b"x = '\xff\xfe'\n")
        res = self.run_tool()
        self.assertEqual((res.code, res.out), (2, ""))
        self.assertTrue(res.err.startswith("m.py:"), res.err)
        self.assertEqual(len(res.err.splitlines()), 1)

    def test_unknown_coding_declaration(self) -> None:
        self.write("m.py", b"# -*- coding: nonsense -*-\nx = 1\n")
        res = self.run_tool()
        self.assertEqual((res.code, res.out), (2, ""))
        self.assertTrue(res.err.startswith("m.py:"), res.err)

    def test_null_byte(self) -> None:
        self.write("m.py", b"x = 1\x00\n")
        res = self.run_tool()
        self.assertEqual((res.code, res.out), (2, ""))
        self.assertTrue(res.err.startswith("m.py:"), res.err)
        self.assertNotIn("Traceback", res.err)

    def test_non_ascii_path_in_error(self) -> None:
        self.write("\u00e9.py", "def (\n")
        res = self.run_tool()
        self.assertEqual(res.code, 2)
        self.assertTrue(res.err.startswith("\u00e9.py:1:"), res.err)

    def test_bad_file_wins_over_fail_under(self) -> None:
        self.write("bad.py", "def (\n")
        self.assertEqual(self.run_tool("--fail-under", "0").code, 2)
        self.assertEqual(self.run_tool("--fail-under", "100").code, 2)


class TestEncodings(Base):
    def test_coding_declaration(self) -> None:
        src = "# -*- coding: latin-1 -*-\ndef caf\u00e9(a): return '\u00e9'\n".encode("latin-1")
        self.write("m.py", src)
        res = self.run_tool()
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.lines()[0], "m.py:2: function caf\u00e9: parameter a")

    def test_byte_order_mark(self) -> None:
        self.write("m.py", b"\xef\xbb\xbfdef f(a): ...\n")
        res = self.run_tool()
        self.assertEqual(res.code, 0, res.err)
        self.assertEqual(res.lines(), ["m.py:1: function f: parameter a", "m.py:1: function f: return",
                                       "m.py: 0/2 0.0%", "total: 0/2 0.0%"])

    def test_crlf_and_cr_change_nothing(self) -> None:
        src = "class C:\n    def m(self, a,\n          b: int):\n        return a\n"
        self.write("m.py", src)
        base = self.run_tool().out
        self.write("m.py", src.replace("\n", "\r\n"))
        self.assertEqual(self.run_tool().out, base)
        self.write("m.py", src.replace("\n", "\r"))
        self.assertEqual(self.run_tool().out, base)

    def test_utf8_in_strings_and_comments(self) -> None:
        self.write("m.py", "# caf\u00e9 \u65e5\u672c\ndef f(a: '\u00e9') -> '\u65e5': s = '\u00e9'\n")
        self.assertEqual(self.run_tool().lines(), ["m.py: 2/2 100.0%", "total: 2/2 100.0%"])

    def test_no_trailing_newline_and_form_feed(self) -> None:
        self.write("m.py", "def f(a): ...\n\x0c\ndef g(b): ...")
        self.assertEqual(self.run_tool().lines()[-1], "total: 0/4 0.0%")

    def test_warnings_in_source_do_not_reach_stderr(self) -> None:
        self.write("m.py", "def f(a: int) -> int:\n    return '\\d' is 1\n")
        res = self.run_tool()
        self.assertEqual((res.code, res.err), (0, ""))


class TestJsonForms(Base):
    def test_options_never_change_the_shape(self) -> None:
        self.write("m.py", "from typing import Any\nclass C:\n    def __init__(self, a): ...\n    def _p(self, b: Any): ...\n")
        shapes = set()
        for extra in ([], ["--include-private"], ["--include-magic"], ["--strict-any"],
                      ["--include-private", "--include-magic", "--strict-any", "--fail-under", "0"]):
            res = self.run_tool("--json", *extra)
            doc = res.json()
            shape = (tuple(sorted(doc)), tuple(sorted(doc["total"])), tuple(sorted(doc["files"][0])))
            shapes.add(shape)
        self.assertEqual(len(shapes), 1)

    def test_all_options_json(self) -> None:
        self.write("m.py", "from typing import Any\nclass C:\n    def __init__(self, a): ...\n    def _p(self, b: Any): ...\n")
        doc = self.run_tool("--json", "--include-private", "--include-magic", "--strict-any").json()
        self.assertEqual(doc["total"], {"annotated": 0, "counted": 3, "percent": 0.0})
        self.assertEqual(
            [(m["name"], m["parameter"]) for m in doc["files"][0]["missing"]],
            [("C.__init__", "a"), ("C._p", "b"), ("C._p", None)],
        )

    def test_json_is_one_document_and_one_newline(self) -> None:
        self.write("m.py", "def f(a): ...\n")
        out = self.run_tool("--json").out
        self.assertTrue(out.endswith("\n") and not out.endswith("\n\n"))
        json.loads(out)

    def test_percent_values(self) -> None:
        self.write("a.py", "def f(a: int, b) -> int: ...\n")
        self.write("b.py", "def f(a: int) -> int: ...\n")
        self.write("c.py", "")
        doc = self.run_tool("--json").json()
        self.assertEqual([f["percent"] for f in doc["files"]], [66.6, 100.0, None])
        self.assertEqual(doc["total"]["percent"], 80.0)

    def test_json_non_ascii_survives(self) -> None:
        self.write("\u00e9.py", "def f\u00fc(\u00e4): ...\n")
        doc = self.run_tool("--json").json()
        self.assertEqual(doc["files"][0]["file"], "\u00e9.py")
        self.assertEqual(doc["files"][0]["missing"][0]["name"], "f\u00fc")
        self.assertEqual(doc["files"][0]["missing"][0]["parameter"], "\u00e4")

    def test_json_file_with_nothing_to_count(self) -> None:
        self.write("m.py", "x = 1\n")
        doc = self.run_tool("--json").json()
        self.assertEqual(doc["files"], [{"file": "m.py", "annotated": 0, "counted": 0, "percent": None, "missing": []}])


class TestSourceOfTheTool(unittest.TestCase):
    """The tool's own file follows the rules the job states for it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = TOOL.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.text)

    def test_first_line(self) -> None:
        self.assertEqual(self.text.split("\n", 1)[0], "#!/usr/bin/env python3")

    def test_imports_are_standard_library(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                self.assertIn(name, sys.stdlib_module_names)

    def test_every_def_is_fully_annotated(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertIsNotNone(node.returns, node.name)
                a = node.args
                params = [*a.posonlyargs, *a.args, *a.kwonlyargs]
                if a.vararg:
                    params.append(a.vararg)
                if a.kwarg:
                    params.append(a.kwarg)
                for i, p in enumerate(params):
                    if i == 0 and p.arg in ("self", "cls"):
                        continue
                    self.assertIsNotNone(p.annotation, f"{node.name}({p.arg})")

    def test_main_signature_and_ending(self) -> None:
        mains = [n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
        self.assertEqual(len(mains), 1)
        main_args = mains[0].args
        self.assertEqual([a.arg for a in main_args.args], ["argv"])
        self.assertEqual(ast.unparse(main_args.args[0].annotation), "list[str] | None")
        self.assertEqual([ast.unparse(d) for d in main_args.defaults], ["None"])
        self.assertEqual(ast.unparse(mains[0].returns), "int")
        self.assertTrue(self.text.rstrip("\n").endswith('if __name__ == "__main__":\n    raise SystemExit(main())'))

    def test_the_tool_is_its_own_clean_input(self) -> None:
        # No string of the key-like kind, and the tool reports itself fully annotated (not run on this folder).
        self.assertNotIn("PRIVATE" + " KEY", self.text)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "typed_cov.py").write_text(self.text, encoding="utf-8")
            proc = subprocess.run([sys.executable, str(TOOL), tmp], capture_output=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = proc.stdout.decode("utf-8").splitlines()
            self.assertEqual(len(out), 2, out)
            self.assertRegex(out[0], r"^typed_cov\.py: (\d+)/\1 100\.0%$")


class TestMainInProcess(Base):
    def load(self):
        spec = importlib.util.spec_from_file_location("typed_cov_under_test", TOOL)
        module = importlib.util.module_from_spec(spec)
        sys.modules["typed_cov_under_test"] = module
        self.addCleanup(sys.modules.pop, "typed_cov_under_test", None)
        spec.loader.exec_module(module)
        return module

    def test_main_returns_the_exit_code(self) -> None:
        self.write("m.py", "def f(a): ...\n")
        module = self.load()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main([str(self.root)]), 0)
            self.assertEqual(module.main([str(self.root), "--fail-under", "50"]), 1)
            self.assertEqual(module.main(["--nope"]), 2)
        self.assertIn("total: 0/2 0.0%", out.getvalue())

    def test_main_none_means_sys_argv(self) -> None:
        self.write("m.py", "def f(a: int) -> int: ...\n")
        module = self.load()
        saved = sys.argv
        out = io.StringIO()
        try:
            sys.argv = ["typed_cov.py", str(self.root)]
            with contextlib.redirect_stdout(out):
                self.assertEqual(module.main(), 0)
        finally:
            sys.argv = saved
        self.assertIn("total: 2/2 100.0%", out.getvalue())


if __name__ == "__main__":
    unittest.main()
