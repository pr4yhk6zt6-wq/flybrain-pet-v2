#!/usr/bin/env python3
"""Cross-check Swift call sites in the test target against source declarations.

WHY THIS EXISTS
---------------
There is no Swift toolchain on the development machine, so a test file that
calls `c.neuron(nearest:)` when the method is `c.neuron(nearestNDC:)` looks
perfectly fine until CI compiles it. That happened twice in one sitting: a test
file was written against an API that did not exist (`setNeuronsForTesting`),
and a second time against the wrong labels. Both are mechanical errors that a
grep can catch, and catching them here is far cheaper than a CI round trip.

WHAT IT CHECKS, AND WHAT IT DOES NOT
------------------------------------
It checks, for every declaration in `ios/Sources`, the set of argument labels it
accepts, and then reports a call in `ios/Tests` when either
  (a) no declaration of that name exists, or
  (b) the call passes an argument label the declaration does not accept.

It does NOT type-check, resolve overloads by argument type, or understand
generics. A label set is a SET of alternatives, so a call that is legal against
any one overload passes. It reads names and labels only — a call that compiles
still might not, and this script never claims otherwise. Its value is that it
is executable today, and it is verified to fail (see --self-test).

This is a gate, not a proof: `xcodebuild`/`swift build` remains the authority.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "ios" / "Sources"
TESTS = ROOT / "ios" / "Tests"

# Control-flow keywords that the call regex cannot tell from a function call.
# Listed explicitly: a heuristic such as "no labels means not a call" would also
# hide a real argument-less call, and a checker that hides things is worthless.
SWIFT_KEYWORDS = {
    "return", "let", "var", "if", "else", "for", "while", "guard", "switch",
    "case", "default", "do", "catch", "try", "throw", "defer", "repeat",
    "break", "continue", "where", "as", "is", "some", "any", "await", "async",
    "self", "in", "repeat", "precondition", "assert", "fatalError",
}

# Free functions, initialisers and collection members that the test target
# calls without a declaration in this project. Anything NOT here and NOT
# declared is reported, so this list must stay small and legible — a long
# ignore list is how such a checker becomes decorative.
EXTERNAL = {
    "XCTAssertEqual", "XCTAssertNotEqual", "XCTAssertTrue", "XCTAssertFalse",
    "XCTAssertNil", "XCTAssertNotNil", "XCTUnwrap", "XCTFail", "XCTAssert",
    "XCTAssertGreaterThan", "XCTAssertLessThan", "setUp", "tearDown",
    "stride", "contains", "filter", "map", "reduce", "max", "min", "abs",
    "sqrt", "pow", "sin", "cos", "tan", "round", "zip", "enumerated",
    "offset", "joined", "withUnsafeBytes", "withMemoryRebound",
    "fromLittleEndian", "squareRoot", "isFinite", "isNaN", "loadUnaligned",
    "appendingPathComponent", "init", "deinit", "count", "append",
    "reserveCapacity", "allCases", "firstIndex", "rawValue", "isEmpty",
    "sinf", "cosf", "tanf", "sqrtf", "powf", "fabsf", "expf", "logf",
    "atan2f", "copysignf", "Float", "Double", "Int", "Bool", "String",
    "Array", "Set", "Dictionary", "UInt8", "UInt16", "UInt32", "UInt64",
    "Int8", "Int16", "Int32", "Int64", "SIMD2", "SIMD3", "SIMD4", "Float4x4",
    "String", "Data", "URL", "Bundle", "NSLock", "Date", "UUID",
    # stdlib / Foundation members called on types this target does not declare
    # (`Set`, `FileManager`, `Dictionary.Keys`, `String`). Each is a real Apple
    # API, not something FlyBrainCore is expected to define. Kept explicit and
    # small: a broad ignore list is how such a checker becomes decorative.
    "insert", "isSubset", "sorted", "uppercased", "removeItem", "default",
    "keys", "values", "unsupportedVersion", "url",  # Bundle.url(forResource:...)
}

DECL_FUNC = re.compile(r"\bfunc\s+([A-Za-z_]\w*)\s*\(")
DECL_VAR = re.compile(r"\b(?:var|let)\s+([A-Za-z_]\w*)\s*[:=]")
DECL_TYPE = re.compile(r"\b(?:struct|class|enum|protocol|actor|typealias)\s+([A-Za-z_]\w*)")
# A call: optional receiver/keyword prefix, then a possibly-dotted name, then an
# opening paren. The lookbehind excludes only operator-ish noise, NOT `.`, so a
# receiver call (`c.inspection(`) is seen — the first revision excluded `.` and
# therefore saw almost no calls at all, and reported a clean pass on a file that
# was full of non-existent methods.
CALL = re.compile(r"(?<![A-Za-z0-9_$])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(")


def code_with_offsets(text: str) -> tuple[str, list[int]]:
    """Remove comments and string bodies, keeping a map back to raw offsets.

    The map matters: a call on a line preceded by a comment would otherwise be
    reported at the wrong position, and a multi-line call would be skipped
    because a per-line scan cannot see its closing parenthesis.
    """
    kept: list[str] = []
    offsets: list[int] = []
    i, n = 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "//":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if two == "/*":
            depth, i = 1, i + 2
            while i < n and depth:
                if text[i:i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                elif text[i:i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                else:
                    i += 1
            continue
        if text[i] == '"':
            triple = text[i:i + 3] == '"""'
            j = i + (3 if triple else 1)
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if triple and text[j:j + 3] == '"""':
                    j += 3
                    break
                if not triple and text[j] == '"':
                    j += 1
                    break
                if not triple and text[j] == "\n":
                    break
                j += 1
            kept.append('""')
            offsets.append(i)
            i = j
            continue
        kept.append(text[i])
        offsets.append(i)
        i += 1
    return "".join(kept), offsets


def line_of(offsets: list[int], stripped_index: int) -> int:
    raw = offsets[min(stripped_index, len(offsets) - 1)]
    return raw


def _split_params(params: str) -> list[str]:
    """Split on top-level commas only.

    A naive `split(",")` breaks on generic commas (`Dictionary<String, Int>`) and
    on array-literal defaults, and a broken split invents labels that do not
    exist — the first revision reported a real `appendSynapse(s:)` as missing.
    """
    parts, depth, current = [], 0, ""
    for ch in params:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return parts


def param_labels(params: str) -> frozenset[str]:
    """The external argument labels a declaration accepts.

      `func f(a: T)`            -> {"a"}
      `func f(_ a: T)`          -> {}          (underscore suppresses the label)
      `func f(a: T = x, b: U)`  -> {"a","b"}   (a default still has a label)
    """
    labels: set[str] = set()
    for part in _split_params(params):
        part = re.sub(r"^\s*@\w+(\([^)]*\))?\s*", "", part.strip())
        head = part.split("=")[0].strip()
        if not head:
            continue
        m = re.match(r"^(_)\s+\w+\s*:", head)
        if m:
            continue
        m = re.match(r"^(\w+)\s+\w+\s*:", head)
        if m:
            labels.add(m.group(1))
            continue
        m = re.match(r"^(\w+)\s*:", head)
        if m and m.group(1) not in SWIFT_KEYWORDS:
            labels.add(m.group(1))
    return frozenset(labels)


def match_paren(code: str, open_index: int) -> int:
    depth, i = 0, open_index
    while i < len(code):
        if code[i] == "(":
            depth += 1
        elif code[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def scan_declarations(code: str) -> dict[str, set[frozenset[str]]]:
    index: dict[str, set[frozenset[str]]] = {}
    for m in DECL_FUNC.finditer(code):
        open_i = m.end() - 1
        close_i = match_paren(code, open_i)
        if close_i < 0:
            continue
        index.setdefault(m.group(1), set()).add(param_labels(code[open_i + 1:close_i]))
    for m in DECL_VAR.finditer(code):
        index.setdefault(m.group(1), set()).add(frozenset())
    for m in DECL_TYPE.finditer(code):
        index.setdefault(m.group(1), set()).add(frozenset())
    return index


def declared_types() -> set[str]:
    """Names declared as struct/class/enum/actor — their memberwise initialisers
    are synthesised by the compiler, so there is no `func` to read labels from.
    A call to one of these is therefore NOT label-checked: claiming to validate
    it would mean guessing the property order, and this script does not guess."""
    names: set[str] = set()
    for root in (SOURCES, TESTS):
        for path in sorted(root.rglob("*.swift")):
            code, _ = code_with_offsets(path.read_text())
            names.update(m.group(1) for m in DECL_TYPE.finditer(code))
    return names


def build_index() -> dict[str, set[frozenset[str]]]:
    """Declarations from the library AND the test target.

    The test target declares its own fixtures (`TestSupport.provIndex`,
    `closedLoopConnectome`). Leaving them out produced 300+ false positives,
    which is how a gate gets switched off.
    """
    index: dict[str, set[frozenset[str]]] = {}
    for root in (SOURCES, TESTS):
        for path in sorted(root.rglob("*.swift")):
            code, _ = code_with_offsets(path.read_text())
            for name, labels in scan_declarations(code).items():
                index.setdefault(name, set()).update(labels)
    return index


def call_sites(tests: Path):
    for path in sorted(tests.rglob("*.swift")):
        raw = path.read_text()
        code, offsets = code_with_offsets(raw)
        for m in CALL.finditer(code):
            name = m.group(1)
            bare = name.split(".")[-1]
            open_i = m.end() - 1
            close_i = match_paren(code, open_i)
            if close_i < 0:
                continue
            labels = param_labels(code[open_i + 1:close_i])
            raw_off = line_of(offsets, m.start())
            line = raw.count("\n", 0, raw_off) + 1
            yield name, bare, labels, path.relative_to(ROOT), line


def check() -> list[str]:
    index = build_index()
    types = declared_types()
    problems: list[str] = []
    for name, bare, labels, rel, line in call_sites(TESTS):
        if bare in EXTERNAL or name in EXTERNAL or bare in SWIFT_KEYWORDS:
            continue
        if bare.startswith("XCT"):
            continue
        if bare in types:
            continue  # memberwise init: not label-checked, by design
        accepted = index.get(bare) or index.get(name)
        if accepted is None:
            problems.append(f"{rel}:{line}: calls `{name}`, which nothing declares")
            continue
        if not any(labels <= alt for alt in accepted):
            best = sorted(accepted, key=len)[0] if accepted else frozenset()
            extra = ", ".join(sorted(labels - set().union(*accepted))) or "(none)"
            problems.append(
                f"{rel}:{line}: `{name}({', '.join(sorted(labels))})` passes label(s) "
                f"{extra}; declared label(s): {', '.join(sorted(best)) or '-'}")
    return problems


SELF_TEST_CASES = [
    # (source declaration, call in a test file, must be reported?)
    ("func neuron(nearestNDC ndc: SIMD2<Float>, camera: RenderCamera) {}",
     "c.neuron(nearestNDC: p, camera: cam)", False),
    ("func neuron(nearestNDC ndc: SIMD2<Float>, camera: RenderCamera) {}",
     "c.neuron(nearest: p, camera: cam)", True),
    ("func inspection(of index: Int) {}",
     "c.inspection(of: i)", False),
    ("func inspection(of index: Int) {}",
     "c.inspection(at: i)", True),
    ("func f(_ a: Int) {}", "f(a: 1)", True),
    ("func f(_ a: Int) {}", "f(1)", False),
    ("func g(x: Int = 2, y: Int) {}", "g(y: 1)", False),
    ("func h(a: Int, b: Int) {}", "h(a: 1, b: 2)", False),
]


def self_test() -> int:
    """Prove the checker can fail, and can pass, before trusting either.

    A gate reported as working is worth nothing without this: the first
    revision of this file printed '0 problems' on a test file containing three
    calls to methods that do not exist.
    """
    failures = 0
    for decl, call, should_flag in SELF_TEST_CASES:
        code, _ = code_with_offsets(f"{decl}\n{call}\n")
        index = scan_declarations(code)
        flagged = False
        for m in CALL.finditer(code):
            bare = m.group(1).split(".")[-1]
            if bare in EXTERNAL or bare == "c":
                continue
            open_i = m.end() - 1
            close_i = match_paren(code, open_i)
            if close_i < 0:
                continue
            labels = param_labels(code[open_i + 1:close_i])
            accepted = index.get(bare)
            if accepted is None or not any(labels <= alt for alt in accepted):
                flagged = True
        if flagged != should_flag:
            failures += 1
            print(f"  SELF-TEST FAIL: {call!r} expected flag={should_flag} got {flagged}")
    if failures:
        print(f"self-test: {failures} case(s) wrong — the checker itself is broken")
        return 1
    print(f"self-test: {len(SELF_TEST_CASES)} cases behave as specified")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    rc = self_test()
    problems = check()
    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s)")
    return 1 if (problems or rc) else 0


if __name__ == "__main__":
    raise SystemExit(main())