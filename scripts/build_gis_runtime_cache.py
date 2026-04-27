from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.routes import _build_gis_route_context_signature
from app.api.routes import get_gis_route_context
from app.services.gis_runtime_artifacts import describe_runtime_artifact_path
from app.services.runtime import get_network


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Return success if the current runtime artifact already exists, otherwise return non-zero without building.",
    )
    args = parser.parse_args()

    signature = _build_gis_route_context_signature()
    artifact_path = describe_runtime_artifact_path(PROJECT_ROOT, signature)
    if args.check_only:
        if artifact_path.exists():
            print(f"GIS runtime artifact already exists: {artifact_path}")
            return 0
        print(f"GIS runtime artifact missing: {artifact_path}")
        return 1

    network = get_network()
    context = get_gis_route_context(network)

    print("GIS runtime artifact ready")
    print(f"path={artifact_path}")
    print(f"walk_targets={len(context.walk_targets_by_node)}")
    print(f"segment_index={len(context.geojson_segment_index)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
