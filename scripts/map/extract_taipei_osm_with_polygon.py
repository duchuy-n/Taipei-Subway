from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

import osmium
from shapely.geometry import Point
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.prepared import prep


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_PATH = PROJECT_ROOT / "taiwan-260325.osm.pbf"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "map" / "geography" / "taipei-clipped.osm.pbf"
DEFAULT_POLYGON_GLOB_PATTERNS = (
    "OSMB-*.geojson.gz",
    "*.geojson.gz",
    "OSMB-*.geojson",
    "*.geojson",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract a Taipei-only OSM PBF subset with pyosmium and a polygon GeoJSON file."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_PATH,
        help=f"Source OSM PBF file (default: {DEFAULT_INPUT_PATH})",
    )
    parser.add_argument(
        "--polygon",
        type=Path,
        help="Polygon file in .geojson or .geojson.gz format. Defaults to the newest matching file in the repo root.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output clipped OSM PBF file (default: {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )
    parser.add_argument(
        "--keep-unpacked-polygon",
        action="store_true",
        help="Keep the temporary unpacked GeoJSON when the polygon source is .gz.",
    )
    parser.add_argument(
        "--relation-depth",
        type=int,
        default=1,
        help="Nested relation depth for back-reference completion (default: 1).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve files and print extraction settings without writing output.",
    )
    return parser.parse_args()


def find_default_polygon_path(project_root: Path) -> Path:
    candidates: list[Path] = []

    for pattern in DEFAULT_POLYGON_GLOB_PATTERNS:
        candidates.extend(project_root.glob(pattern))

    files = [path for path in candidates if path.is_file()]
    if not files:
        raise FileNotFoundError(
            "No polygon GeoJSON file was found. Pass --polygon explicitly."
        )

    return max(files, key=lambda path: path.stat().st_mtime)


def ensure_input_file(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Input PBF file not found: {resolved}")
    return resolved


def resolve_polygon_path(
    polygon_path: Path,
    *,
    keep_unpacked_polygon: bool,
) -> tuple[Path, Path | None]:
    resolved = polygon_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Polygon file not found: {resolved}")

    if resolved.suffix != ".gz":
        return resolved, None

    unpacked_name = resolved.stem
    unpacked_dir = PROJECT_ROOT / "cache" / "osmium-polygons"
    unpacked_dir.mkdir(parents=True, exist_ok=True)
    unpacked_path = unpacked_dir / unpacked_name

    with gzip.open(resolved, "rb") as source_handle:
        unpacked_path.write_bytes(source_handle.read())

    if keep_unpacked_polygon:
        return unpacked_path, None

    return unpacked_path, unpacked_path


def load_polygon_geometry(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))

    if payload.get("type") == "FeatureCollection":
        geometries = [
            shape(feature["geometry"])
            for feature in payload.get("features", [])
            if feature.get("geometry") is not None
        ]
        if not geometries:
            raise ValueError(f"Polygon file has no geometries: {path}")
        return unary_union(geometries)

    if payload.get("type") == "Feature":
        geometry = payload.get("geometry")
        if geometry is None:
            raise ValueError(f"Polygon feature has no geometry: {path}")
        return shape(geometry)

    return shape(payload)


def remove_existing_output(path: Path, *, overwrite: bool) -> None:
    if not path.exists():
        return
    if not overwrite:
        raise FileExistsError(
            f"Output file already exists: {path}. Pass --overwrite to replace it."
        )
    path.unlink()


def cleanup_temp_file(path: Path | None) -> None:
    if path is None or not path.exists():
        return
    path.unlink()


class PolygonExtractHandler(osmium.SimpleHandler):
    def __init__(self, polygon, writer: osmium.BackReferenceWriter):
        super().__init__()
        self.writer = writer
        self.id_tracker = osmium.IdTracker()
        self.polygon = prep(polygon)
        self.min_x, self.min_y, self.max_x, self.max_y = polygon.bounds
        self.nodes_written = 0
        self.ways_written = 0
        self.relations_written = 0

    def node(self, node) -> None:
        if not node.location.valid():
            return

        lon = node.location.lon
        lat = node.location.lat
        if lon < self.min_x or lon > self.max_x or lat < self.min_y or lat > self.max_y:
            return
        if not self.polygon.covers(Point(lon, lat)):
            return

        self.writer.add_node(node)
        self.id_tracker.add_node(node.id)
        self.nodes_written += 1

    def way(self, way) -> None:
        if not self.id_tracker.contains_any_references(way):
            return

        self.writer.add_way(way)
        self.id_tracker.add_way(way.id)
        self.ways_written += 1

    def relation(self, relation) -> None:
        if not self.id_tracker.contains_any_references(relation):
            return

        self.writer.add_relation(relation)
        self.id_tracker.add_relation(relation.id)
        self.relations_written += 1


def main() -> int:
    args = parse_args()

    polygon_arg = args.polygon if args.polygon else find_default_polygon_path(PROJECT_ROOT)
    input_path = ensure_input_file(args.input)
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_polygon_path: Path | None = None
    try:
        polygon_path, temp_polygon_path = resolve_polygon_path(
            polygon_arg,
            keep_unpacked_polygon=args.keep_unpacked_polygon,
        )
        polygon_geometry = load_polygon_geometry(polygon_path)

        print(f"Input PBF      : {input_path}")
        print(f"Polygon        : {polygon_path}")
        print(f"Output PBF     : {output_path}")
        print(f"Polygon bounds : {polygon_geometry.bounds}")
        print(f"Relation depth : {args.relation_depth}")

        if args.dry_run:
            return 0

        remove_existing_output(output_path, overwrite=args.overwrite)

        with osmium.BackReferenceWriter(
            str(output_path),
            str(input_path),
            overwrite=args.overwrite,
            relation_depth=args.relation_depth,
        ) as writer:
            handler = PolygonExtractHandler(polygon_geometry, writer)
            handler.apply_file(str(input_path))

        print(f"Nodes written  : {handler.nodes_written}")
        print(f"Ways written   : {handler.ways_written}")
        print(f"Relations writ.: {handler.relations_written}")
        print("Extract completed successfully.")
        return 0
    except Exception as exc:
        print(f"Extract failed: {exc}", file=sys.stderr)
        return 1
    finally:
        cleanup_temp_file(temp_polygon_path)


if __name__ == "__main__":
    raise SystemExit(main())
