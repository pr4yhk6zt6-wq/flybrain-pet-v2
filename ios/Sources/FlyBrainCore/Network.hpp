//
//  Network.hpp
//  FlyBrainCore
//
//  Network graph for weight/bias/target management.
//  For now the authoritative topology lives in `Connectome` (packed binary,
//  sparse, provenance-aware). This header documents the C++-style interface
//  that the MotorSystem / gait planner will use when the full body+muscle
//  graph lands (Phase 5–7).
//

#ifndef Network_hpp
#define Network_hpp

#include <cstdint>
#include <vector>

namespace flybrain {

/// A directed edge with a scalar weight (physiological gain, INFERRED).
struct Edge {
    std::uint32_t pre;
    std::uint32_t post;
    float weight;
};

/// Sparse adjacency list graph used by motor planning and body-loop code.
class Network {
public:
    explicit Network(std::uint32_t nodeCount);

    void addEdge(std::uint32_t pre, std::uint32_t post, float weight);
    const std::vector<Edge>& edges() const { return edges_; }
    std::size_t nodeCount() const { return nodeCount_; }

private:
    std::uint32_t nodeCount_;
    std::vector<Edge> edges_;
};

} // namespace flybrain

#endif /* Network_hpp */