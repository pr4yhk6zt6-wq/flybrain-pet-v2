//
//  Connectome.swift
//  FlyBrainCore
//
//  Compact, cache-friendly connectome representation (spec #5).
//  - Contiguous struct arrays (no per-object heap allocations)
//  - CSR-like adjacency via per-neuron start/count index ranges
//  - Typed integer indices, all synapse metadata in one array
//  - Loaded from the packed binary format produced by python/flybrain/pack.py
//

import Foundation

/// Compact neuron record. 64 bytes on x86_64, 56 on arm64.
public struct NeuronRecord {
    public var canonicalID: Int32      // stable ID across datasets (index = position in array)
    public var datasetID: UInt8        // DatasetID raw
    public var type: UInt16            // CellType raw (u16: the real BANC
                                       // release has 11,566 distinct cell
                                       // types, so a u8 vocabulary would
                                       // silently alias them into 256 bins)
    public var region: UInt8           // RegionID raw
    public var side: UInt8             // 0=center,1=left,2=right
    public var transmitter: UInt8      // TransmitterType raw
    public var provenance: UInt8       // Provenance raw
    public var morphologyIndex: Int32  // -1 = none
    public var incomingStart: Int32
    public var incomingCount: Int32
    public var outgoingStart: Int32
    public var outgoingCount: Int32
    public var x: Float
    public var y: Float
    public var z: Float

    public init(canonicalID: Int32, datasetID: UInt8, type: UInt16, region: UInt8,
                side: UInt8, transmitter: UInt8, provenance: UInt8,
                morphologyIndex: Int32, incomingStart: Int32, incomingCount: Int32,
                outgoingStart: Int32, outgoingCount: Int32,
                x: Float, y: Float, z: Float) {
        self.canonicalID = canonicalID
        self.datasetID = datasetID
        self.type = type
        self.region = region
        self.side = side
        self.transmitter = transmitter
        self.provenance = provenance
        self.morphologyIndex = morphologyIndex
        self.incomingStart = incomingStart
        self.incomingCount = incomingCount
        self.outgoingStart = outgoingStart
        self.outgoingCount = outgoingCount
        self.x = x; self.y = y; self.z = z
    }
}

/// Synapse/edge record (spec #7). `estimatedEfficacy` is ALWAYS INFERRED —
/// never presented as measured physiology.
public struct SynapseRecord {
    public var preNeuron: Int32
    public var postNeuron: Int32
    public var synapseCount: UInt16     // anatomical connection count from EM
    public var transmitter: UInt8       // TransmitterType raw
    public var sign: Int8               // SynapseSign raw
    public var confidence: UInt8        // 0..100 (Provenance detail)
    public var delaySteps: UInt8        // conduction delay in simulation steps
    public var estimatedEfficacy: Float // INFERRED physiological gain

    public init(preNeuron: Int32, postNeuron: Int32, synapseCount: UInt16,
                transmitter: UInt8, sign: Int8, confidence: UInt8,
                delaySteps: UInt8, estimatedEfficacy: Float) {
        self.preNeuron = preNeuron
        self.postNeuron = postNeuron
        self.synapseCount = synapseCount
        self.transmitter = transmitter
        self.sign = sign
        self.confidence = confidence
        self.delaySteps = delaySteps
        self.estimatedEfficacy = estimatedEfficacy
    }
}

/// One per-neuron outgoing connection: target index range into `synapses`.
public struct OutEdgeRange {
    public var start: Int32
    public var count: Int32
}

/// Region axis-aligned bounds, used for LOD scheduling and culling.
public struct RegionBounds: Codable, Sendable {
    public var region: UInt8
    public var minX: Float, minY: Float, minZ: Float
    public var maxX: Float, maxY: Float, maxZ: Float
}

/// Header of the packed binary connectome asset (.fbpack).
public struct ConnectomeHeader: Codable {
    public var magic: UInt32            // 0x46425031 "FBP1"
    public var version: UInt32
    public var flags: UInt32
    public var neuronCount: Int32
    public var synapseCount: Int32
    public var morphologyCount: Int32
    public var regionCount: Int32
    public var organism: OrganismInfo
    public var sourceDatasets: [String]
    public var dataProvenance: String
    public var generationDate: String
    public var generatedBy: String
    public var description: String

    public init(magic: UInt32, version: UInt32 = 2, flags: UInt32 = 0,
                neuronCount: Int32, synapseCount: Int32, morphologyCount: Int32,
                regionCount: Int32, organism: OrganismInfo,
                sourceDatasets: [String], dataProvenance: String,
                generationDate: String, generatedBy: String, description: String) {
        self.magic = magic
        self.version = version
        self.flags = flags
        self.neuronCount = neuronCount
        self.synapseCount = synapseCount
        self.morphologyCount = morphologyCount
        self.regionCount = regionCount
        self.organism = organism
        self.sourceDatasets = sourceDatasets
        self.dataProvenance = dataProvenance
        self.generationDate = generationDate
        self.generatedBy = generatedBy
        self.description = description
    }
}

/// Errors that can arise while loading a connectome asset (spec #114).
public enum ConnectomeError: Error, Sendable {
    case badMagic
    case unsupportedVersion(UInt32)
    case corrupt(String)
    case missingData(String)

    public var localizedDescription: String {
        switch self {
        case .badMagic: return "Not a FlyBrain connectome asset (bad magic)"
        case .unsupportedVersion(let v): return "Unsupported connectome format version \(v)"
        case .corrupt(let msg): return "Corrupt connectome asset: \(msg)"
        case .missingData(let msg): return "Missing connectome data: \(msg)"
        }
    }
}

/// The immutable, heavily-optimized connectome graph (spec #5, #117).
/// Static topology is shared across flies; dynamic state lives elsewhere.
public final class Connectome: @unchecked Sendable {
    public let header: ConnectomeHeader
    public private(set) var neurons: [NeuronRecord] = []
    public private(set) var synapses: [SynapseRecord] = []
    public private(set) var outgoingRanges: [OutEdgeRange] = []
    public private(set) var regionBounds: [RegionBounds] = []

    /// Convenience lookup: neuron index by canonicalID (built once).
    public private(set) var indexByCanonicalID: [Int32: Int32] = [:]

    public init(header: ConnectomeHeader) {
        self.header = header
    }

    public var neuronCount: Int { neurons.count }
    public var synapseCount: Int { synapses.count }

    /// Total incoming index space (sum of incomingCount).
    public var totalIncomingSlots: Int { neurons.reduce(0) { $0 + Int($1.incomingCount) } }

    // MARK: - Build & validate

    /// Append a neuron and keep index maps consistent. Call `finishBuild()` after.
    @discardableResult
    public func appendNeuron(_ n: NeuronRecord) -> Bool {
        guard indexByCanonicalID[n.canonicalID] == nil else { return false }
        indexByCanonicalID[n.canonicalID] = Int32(neurons.count)
        neurons.append(n)
        return true
    }

    public func appendSynapse(_ s: SynapseRecord) { synapses.append(s) }

    public func setOutgoingRanges(_ r: [OutEdgeRange]) { outgoingRanges = r }

    /// Post-build validation (spec #90): duplicate IDs, orphan edges, invalid indexes.
    /// Returns list of problems; caller decides severity.
    public func validate() -> [String] {
        var problems: [String] = []
        if neurons.isEmpty { problems.append("no neurons") }
        let n = neurons.count
        let s = synapses.count
        for (i, nrn) in neurons.enumerated() {
            if nrn.canonicalID != i { problems.append("canonicalID mismatch at \(i)") }
            if nrn.outgoingStart > Int32(s) || nrn.outgoingStart + nrn.outgoingCount > Int32(s) {
                problems.append("neuron \(i): outgoing range out of bounds")
            }
            if nrn.region >= RegionID.allCases.count { problems.append("neuron \(i): bad region \(nrn.region)") }
            if nrn.transmitter >= TransmitterType.allCases.count { problems.append("neuron \(i): bad transmitter") }
            if nrn.provenance >= Provenance.allCases.count { problems.append("neuron \(i): bad provenance") }
        }
        for (i, syn) in synapses.enumerated() {
            if syn.preNeuron < 0 || syn.preNeuron >= n { problems.append("synapse \(i): bad preNeuron") }
            if syn.postNeuron < 0 || syn.postNeuron >= n { problems.append("synapse \(i): bad postNeuron") }
            if syn.synapseCount == 0 { problems.append("synapse \(i): zero synapseCount") }
            if syn.confidence > 100 { problems.append("synapse \(i): confidence > 100") }
        }
        if outgoingRanges.count != n { problems.append("outgoingRanges count mismatch") }
        // Each edge must be listed by exactly its own presynaptic neuron. Ranges
        // built before the synapse array is filled (or a stale index) silently
        // make neurons non-partitioning: two neurons then emit the same edge,
        // and some presynaptic cells lose theirs entirely — a graph that still
        // simulates, just wrong (caught a real test bug).
        var owners = [Int](repeating: 0, count: s)
        for (i, r) in outgoingRanges.enumerated() where i < n {
            for k in Int(r.start)..<(Int(r.start) + Int(r.count)) {
                guard k >= 0 && k < s else { continue }
                owners[k] += 1
                if synapses[k].preNeuron != Int32(i) {
                    problems.append("synapse \(k) owned by neuron \(i) but preNeuron is \(synapses[k].preNeuron)")
                }
            }
        }
        for (k, count) in owners.enumerated() where count != 1 {
            problems.append("synapse \(k) referenced by \(count) neurons (must be exactly 1)")
        }
        return problems
    }

    /// Validate the incoming ranges.
    /// The .fbpack format stores NO incoming index block — only outgoing edges
    /// (neuron/synapse/range/region). Assets therefore declare incomingCount=0
    /// for every neuron ("no incoming CSR is stored"), and this passes.
    ///
    /// If an asset DID declare non-zero incoming counts, `incomingStart` would
    /// have to be the prefix sum of those counts in neuron order, because that
    /// is what a CSR reader derives its offset from; the previous single-run
    /// counter accepted a layout with no gaps OR overlaps only by accident and
    /// would reject a legitimate CSR whose zero-count neurons sit mid-table.
    /// It also had to be told separately that a synthetic demo with all-zero
    /// counts "passes", which is the same statement as above.
    public func validateCSR() -> [String] {
        var problems: [String] = []
        var expected = 0
        for nrn in neurons {
            if nrn.incomingCount > 0 && nrn.incomingStart != expected {
                problems.append("incoming CSR gap at \(nrn.canonicalID): "
                                + "start \(nrn.incomingStart) != \(expected)")
            }
            expected += Int(max(nrn.incomingCount, 0))
        }
        if expected > synapses.count {
            problems.append("incoming CSR total \(expected) exceeds \(synapses.count) synapses")
        }
        return problems
    }

    /// Recompute region bounds (used by renderer culling & LOD).
    public func buildRegionBounds() {
        regionBounds = deriveRegionBounds()
    }

    /// Set region bounds directly (used by the asset loader, which reads them
    /// from the file rather than recomputing them from soma positions).
    public func setRegionBounds(_ bounds: [RegionBounds]) {
        regionBounds = bounds
    }

    /// Derive region bounds from the neurons currently held (pure; does not
    /// mutate). `buildRegionBounds` publishes the result; the packer uses it to
    /// fill `header.regionCount` without touching the stored property.
    private func deriveRegionBounds() -> [RegionBounds] {
        var minV: [UInt8: (Float, Float, Float)] = [:]
        var maxV: [UInt8: (Float, Float, Float)] = [:]
        for nrn in neurons {
            let r = nrn.region
            let c = minV[r, default: (Float.greatestFiniteMagnitude, Float.greatestFiniteMagnitude, Float.greatestFiniteMagnitude)]
            minV[r] = (min(c.0, nrn.x), min(c.1, nrn.y), min(c.2, nrn.z))
            let m = maxV[r, default: (-Float.greatestFiniteMagnitude, -Float.greatestFiniteMagnitude, -Float.greatestFiniteMagnitude)]
            maxV[r] = (max(m.0, nrn.x), max(m.1, nrn.y), max(m.2, nrn.z))
        }
        return minV.keys.map { r in
            RegionBounds(region: r, minX: minV[r]!.0, minY: minV[r]!.1, minZ: minV[r]!.2,
                         maxX: maxV[r]!.0, maxY: maxV[r]!.1, maxZ: maxV[r]!.2)
        }.sorted { $0.region < $1.region }
    }

    // MARK: - Query helpers (used by path tracing / inspector)

    /// Outgoing synapses of neuron `i` (index into `synapses`).
    public func outgoingRange(of i: Int) -> Range<Int> {
        guard i >= 0 && i < outgoingRanges.count else { return 0..<0 }
        let r = outgoingRanges[i]
        let start = Int(r.start), count = Int(r.count)
        guard start >= 0, count >= 0, start + count <= synapses.count else { return 0..<0 }
        return start..<(start + count)
    }

    /// Outgoing postsynaptic neuron indices.
    public func outgoingTargets(of i: Int) -> [Int32] {
        outgoingRange(of: i).map { synapses[$0].postNeuron }
    }

    /// Incoming synapses of neuron `i`.
    public func incomingSynapses(of i: Int) -> [(Int32, SynapseRecord)] {
        guard i >= 0 && i < neurons.count else { return [] }
        let nrn = neurons[i]
        let start = Int(nrn.incomingStart), count = Int(nrn.incomingCount)
        guard start >= 0, count >= 0, start + count <= synapses.count else { return [] }
        var result: [(Int32, SynapseRecord)] = []
        result.reserveCapacity(count)
        for k in start..<(start + count) {
            result.append((synapses[k].preNeuron, synapses[k]))
        }
        return result
    }

    // MARK: - Serialization (.fbpack)
    //
    // Wire format (little-endian, fixed offsets — NOT dependent on Swift
    // struct layout; mirrored byte-for-byte by python/flybrain/pack.py):
    //
    //   [u64 hdrLen][header JSON padded to 16]
    //   [u64 neuronBytes][NeuronRecord × neuronCount]      stride 44
    //   [u64 synapseBytes][SynapseRecord × synapseCount]   stride 20
    //   [u64 rangeBytes][OutEdgeRange × neuronCount]       stride 8
    //   [u64 regionBytes][RegionBounds × regionCount]      stride 28
    //
    // NeuronRecord (44, v2): i32 canonicalID; u8 datasetID,region,side,
    //   transmitter,provenance; u8 flags; u16 type; i32 morphologyIndex,
    //   incomingStart, incomingCount, outgoingStart, outgoingCount; f32 x,y,z
    //   offsets: 0, 4, 5, 6, 7, 8, 9, 10, 12, 16, 20, 24, 28, 32, 36, 40
    // SynapseRecord (20): i32 preNeuron, postNeuron; u16 synapseCount;
    //   u8 transmitter; i8 sign; u8 confidence, delaySteps; pad2; f32 efficacy
    // OutEdgeRange (8): i32 start, count
    // RegionBounds (28): u8 region; pad3; f32 minX,minY,minZ,maxX,maxY,maxZ

    private func appendU64(_ v: UInt64, to d: inout Data) { d.append(contentsOf: withUnsafeBytes(of: v.littleEndian) { Data($0) }) }
    private func appendI32(_ v: Int32, to d: inout Data) { d.append(contentsOf: withUnsafeBytes(of: v.littleEndian) { Data($0) }) }
    private func appendU16(_ v: UInt16, to d: inout Data) { d.append(contentsOf: withUnsafeBytes(of: v.littleEndian) { Data($0) }) }
    private func appendU8(_ v: UInt8, to d: inout Data) { d.append(v) }
    private func appendF32(_ v: Float, to d: inout Data) { d.append(contentsOf: withUnsafeBytes(of: v.bitPattern.littleEndian) { Data($0) }) }

    private func appendNeuronRecord(_ n: NeuronRecord, to d: inout Data) {
        // v2 layout: i32 id; u8 datasetID,region,side,transmitter,provenance;
        // u8 flags; u16 type; i32 morphology/in-out starts+counts; 3×f32.
        appendI32(n.canonicalID, to: &d)
        appendU8(n.datasetID, to: &d)
        appendU8(n.region, to: &d); appendU8(n.side, to: &d)
        appendU8(n.transmitter, to: &d); appendU8(n.provenance, to: &d)
        appendU8(0, to: &d)                               // flags (reserved)
        appendU16(n.type, to: &d)
        appendI32(n.morphologyIndex, to: &d)
        appendI32(n.incomingStart, to: &d); appendI32(n.incomingCount, to: &d)
        appendI32(n.outgoingStart, to: &d); appendI32(n.outgoingCount, to: &d)
        appendF32(n.x, to: &d); appendF32(n.y, to: &d); appendF32(n.z, to: &d)
    }

    private func appendSynapseRecord(_ s: SynapseRecord, to d: inout Data) {
        appendI32(s.preNeuron, to: &d); appendI32(s.postNeuron, to: &d)
        appendU16(s.synapseCount, to: &d)
        appendU8(s.transmitter, to: &d)
        d.append(UInt8(bitPattern: s.sign))
        appendU8(s.confidence, to: &d); appendU8(s.delaySteps, to: &d)
        d.append(contentsOf: [0, 0])                      // pad 2
        appendF32(s.estimatedEfficacy, to: &d)
    }

    private func appendRangeRecord(_ r: OutEdgeRange, to d: inout Data) {
        appendI32(r.start, to: &d); appendI32(r.count, to: &d)
    }

    private func appendRegionRecord(_ r: RegionBounds, to d: inout Data) {
        appendU8(r.region, to: &d)
        d.append(contentsOf: [0, 0, 0])                   // pad 3
        appendF32(r.minX, to: &d); appendF32(r.minY, to: &d); appendF32(r.minZ, to: &d)
        appendF32(r.maxX, to: &d); appendF32(r.maxY, to: &d); appendF32(r.maxZ, to: &d)
    }

    /// Write the connectome to a packed binary asset.
    public func writeFBPack(to url: URL) throws {
        var data = Data()
        var header = header
        header.neuronCount = Int32(neurons.count)
        header.synapseCount = Int32(synapses.count)
        header.regionCount = Int32(regionBounds.count)

        let enc = JSONEncoder()
        var hdrData = try enc.encode(header)
        while hdrData.count % 16 != 0 { hdrData.append(0) }
        appendU64(UInt64(hdrData.count), to: &data)
        data.append(hdrData)

        var nd = Data(capacity: neurons.count * 44)
        neurons.forEach { appendNeuronRecord($0, to: &nd) }
        appendU64(UInt64(nd.count), to: &data); data.append(nd)

        var sd = Data(capacity: synapses.count * 20)
        synapses.forEach { appendSynapseRecord($0, to: &sd) }
        appendU64(UInt64(sd.count), to: &data); data.append(sd)

        var od = Data(capacity: outgoingRanges.count * 8)
        outgoingRanges.forEach { appendRangeRecord($0, to: &od) }
        appendU64(UInt64(od.count), to: &data); data.append(od)

        var rd = Data(capacity: regionBounds.count * 28)
        regionBounds.forEach { appendRegionRecord($0, to: &rd) }
        appendU64(UInt64(rd.count), to: &data); data.append(rd)

        try data.write(to: url, options: .atomic)
    }

    /// Load a connectome from a packed binary asset (spec #5, #51).
    public static func loadFBPack(from url: URL) throws -> Connectome {
        let data = try Data(contentsOf: url)
        return try parseFBPack(data)
    }

    public static func parseFBPack(_ data: Data) throws -> Connectome {
        var cursor = 0
        func readU64() throws -> UInt64 {
            guard cursor + 8 <= data.count else { throw ConnectomeError.corrupt("truncated length") }
            let v = data.subdata(in: cursor..<(cursor + 8)).withUnsafeBytes { $0.loadUnaligned(as: UInt64.self) }
            cursor += 8
            return UInt64(littleEndian: v)
        }
        func readBlockBytes() throws -> Data {
            let len = try readU64()
            guard cursor + Int(len) <= data.count else { throw ConnectomeError.corrupt("truncated block") }
            let d = data.subdata(in: cursor..<(cursor + Int(len)))
            cursor += Int(len)
            return d
        }

        // Header
        let hdrData = try readBlockBytes()
        // The Python packer pads header JSON to a 16-byte boundary with NULs;
        // JSONDecoder rejects trailing bytes, so trim them first.
        var hdrClean = hdrData
        while hdrClean.last == 0 { hdrClean.removeLast() }
        let header = try JSONDecoder().decode(ConnectomeHeader.self, from: hdrClean)
        guard header.magic == 0x46425031 else { throw ConnectomeError.badMagic }
        // v2 widened NeuronRecord.type from u8 to u16. A v1 asset would be
        // parsed with every neuron taking its region byte as the cell type, so
        // it is rejected instead of silently misread.
        guard header.version == 2 else { throw ConnectomeError.unsupportedVersion(header.version) }

        // Neurons
        let neuronData = try readBlockBytes()
        guard neuronData.count % 44 == 0 else { throw ConnectomeError.corrupt("neuron block alignment") }
        var neurons: [NeuronRecord] = []
        neurons.reserveCapacity(neuronData.count / 44)
        var off = 0
        while off + 44 <= neuronData.count {
            func i32(_ o: Int) -> Int32 { Int32(littleEndian: neuronData.subdata(in: (off+o)..<(off+o+4)).withUnsafeBytes { $0.loadUnaligned(as: Int32.self) }) }
            func f32(_ o: Int) -> Float { Float(bitPattern: UInt32(littleEndian: neuronData.subdata(in: (off+o)..<(off+o+4)).withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) })) }
            func u8(_ o: Int) -> UInt8 { neuronData[off + o] }
            let n = NeuronRecord(
                canonicalID: i32(0), datasetID: u8(4), type: u16(10), region: u8(5),
                // v2 field order after `region` is side, transmitter, provenance,
                // then a reserved flags byte, then the u16 type. The reader used
                // to take type from offset 6 and wind side/transmitter/provenance
                // back by one, so on EVERY asset it returned a bogus type
                // (e.g. 770 instead of 4660) plus the wrong side, transmitter and
                // provenance — both writers (flybrain/pack.py, packNeuronRecord)
                // put them at 6/7/8 with type at 10.
                side: u8(6), transmitter: u8(7), provenance: u8(8),
                morphologyIndex: i32(12), incomingStart: i32(16), incomingCount: i32(20),
                outgoingStart: i32(24), outgoingCount: i32(28),
                x: f32(32), y: f32(36), z: f32(40))
            neurons.append(n)
            off += 44
        }

        // Synapses
        let synapseData = try readBlockBytes()
        guard synapseData.count % 20 == 0 else { throw ConnectomeError.corrupt("synapse block alignment") }
        var synapses: [SynapseRecord] = []
        synapses.reserveCapacity(synapseData.count / 20)
        off = 0
        while off + 20 <= synapseData.count {
            func i32(_ o: Int) -> Int32 { Int32(littleEndian: synapseData.subdata(in: (off+o)..<(off+o+4)).withUnsafeBytes { $0.loadUnaligned(as: Int32.self) }) }
            func f32(_ o: Int) -> Float { Float(bitPattern: UInt32(littleEndian: synapseData.subdata(in: (off+o)..<(off+o+4)).withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) })) }
            func u8(_ o: Int) -> UInt8 { synapseData[off + o] }
            func u16(_ o: Int) -> UInt16 { UInt16(littleEndian: synapseData.subdata(in: (off+o)..<(off+o+2)).withUnsafeBytes { $0.loadUnaligned(as: UInt16.self) }) }
            let s = SynapseRecord(
                preNeuron: i32(0), postNeuron: i32(4), synapseCount: u16(8),
                transmitter: u8(10), sign: Int8(bitPattern: u8(11)),
                confidence: u8(12), delaySteps: u8(13),
                estimatedEfficacy: f32(16))
            synapses.append(s)
            off += 20
        }

        // Outgoing ranges
        let rangeData = try readBlockBytes()
        guard rangeData.count % 8 == 0 else { throw ConnectomeError.corrupt("range block alignment") }
        var ranges: [OutEdgeRange] = []
        ranges.reserveCapacity(rangeData.count / 8)
        off = 0
        while off + 8 <= rangeData.count {
            func i32(_ o: Int) -> Int32 { Int32(littleEndian: rangeData.subdata(in: (off+o)..<(off+o+4)).withUnsafeBytes { $0.loadUnaligned(as: Int32.self) }) }
            ranges.append(OutEdgeRange(start: i32(0), count: i32(4)))
            off += 8
        }

        // Region bounds
        let regionData = try readBlockBytes()
        var regions: [RegionBounds] = []
        // 28 bytes per record (u8 + 3 pad + 6 f32), matching flybrain/pack.py.
        if regionData.count % 28 != 0 { throw ConnectomeError.corrupt("region block alignment") }
        off = 0
        while off + 28 <= regionData.count {
            func f32(_ o: Int) -> Float { Float(bitPattern: UInt32(littleEndian: regionData.subdata(in: (off+o)..<(off+o+4)).withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) })) }
            regions.append(RegionBounds(region: regionData[off], minX: f32(4), minY: f32(8), minZ: f32(12),
                                        maxX: f32(16), maxY: f32(20), maxZ: f32(24)))
            // 28 bytes per record, NOT 32. Advancing 32 walked off the end of
            // the block and (when the block was long enough) fed the parser the
            // next record's region byte as this one's padding, so region
            // bounds came back scrambled or the block was rejected outright.
            off += 28
        }

        guard neurons.count == header.neuronCount else {
            throw ConnectomeError.corrupt("neuron count mismatch")
        }
        guard synapses.count == header.synapseCount else {
            throw ConnectomeError.corrupt("synapse count mismatch")
        }
        guard ranges.count == header.neuronCount else {
            throw ConnectomeError.corrupt("outgoing range count mismatch")
        }

        let c = Connectome(header: header)
        c.neurons = neurons
        c.synapses = synapses
        c.outgoingRanges = ranges
        c.regionBounds = regions
        for (i, n) in neurons.enumerated() {
            c.indexByCanonicalID[n.canonicalID] = Int32(i)
        }
        return c
    }
}

extension RegionID {
    /// Filtering helper: does this neuron belong to the given region set?
    public static func matches(_ regionRaw: UInt8, in set: Set<RegionID>) -> Bool {
        set.contains(RegionID(rawValue: Int(regionRaw)) ?? .unknown)
    }
}