"""CVAT 1.1 *for images* reader/writer. Input and output are always this format
(project decision). We edit the parsed tree in place and write it back, so all
unrelated metadata (project/task/meta blocks) is preserved byte-for-byte."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class Polyline:
    elem: ET.Element            # the live <polyline> node (mutate .set("points", ...))
    points: list[float]         # flat [x1,y1,x2,y2,...]
    label: str
    source: str                 # "manual" | "semi-auto" | "auto"


@dataclass
class Frame:
    elem: ET.Element            # the <image> node
    name: str                   # image file name, e.g. "fassto-sample.png"
    subset: str
    width: int
    height: int
    polylines: list[Polyline]


@dataclass
class Document:
    tree: ET.ElementTree
    frames: list[Frame]

    def write(self, path: str) -> None:
        self.tree.write(path, encoding="utf-8", xml_declaration=True)


def parse_points(s: str) -> list[float]:
    out: list[float] = []
    for pair in s.strip().split(";"):
        if not pair:
            continue
        x, y = pair.split(",")
        out.extend((float(x), float(y)))
    return out


def format_points(points: list[float]) -> str:
    return ";".join(f"{points[i]:.2f},{points[i + 1]:.2f}" for i in range(0, len(points), 2))


def load(xml_path: str) -> Document:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    frames: list[Frame] = []
    for img in root.findall("image"):
        polylines = [
            Polyline(elem=pl, points=parse_points(pl.get("points", "")),
                     label=pl.get("label", ""), source=pl.get("source", ""))
            for pl in img.findall("polyline")
        ]
        frames.append(
            Frame(
                elem=img,
                name=img.get("name", ""),
                subset=img.get("subset", "default"),
                width=int(img.get("width", 0)),
                height=int(img.get("height", 0)),
                polylines=polylines,
            )
        )
    return Document(tree=tree, frames=frames)
