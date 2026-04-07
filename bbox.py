"""
bbox.py
-------
Lightweight bounding box (WGS84) replacing the sentinelhub.BBox dependency.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class BBox:
    min_x: float  # min longitude
    min_y: float  # min latitude
    max_x: float  # max longitude
    max_y: float  # max latitude

    def as_list(self) -> list[float]:
        return [self.min_x, self.min_y, self.max_x, self.max_y]

    def __repr__(self) -> str:
        return (
            f"BBox(min_x={self.min_x}, min_y={self.min_y}, "
            f"max_x={self.max_x}, max_y={self.max_y})"
        )
