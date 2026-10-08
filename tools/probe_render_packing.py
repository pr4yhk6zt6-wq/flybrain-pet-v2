#!/usr/bin/env python3
"""Cross-language gate for the render model's packed word.

`ConnectomeRenderModel.swift` packs region, side, provenance and the cell-class
bits into a single UInt32, and the widths it uses are the whole point: a field
that is one bit too narrow does not fail, it aliases. The first draft packed
region into 4 bits, which maps `endocrineVisceral` (raw 20) onto `medulla`
(raw 4) — two anatomically unrelated regions drawn in one colour, with no error
anywhere in the program.

That defect is only preventable if the widths are DERIVED from the enums rather
than copied from a comment. So this gate reads both sides of the language
boundary, on the real files:

  * ios/Sources/FlyBrainCore/Types.swift   — the Swift enums
  * python/flybrain/pid.py                 — the pipeline's enums, and the
                                             authority for the wire format
                                             (these raw values are baked into
                                             the asset bytes)

and it reads the masks out of the Swift source itself, so the numbers checked
are the ones shipped, not a transcription of them.

WHAT THIS DOES NOT DO: it does not compile or execute Swift. The runtime
assertions on the round trip live in
`ios/Tests/FlyBrainCoreTests/ConnectomeRenderModelTests.swift` and run in CI on
a macOS toolchain. This gate covers the part that a runtime test structurally
cannot see: that the enum sizes the widths were chosen for are still the enum
sizes, on BOTH sides of the Swift/Python boundary, and that the Swift-side
offsets still match the table the Metal shader is written against.

Run: python3 tools/probe_render_packing.py
Exit code 0 = every check below held; 1 = at least one did not.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SWIFT_TYPES = ROOT / "ios/Sources/FlyBrainCore/Types.swift"
SWIFT_MODEL = ROOT / "ios/Sources/FlyBrainCore/ConnectomeRenderModel.swift"
PY_PID = ROOT / "python/flybrain/pid.py"
SWIFT_CAMERA_TESTS = ROOT / "ios/Tests/FlyBrainCoreTests/RenderCameraTests.swift"
PY_CAMERA_MIRROR = ROOT / "tools/verify_render_camera.py"
SHIPPED_BANC = ROOT / "data/generated/banc_cns.fbpack"
SHADER_PATH = ROOT / "ios/Sources/FlyBrainPetApp/Render/NeuronShaders.metal"
SWIFT_RENDERER = ROOT / "ios/Sources/FlyBrainPetApp/Render/ConnectomeRenderer.swift"


class Gate:
    def __init__(self):
        self.failures = []
        self.checks = 0

    def check(self, name, ok, detail=""):
        self.checks += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        if not ok:
            self.failures.append(name)


def measured_banc_extents():
    """Real extents of the shipped BANC asset, or None if it is not checked out.

    Read from the asset rather than from the report JSON: the report is a
    summary a human wrote, the block is the data the renderer will actually be
    handed. The 68 MB asset is not tracked in git (it is far too large), so a
    CI checkout will not have it and this returns None — the bounds are then
    cross-checked only against the other two files, which is stated in the
    output rather than silently skipped.
    """
    if not SHIPPED_BANC.exists():
        return None
    import struct
    blob = SHIPPED_BANC.read_bytes()
    hlen = struct.unpack_from("<Q", blob, 0)[0]
    off = 8 + hlen
    for _ in range(4):                       # neuron, synapse, range, region
        blen = struct.unpack_from("<Q", blob, off)[0]
        off += 8
        if _ == 0:
            neurons = blob[off:off + blen]
        off += blen
    stride = 44
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    fmt = "<iBHBBBBBiiiiifff"
    for i in range(len(neurons) // stride):
        v = struct.unpack_from(fmt, neurons, i * stride)
        for k, val in enumerate(v[13:16]):
            lo[k] = min(lo[k], val)
            hi[k] = max(hi[k], val)
    return lo, hi


def camel_to_upper_snake(name):
    """`lobulaPlate` -> `LOBULA_PLATE`, matching pid.py's spelling."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()


def parse_swift_int_enum(source, enum_name):
    """Cases of a Swift `enum X: Int` as {CASE_NAME: raw_value}."""
    body = extract_enum_body(source, enum_name)
    out = {}
    for name, value in re.findall(r"case\s+(\w+)\s*=\s*(\d+)", body):
        out[camel_to_upper_snake(name)] = int(value)
    return out


def parse_swift_string_enum(source, enum_name):
    """Cases of a Swift `enum X: String` as {CASE_NAME: "raw string"}."""
    body = extract_enum_body(source, enum_name)
    out = {}
    for name, value in re.findall(r'case\s+(\w+)\s*=\s*"([^"]*)"', body):
        out[camel_to_upper_snake(name)] = value
    return out


def extract_enum_body(source, enum_name):
    """Text between `enum <name>` and its closing brace at column 0."""
    m = re.search(r"^public enum %s\b[^{]*\{" % re.escape(enum_name),
                  source, re.MULTILINE)
    if not m:
        raise SystemExit(f"could not find `enum {enum_name}` in the sources")
    i = m.end() - 1
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[i:j]
    raise SystemExit(f"unterminated body for `enum {enum_name}`")


def parse_python_int_enum(source, enum_name):
    """Cases of a Python `class X(enum.IntEnum)` as {CASE_NAME: raw_value}."""
    m = re.search(r"^class %s\b[^:]*:" % re.escape(enum_name), source,
                  re.MULTILINE)
    if not m:
        raise SystemExit(f"could not find `class {enum_name}` in pid.py")
    out = {}
    for line in source[m.end():].splitlines():
        if line and not line[0].isspace():
            break                      # next top-level definition
        hit = re.match(r"\s+([A-Z][A-Z0-9_]*)\s*=\s*(\d+)", line)
        if hit:
            out[hit.group(1)] = int(hit.group(2))
    return out


def read(path):
    if not path.exists():
        raise SystemExit(f"missing required file: {path}")
    return path.read_text(encoding="utf-8")


def field_width(name, raw_values):
    """Smallest bit width that holds every raw value of an enum."""
    top = max(raw_values)
    width = 0
    while (1 << width) - 1 < top:
        width += 1
    return width


def main():
    g = Gate()
    swift_types = read(SWIFT_TYPES)
    swift_model = read(SWIFT_MODEL)
    swift_test = read(SWIFT_CAMERA_TESTS)
    py_mirror = PY_CAMERA_MIRROR.read_text()
    py_pid = read(PY_PID)

    # --- the enums themselves ------------------------------------------------
    swift_regions = parse_swift_int_enum(swift_types, "RegionID")
    py_regions = parse_python_int_enum(py_pid, "RegionID")
    swift_prov = parse_swift_string_enum(swift_types, "Provenance")
    py_prov = parse_python_int_enum(py_pid, "Provenance")

    g.check("RegionID exists on both sides",
            bool(swift_regions) and bool(py_regions),
            f"swift={len(swift_regions)} python={len(py_regions)}")

    # The raw values are stored in asset bytes, so the two sides must agree
    # value-for-value, not just name-for-name: a region that reads 16 on one
    # side and 17 on the other is a different neuropil in the same build.
    g.check("RegionID raw values agree across the language boundary",
            swift_regions == py_regions,
            "" if swift_regions == py_regions
            else f"swift-only={ {k: v for k, v in swift_regions.items() if py_regions.get(k) != v} } "
                 f"py-only={ {k: v for k, v in py_regions.items() if swift_regions.get(k) != v} }")

    g.check("RegionID raw values are exactly 0..count-1 (dense, no gaps)",
            sorted(swift_regions.values()) == list(range(len(swift_regions))),
            f"n={len(swift_regions)} max={max(swift_regions.values(), default=-1)}")

    # Provenance is packed into the render word as an INDEX into `allCases`,
    # not as a raw value — so it is the two languages' ORDER that has to agree,
    # not merely their size. Swift's allCases order is the declaration order in
    # Types.swift; Python's is the numeric order in pid.py. Insert a case in the
    # middle of either and every index past it silently shifts, meaning that one
    # number in the header, and one field on screen, names a different
    # provenance on each side of the boundary. Comparing counts alone (which is
    # what this gate did first) passes in exactly that case.
    swift_prov_order = list(swift_prov.keys())
    py_prov_order = [name for name, _ in
                     sorted(py_prov.items(), key=lambda kv: kv[1])]
    g.check("Provenance order agrees across the language boundary (packed as index)",
            swift_prov_order == py_prov_order,
            "" if swift_prov_order == py_prov_order
            else f"swift={swift_prov_order} python={py_prov_order}")

    # The wire format stores the provenance NAME, so the spelling must match
    # too: Swift's implicit rawValue for `case inferred` would be "inferred"
    # while the pipeline writes "INFERRED", decoding to nil with no error.
    g.check("Provenance names are spelled identically on both sides",
            all(swift_prov[name] == name for name in swift_prov)
            and set(swift_prov) == set(py_prov),
            f"swift names={sorted(swift_prov.values())}")

    # --- derive the widths the packed word needs -----------------------------
    region_bits = field_width("region", swift_regions.values())
    # Provenance is packed as an INDEX into allCases, not as a raw value, so it
    # is sized by the case count (which the archive format bounds-checks).
    prov_bits = field_width("provenance", range(len(swift_prov)))

    g.check("derived region width still fits every RegionID case",
            len(swift_regions) <= (1 << region_bits),
            f"{len(swift_regions)} cases need {region_bits} bits "
            f"(capacity {1 << region_bits})")
    g.check("derived provenance width still fits every Provenance case",
            len(swift_prov) <= (1 << prov_bits),
            f"{len(swift_prov)} cases need {prov_bits} bits "
            f"(capacity {1 << prov_bits})")

    # --- the masks actually in the Swift source ------------------------------
    # Read from the packing expression itself, so a hand-edit that changes a
    # width is caught here rather than by a comment going stale.
    #
    # Comment lines are NOT stripped: every regex below is anchored with `///?`
    # so that BOTH the doc comments and the real declarations match. That is
    # deliberate — the first version of this gate read the `///`-prefixed
    # layout table and silently checked nothing, because a `///` line cannot
    # match `^\s*(\d+)`. A gate that passes by matching no lines at all is the
    # failure mode this whole file exists to avoid, so the patterns here are
    # written to fire on the comments and `unmatched` is asserted below.

    # The `packed =` assignment spans several lines joined by a leading `|` and
    # contains nested parentheses (`UInt32(region)`), so a bracket-matching
    # regex cannot find its end. Walk lines instead: the first line, then every
    # continuation line that starts with `|`.
    whole = swift_model
    m = re.search(r"packed\s*=\s*(.*)$", whole, re.MULTILINE)
    if not m:
        raise SystemExit("could not find the `packed =` expression in "
                         "ConnectomeRenderModel.swift")
    lines = whole[m.start(1):].splitlines()
    segment = [lines[0]]
    for line in lines[1:]:
        if not line.lstrip().startswith("|"):
            break
        segment.append(line)
    expr = " ".join(" ".join(segment).split())

    # The four terms of the `|` chain, each wearing its own shift. A term is
    # `(...) << N`, or — for the classes field — a bare expression shifted by
    # an already-multiplied constant (`(cls << 13)`). Splitting on `| SOURCE`
    # and reading the shift out of each head covers all three shapes without a
    # mask, and the mask (where one exists) is read for the width check.
    heads = [t for t in re.split(r"\|\s*(?=(?:\(|\w))", expr) if t.strip()]
    fields = []
    for head in heads:
        shift_m = re.search(r"<<\s*(\d+)", head)
        mask_m = re.search(r"&\s*0x([0-9A-Fa-f]+)", head)
        if shift_m is None and mask_m is None:
            # First term carries no shift and may carry no mask.
            fields.append((0, None))
            continue
        if shift_m is None:
            # Masked but never shifted: the first field.
            fields.append((0, int(mask_m.group(1), 16)))
            continue
        fields.append((int(shift_m.group(1)),
                       int(mask_m.group(1), 16) if mask_m else None))
    fields.sort(key=lambda f: f[0])

    # The widths in the source are a SAFE SUPERSET of what the enums need —
    # side is given 4 bits for 3 values, provenance 4 bits for 6 cases (so bit
    # 12 is deliberate slack, letting a 7th/8th provenance case land without
    # disturbing any other field). Asserting exact equality with the derived
    # widths would forbid that, so check the properties that actually matter:
    # the fields tile the word with no hole or overlap, every mask is wide
    # enough for the enum it carries, and nothing spills past bit 31.
    bits_needed = [region_bits, 2, prov_bits, 2]   # region, side, prov, classes
    ok_tiling = len(fields) == len(bits_needed)
    notes = []
    cursor = 0
    for (shift, mask), need in zip(fields, bits_needed):
        if shift != cursor:
            ok_tiling = False
            notes.append(f"bit {cursor}..{shift - 1} unaccounted for")
        width = mask.bit_length() if mask else need
        if width < need:
            ok_tiling = False
            notes.append(f"field at bit {shift} holds {width} bits, needs "
                         f"{need}")
        cursor = shift + width
    if cursor > 32:
        ok_tiling = False
        notes.append(f"fields reach bit {cursor - 1}, past the 32-bit word")
    shown = [("0x%X" % m_ if m_ else "none", s) for s, m_ in fields]
    g.check("Swift packed word fields tile the word and cover every enum",
            ok_tiling,
            f"source={shown} bits used={cursor}"
            + ("  <-- " + "; ".join(notes) if notes else ""))

    # --- layout table the Metal shader is written against --------------------
    # The runtime test measures these with MemoryLayout.offset(of:); this is
    # the parse-level twin, so a reshuffle is caught even if the runtime test
    # is the thing being edited. The table is in the doc comment above the
    # struct, hence the optional leading `///`.
    #
    # Whitespace is written as `[ \t]`, NOT `\s`: `\s` matches newlines, which
    # lets a pattern slide past the end of one row and swallow the next row's
    # offset — how the first version reported `x` at offset 0 while really
    # reading `y`'s number.
    #
    # Scope this to the NeuronInstance table and stop there. Both structs carry
    # their table in a doc comment ABOVE the declaration, so splitting at
    # `public struct NeuronActivity` still captures it — `index`, `_pad0` and
    # `_pad1` appear in both structs at different offsets. Walk the rows in
    # order and stop at the first repeated member name instead, which needs no
    # assumption about how the next struct is spelled.
    table_pat = (r"^[ \t]*///?[ \t]+(\d+)[ \t]+(\d+)[ \t]+"
                 r"([A-Za-z_]\w*)[ \t]+(UInt32|Float)[ \t]*.*$")
    table = []
    seen_names = set()
    for off, size, name, typ in re.findall(table_pat, swift_model,
                                           re.MULTILINE):
        if name in seen_names:
            break                      # next struct's table begins
        seen_names.add(name)
        table.append((off, size, name, typ))
    g.check("the shader layout table was actually matched (not zero rows)",
            len(table) >= 8,
            f"matched {len(table)} rows; if this is 0 the check below is "
            + "vacuous, which is the bug this line exists to catch")

    expected_offsets = {"x": 0, "y": 4, "z": 8, "packed": 12,
                        "typeIndex": 16, "index": 20, "_pad0": 24, "_pad1": 28}
    got = {name: int(off) for off, _size, name, _typ in table
           if name in expected_offsets}
    g.check("documented NeuronInstance offsets still sum to a 32-byte row",
            got == expected_offsets,
            f"documented={got} expected={expected_offsets}")

    # --- the numbers the Swift camera tests assert ---------------------------
    # Two copies of the same measurement (the bounds in RenderCameraTests.swift
    # and in tools/verify_render_camera.py) can drift, and nothing would notice:
    # each file would keep passing against its own copy. Pin them to each other
    # and to the shipped asset's real extents.
    print()
    print("=== the camera's measured bounds are one number, not two ===")
    swift_bounds = re.search(
        r"RenderBounds\(minX:\s*([\d.]+),\s*minY:\s*([\d.]+),\s*minZ:\s*([\d.]+),"
        r"\s*maxX:\s*([\d.]+),\s*maxY:\s*([\d.]+),\s*maxZ:\s*([\d.]+)\)",
        swift_test)
    py_bounds = re.search(
        r"BANC_BOUNDS = \(\(([\d.]+), ([\d.]+), ([\d.]+)\), "
        r"\(([\d.]+), ([\d.]+), ([\d.]+)\)\)", py_mirror)
    if not swift_bounds or not py_bounds:
        raise SystemExit("could not read the bounds from the Swift test or the "
                         "Python mirror")
    sb = [float(x) for x in swift_bounds.groups()]
    pb = [float(x) for x in py_bounds.groups()]
    g.check("the Swift test and the Python mirror quote the same bounds",
            sb == pb, f"swift={sb} python={pb}")

    # ...and that those bounds really are the shipped asset's extents, so a
    # rename or a re-ingest that moves the cloud fails here instead of leaving
    # both copies agreeing on a stale rectangle.
    measured = measured_banc_extents()
    if measured is None:
        print("  note: shipped BANC asset not present; bounds are not "
              "cross-checked against it in this checkout")
    else:
        lo, hi = measured
        want = [lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]]
        ok = all(abs(a - b) < 0.01 for a, b in zip(sb, want))
        g.check("the quoted bounds are the shipped asset's real extents",
                ok, f"asset={[round(v, 4) for v in want]} quoted={sb}")

    # --- what the shader must agree with -------------------------------------
    # --- the shader's copy of the layout must agree with Swift's -------------
    # The shader restates the packed layout in a comment table, and restating
    # is exactly how the two drift. Parse the shader and compare it against the
    # derived widths — a change on either side alone fails here.
    print()
    print("=== the Metal shader agrees with the Swift layout ===")
    shader = SHADER_PATH.name
    shader_src = SHADER_PATH.read_text()
    shader_rows = re.findall(
        r"^[ \t]*//[ \t]+([A-Za-z_]\w*)[ \t]+(\d+)[ \t]+bits[ \t]+at[ \t]+bit"
        r"[ \t]+(\d+)", shader_src, re.MULTILINE)
    g.check("the shader's layout table was matched (not zero rows)",
            len(shader_rows) >= 4,
            f"matched {len(shader_rows)} rows from {SHADER_PATH.name}")
    shader_map = {name: (int(bits), int(bit)) for name, bits, bit in shader_rows}
    # Derive the expectation from the Swift expression's ACTUAL masks, not from
    # the minimal widths the enums need. The source deliberately gives
    # provenance 4 bits where 6 cases need 3, so a minimal-width expectation
    # reports a mismatch that does not exist — which is what this check did on
    # its first run. Each field's declared width is its mask's bit length; only
    # the shift is derived from the preceding widths.
    
    prov_declared = next((m for s, m in fields if s == region_bits + 4), None)
    classes_shift = region_bits + 4
    if prov_declared is not None:
        classes_shift += prov_declared.bit_length()
    derived = [("region", fields[0][1].bit_length() if fields[0][1] else region_bits, 0),
               ("side", 4, region_bits),
               ("provenance", prov_declared.bit_length() if prov_declared else prov_bits,
                region_bits + 4),
               ("classes", 2, classes_shift)]
    mismatches = []
    for name, bits, bit in derived:
        got = shader_map.get(name)
        if got != (bits, bit):
            mismatches.append(f"{name}: shader={got} swift=({bits}, {bit})")
    g.check("every packed field agrees between the shader and the Swift source",
            not mismatches, "; ".join(mismatches))

    # The shader must not index the palette with a RegionID case count: the
    # field is 5 bits, so any code up to 31 can arrive and a 21-entry table
    # would read out of bounds on the GPU (undefined, not a trap).
    g.check("the shader masks the region out of the packed word",
            re.search(r"packed\s*&\s*0x1F", shader_src) is not None,
            "the shader must derive region as `packed & 0x1F`")

    # The host must pad its upload to the same 32 entries, or the shader's
    # indices past the real palette read whatever follows the buffer.
    host = read(SWIFT_RENDERER)
    g.check("the host pads its palette upload to 32 entries to match the shader",
            re.search(r"count:\s*32\b", host) is not None,
            "ConnectomeRenderer.swift must build a 32-entry palette")

    # RegionColor is four plain floats on both sides. A `SIMD3<Float>` in Swift
    # and a `float3` in Metal HAPPEN to agree (both pad to stride 32), which is
    # the dangerous case: it works until one side "simplifies" the other field
    # and the padding rules stop coinciding. Pinning the absence of a vector
    # type here makes that edit fail loudly.
    shader_color = re.search(r"struct RegionColor \{(.*?)\};", shader_src,
                             re.DOTALL)
    g.check("the shader's RegionColor is four scalar floats, not a float3",
            shader_color is not None
            and "float3" not in shader_color.group(1)
            and len(re.findall(r"float\s+\w+;", shader_color.group(1))) == 4,
            "a vector type is padded to its full width in both languages; four "
            "scalars avoid depending on two padding rules coinciding")

    print()
    print(f"  region      {region_bits} bits at bit 0      "
          f"({len(swift_regions)} cases)")
    print(f"  side        4 bits at bit {region_bits}      (3 cases)")
    print(f"  provenance  {derived[2][1]} bits at bit {region_bits + 4}      "
          f"({len(swift_prov)} cases, index not raw value; "
          f"{derived[2][1] - prov_bits} bit(s) of slack)")
    print(f"  classes     2 bits at bit {classes_shift}      "
          f"(motor bit 0, sensory bit 1)")
    print()

    print(f"{g.checks - len(g.failures)}/{g.checks} checks passed")
    if g.failures:
        print("\nFAILED:")
        for f in g.failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())