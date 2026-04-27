from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


def svg_tag(local_name: str) -> str:
    return f"{{{SVG_NS}}}{local_name}"


def slugify(value: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def append_class(node: ET.Element, class_name: str) -> ET.Element:
    existing = node.get("class", "").split()
    if class_name not in existing:
        existing.append(class_name)
    node.set("class", " ".join(filter(None, existing)))
    return node


def numeric_attr(node: ET.Element, name: str) -> float:
    try:
        return float(node.get(name, "0"))
    except ValueError:
        return 0.0


def extract_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def href_value(node: ET.Element) -> str:
    return node.get("href") or node.get(f"{{{XLINK_NS}}}href") or ""


def parse_svg(source: Path) -> ET.ElementTree:
    return ET.parse(source)


def build_output_tree(root: ET.Element) -> tuple[ET.ElementTree, dict]:
    style_tag = svg_tag("style")
    defs_tag = svg_tag("defs")
    line_tag = svg_tag("line")
    text_tag = svg_tag("text")
    marker_tags = {svg_tag("use"), svg_tag("rect"), svg_tag("circle")}

    output_root = ET.Element(svg_tag("svg"), dict(root.attrib))
    output_root.set("data-generated-by", "normalize_metromapmaker_svg.py")
    output_root.set("data-source-kind", "metromapmaker")

    for child in root:
        if child.tag in {style_tag, defs_tag}:
            output_root.append(copy.deepcopy(child))

    extra_style = ET.Element(style_tag)
    extra_style.text = (
        ".interactive-track { vector-effect: non-scaling-stroke; } "
        ".station-node { cursor: pointer; } "
        ".station-label { pointer-events: none; }"
    )
    output_root.append(extra_style)

    tracks_group = ET.SubElement(
        output_root,
        svg_tag("g"),
        {"id": "tracks", "class": "track-layer"},
    )
    stations_group = ET.SubElement(
        output_root,
        svg_tag("g"),
        {"id": "stations", "class": "station-layer"},
    )
    misc_group = ET.SubElement(
        output_root,
        svg_tag("g"),
        {"id": "misc", "class": "misc-layer"},
    )

    body_children = [child for child in root if child.tag not in {style_tag, defs_tag}]
    handled_indexes: set[int] = set()
    name_counts: Counter[str] = Counter()
    track_index = 1
    station_index = 1

    metadata = {
        "view_box": root.get("viewBox"),
        "tracks": [],
        "stations": [],
    }

    for index, child in enumerate(body_children):
        if index in handled_indexes:
            continue

        if child.tag == line_tag:
            class_name = child.get("class", "unclassified")
            track_id = f"track-{class_name}-{track_index:03d}"
            track_index += 1

            line_copy = append_class(copy.deepcopy(child), "interactive-track")
            line_copy.set("id", track_id)
            line_copy.set("data-line-class", class_name)
            tracks_group.append(line_copy)

            metadata["tracks"].append(
                {
                    "svg_id": track_id,
                    "line_class": class_name,
                    "x1": numeric_attr(child, "x1"),
                    "y1": numeric_attr(child, "y1"),
                    "x2": numeric_attr(child, "x2"),
                    "y2": numeric_attr(child, "y2"),
                }
            )
            continue

        if child.tag in marker_tags:
            label_copy: ET.Element | None = None
            if index + 1 < len(body_children) and body_children[index + 1].tag == text_tag:
                label_copy = append_class(copy.deepcopy(body_children[index + 1]), "station-label")
                handled_indexes.add(index + 1)

            station_name = extract_text(label_copy) or f"Station {station_index}"
            slug = slugify(station_name) or f"station-{station_index:03d}"
            name_counts[slug] += 1

            station_id = f"station-{slug}"
            if name_counts[slug] > 1:
                station_id = f"{station_id}-{name_counts[slug]}"

            marker_copy = append_class(copy.deepcopy(child), "station-marker")
            marker_copy.set("id", f"{station_id}--marker")

            station_group = ET.Element(
                svg_tag("g"),
                {
                    "id": station_id,
                    "class": "station-node",
                    "data-station-name": station_name,
                },
            )
            if href_value(child):
                station_group.set("data-marker-ref", href_value(child))

            station_group.append(marker_copy)
            if label_copy is not None:
                label_copy.set("id", f"{station_id}--label")
                station_group.append(label_copy)
            stations_group.append(station_group)

            metadata["stations"].append(
                {
                    "svg_id": station_id,
                    "name": station_name,
                    "marker_ref": href_value(child),
                    "x": numeric_attr(child, "x"),
                    "y": numeric_attr(child, "y"),
                }
            )
            station_index += 1
            continue

        misc_group.append(copy.deepcopy(child))

    ET.indent(output_root)
    return ET.ElementTree(output_root), metadata


def normalize_svg(source: Path, output: Path, mapping: Path) -> None:
    tree = parse_svg(source)
    output_tree, metadata = build_output_tree(tree.getroot())
    output.parent.mkdir(parents=True, exist_ok=True)
    mapping.parent.mkdir(parents=True, exist_ok=True)
    output_tree.write(output, encoding="utf-8", xml_declaration=False)
    mapping.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize a MetroMapMaker SVG into a semantic SVG.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    normalize_svg(args.source, args.output, args.mapping)


if __name__ == "__main__":
    main()
