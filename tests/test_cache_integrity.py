import sys
import os
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app.config import get_settings
from app.services.walk_network import build_walk_graph

def test_cache_integrity():
    settings = get_settings()
    data_file = settings.qgis_geojson_dir / "walk_network.geojson"
    
    if not data_file.exists():
        print(f"Skipping test: {data_file} not found")
        return

    import json
    with open(data_file, "r", encoding="utf-8") as f:
        walk_network_geojson = json.load(f)

    print("Building cold graph...")
    graph_cold = build_walk_graph(walk_network_geojson, settings=None)
    
    print("Building cached graph...")
    # Force a save
    build_walk_graph(walk_network_geojson, settings=settings)
    # Load from cache
    graph_cached = build_walk_graph(None, settings=settings)

    print("Verifying parity...")
    assert len(graph_cold.adjacency) == len(graph_cached.adjacency), "Node count mismatch"
    assert len(graph_cold.spatial_index) == len(graph_cached.spatial_index), "Spatial index cell count mismatch"
    
    # Check a few random nodes
    import random
    nodes = list(graph_cold.adjacency.keys())
    sample_size = min(len(nodes), 100)
    for node in random.sample(nodes, sample_size):
        assert graph_cold.adjacency[node] == graph_cached.adjacency[node], f"Adjacency mismatch for node {node}"
        
    # Check nearest_node consistency
    for _ in range(10):
        test_lon = random.uniform(121.4, 121.7)
        test_lat = random.uniform(24.9, 25.2)
        try:
            node_cold = graph_cold.nearest_node(test_lon, test_lat)
            node_cached = graph_cached.nearest_node(test_lon, test_lat)
            assert node_cold == node_cached, f"Nearest node mismatch for ({test_lon}, {test_lat})"
        except ValueError:
            continue

    print("Integrity test PASSED")

if __name__ == "__main__":
    test_cache_integrity()
