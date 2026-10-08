//
//  NeuronShaders.metal
//  FlyBrainPet — connectome point renderer
//
//  Draws one point per neuron from the instance buffer produced by
//  FlyBrainCore's `ConnectomeRenderModel`. Two rules govern this file:
//
//  1. The `NeuronInstance` struct here MUST match the Swift one byte for byte.
//     Swift pads a struct to its largest member's alignment and Metal does not
//     pad the same way, which is why both sides are kept to 4-byte members and
//     the total is 32 bytes. A mismatch is not a compile error on either side —
//     it is silently wrong geometry. The Swift tests pin the offsets
//     (MemoryLayout.offset(of:)) and tools/probe_render_packing.py pins the
//     documented table, so this struct is the third copy and the only one the
//     compiler cannot check against the other two.
//
//  2. Colour is produced HERE, on the GPU, from the packed region code — not
//     precomputed per neuron on the CPU. 153,746 neurons would otherwise need
//     1.8 MB of colour re-uploaded whenever a region filter changes, and the
//     region palette becomes a shader-side lookup instead of per-vertex data
//     (docs/PERFORMANCE.md: "GPU-side color mapping/filtering").
//
//  Brightness is NOT invented here. It is `rate / referenceRateHz` clamped, with
//  `maxActivityLift` as the same bound `RenderPalette` uses, so a silent neuron
//  is exactly its region colour — no ambient floor, no idle shimmer.
//

#include <metal_stdlib>
using namespace metal;

// MARK: - Layout shared with Swift (do not reorder; 32 bytes)
//
// The packed word's fields, as `ConnectomeRenderModel.NeuronInstance` builds
// them. tools/probe_render_packing.py parses THIS table and the Swift source
// and fails if they disagree, so it is a checked statement rather than a note:
//
//   region      5 bits at bit 0    (RegionID case count, grows on its own)
//   side        4 bits at bit 5    (3 values; 4 bits is deliberate slack)
//   provenance  4 bits at bit 9    (INDEX into Provenance.allCases, not raw)
//   classes     2 bits at bit 13   (motor bit 0, sensory bit 1)
//
// Bit 12 inside the provenance field is unused slack: 6 cases need 3 bits, and
// the 4th lets a 7th or 8th case land without moving any other field.

struct NeuronInstance {
    float x;
    float y;
    float z;
    uint  packed;      // region | side | provenance | classes
    uint  typeIndex;
    uint  index;       // neuron array index — the key into the rate buffer
    uint  _pad0;       // reserved, always 0
    uint  _pad1;       // reserved, always 0
};

// MARK: - Per-frame uniforms (16-byte aligned for the 4x4)

struct Uniforms {
    float4x4 viewProjection;
    float    pointSize;         // in points, already scaled for the drawable
    float    referenceRateHz;   // matches RenderActivity.referenceRateHz
    float    maxActivityLift;   // matches RenderPalette.maxActivityLift
    float    _pad0;
};

/// One entry per possible packed region code (5 bits = 32 slots), not per
/// RegionID case. The field is 5 bits wide, so a shader that indexed a
/// 21-entry table would read past the end for any code this build does not
/// know — the same wrap that `RenderPalette.color(region:)` guards against on
/// the CPU. The host pads the table out to 32.
///
/// Four plain floats, deliberately not `float3` + pad: a vector type is padded
/// to its full width, giving `{float3, float}` a 32-byte stride, while Swift's
/// matching struct is four Floats and 16 bytes. The two would disagree by a
/// factor of two and every region would read the wrong colour. Four scalars
/// have no padding rule on either side.
struct RegionColor {
    float r;
    float g;
    float b;
    float a;
};

// MARK: - Pass-through shading

struct VaryingOut {
    float4 position [[position]];
    float  pointSize [[point_size]];
    float3 color;
};

vertex VaryingOut neuron_vertex(
    uint vertexID [[vertex_id]],
    uint instanceID [[instance_id]],
    const device NeuronInstance *neurons [[buffer(0)]],
    const device float          *rates   [[buffer(1)]],
    constant Uniforms           &u       [[buffer(2)]],
    const device RegionColor    *palette [[buffer(3)]])
{
    // One vertex per instance: drawPrimitives(type:.point, vertexStart: 0,
    // vertexCount: 1, instanceCount: neuronCount). `vertexID` is therefore
    // always 0 and is unused — the instance id is the neuron.
    (void)vertexID;

    NeuronInstance inst = neurons[instanceID];

    VaryingOut out;
    out.position = u.viewProjection * float4(inst.x, inst.y, inst.z, 1.0);
    out.pointSize = u.pointSize;

    // Region is the low 5 bits of the packed word, matching
    // `NeuronInstance.region` (`packed & 0x1F`).
    uint region = inst.packed & 0x1Fu;
    RegionColor rc = palette[region];
    float3 base = float3(rc.r, rc.g, rc.b);

    // Activity is looked up by the neuron's array index, which is the only
    // stable identity across frames. Absent indices read 0 from a zeroed
    // buffer, which is the desired "silent" value.
    float rate = rates[inst.index];
    float t = clamp(rate / max(u.referenceRateHz, 1.0), 0.0, 1.0);
    out.color = base + (float3(1.0) - base) * (u.maxActivityLift * t);
    return out;
}

fragment float4 neuron_fragment(VaryingOut in [[stage_in]],
                                float2 pointCoord [[point_coord]])
{
    // Round points. Without this every neuron is a square, and on a cloud this
    // dense the corners merge into a grid that reads as structure that is not
    // there.
    float2 d = pointCoord - float2(0.5);
    float r2 = dot(d, d);
    if (r2 > 0.25) {
        discard_fragment();
    }
    // Soft edge over the outermost ring only: a hard circle looks like a hole
    // when thousands overlap.
    float edge = smoothstep(0.25, 0.16, r2);
    return float4(in.color * (0.55 + 0.45 * edge), 1.0);
}