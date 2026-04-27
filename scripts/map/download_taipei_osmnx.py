from __future__ import annotations

import argparse
from pathlib import Path
import sys

import osmnx as ox


DEFAULT_PLACE = "Taipei, Taiwan"
DEFAULT_NETWORK_TYPE = "drive"
DEFAULT_WHICH_RESULT = 1
DEFAULT_MAX_QUERY_AREA_SIZE = 50_000_000
DEFAULT_OVERPASS_URLS = ("https://overpass-api.de/api",)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download an OSMnx street graph and export GraphML/SVG/PNG."
    )
    parser.add_argument(
        "--place",
        default=DEFAULT_PLACE,
        help=f'Place name for OSM geocoding (default: "{DEFAULT_PLACE}")',
    )
    parser.add_argument(
        "--network-type",
        default=DEFAULT_NETWORK_TYPE,
        help=(
            "OSMnx network type, for example drive, walk, bike, all, all_public "
            f'(default: "{DEFAULT_NETWORK_TYPE}")'
        ),
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore existing OSMnx cache files for this run.",
    )
    parser.add_argument(
        "--max-query-area-size",
        type=int,
        default=DEFAULT_MAX_QUERY_AREA_SIZE,
        help=(
            "Maximum area in projected square meters per Overpass sub-query "
            f"(default: {DEFAULT_MAX_QUERY_AREA_SIZE})"
        ),
    )
    parser.add_argument(
        "--overpass-url",
        action="append",
        dest="overpass_urls",
        help=(
            "Custom Overpass base URL. Repeat this flag to provide multiple "
            "fallback endpoints."
        ),
    )
    return parser.parse_args()


def configure_osmnx(cache_dir: Path, refresh_cache: bool, max_query_area_size: int) -> None:
    ox.settings.use_cache = not refresh_cache
    ox.settings.cache_folder = str(cache_dir)
    ox.settings.log_console = True
    ox.settings.requests_timeout = 180
    ox.settings.max_query_area_size = max_query_area_size
    ox.settings.overpass_rate_limit = True


def ensure_non_empty_graph(graph, label: str) -> None:
    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()
    print(f"{label}: {node_count} nodes, {edge_count} edges")
    if node_count == 0 or edge_count == 0:
        raise ValueError(f"{label} returned an empty graph")


def build_graph(place: str, network_type: str, overpass_urls: tuple[str, ...]):
    print(
        f'Fetching "{place}" with network_type="{network_type}" '
        f"and which_result={DEFAULT_WHICH_RESULT}"
    )

    gdf = None
    errors: list[str] = []

    for overpass_url in overpass_urls:
        ox.settings.overpass_url = overpass_url
        print(f"Trying graph_from_place via {overpass_url}")
        try:
            graph = ox.graph_from_place(
                place,
                which_result=DEFAULT_WHICH_RESULT,
                network_type=network_type,
                simplify=True,
                retain_all=False,
            )
            ensure_non_empty_graph(graph, f"graph_from_place ({overpass_url})")
            return graph
        except Exception as exc:
            message = f"graph_from_place via {overpass_url} failed: {exc}"
            print(message)
            errors.append(message)

    try:
        gdf = ox.geocode_to_gdf(place, which_result=DEFAULT_WHICH_RESULT)
    except Exception as exc:
        errors.append(f"geocode_to_gdf failed: {exc}")
        raise RuntimeError(" ; ".join(errors)) from exc

    if gdf.empty:
        raise RuntimeError(f'Could not geocode "{place}" to a polygon boundary')

    geometry = gdf.geometry.iloc[0]
    if geometry is None or geometry.is_empty:
        raise RuntimeError(f'Geocoder returned an empty geometry for "{place}"')

    for overpass_url in overpass_urls:
        ox.settings.overpass_url = overpass_url
        print(f"Trying graph_from_polygon via {overpass_url}")
        try:
            graph = ox.graph_from_polygon(
                geometry,
                network_type=network_type,
                simplify=True,
                retain_all=False,
            )
            ensure_non_empty_graph(graph, f"graph_from_polygon ({overpass_url})")
            return graph
        except Exception as exc:
            message = f"graph_from_polygon via {overpass_url} failed: {exc}"
            print(message)
            errors.append(message)

    raise RuntimeError(" ; ".join(errors))


def save_outputs(graph, graphml_path: Path, svg_path: Path, png_path: Path) -> None:
    graphml_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Saving GraphML to {graphml_path}")
    ox.save_graphml(graph, graphml_path)

    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        print("matplotlib is not installed, skipping SVG/PNG export.")
        return

    print(f"Rendering image outputs to {svg_path} and {png_path}")
    fig, ax = ox.plot_graph(
        graph,
        show=False,
        close=False,
        node_size=0,
        edge_color="black",
        edge_linewidth=0.5,
        bgcolor="white",
    )
    fig.savefig(svg_path, bbox_inches="tight", pad_inches=0, facecolor="white")
    fig.savefig(png_path, dpi=300, bbox_inches="tight", pad_inches=0, facecolor="white")
    plt.close(fig)


def main() -> int:
    args = parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    out_dir = repo_root / "map" / "geography"
    cache_dir = repo_root / "cache"

    graphml_path = out_dir / "taipei_streets.graphml"
    svg_path = out_dir / "taipei_osmnx_streets.svg"
    png_path = out_dir / "taipei_osmnx_streets.png"
    overpass_urls = tuple(args.overpass_urls or DEFAULT_OVERPASS_URLS)

    configure_osmnx(
        cache_dir=cache_dir,
        refresh_cache=args.refresh_cache,
        max_query_area_size=args.max_query_area_size,
    )

    try:
        graph = build_graph(args.place, args.network_type, overpass_urls=overpass_urls)
        save_outputs(graph, graphml_path=graphml_path, svg_path=svg_path, png_path=png_path)
    except Exception as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1

    print("Download completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
