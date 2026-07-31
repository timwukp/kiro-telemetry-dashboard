"""Geometric test for the architecture diagram: no two edges may cross,
and no edge may pass through a node it doesn't connect to.
Parses NODES/EDGES out of frontend/arch.js (explicit polylines) so the
test follows the source of truth."""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCH = open(os.path.join(ROOT, "frontend", "arch.js")).read()


def parse_nodes():
    nodes = {}
    for m in re.finditer(
        r"\{ id: '(\w+)',\s*x: (\d+),\s*y: (\d+),\s*w: (\d+),\s*h: (\d+)", ARCH
    ):
        nid = m.group(1)
        x, y, w, h = map(int, m.groups()[1:])
        nodes[nid] = {"x": x, "y": y, "w": w, "h": h}
    return nodes


def parse_edges():
    """EDGES entries look like: { pts: [[164, 92], [240, 92]], kind: 'flow' }"""
    edges = []
    for m in re.finditer(r"\{ pts: \[((?:\[\d+,\s*\d+\],?\s*)+)\]", ARCH):
        pts = [(int(a), int(b)) for a, b in re.findall(r"\[(\d+),\s*(\d+)\]", m.group(1))]
        if len(pts) >= 2:
            edges.append(list(zip(pts, pts[1:])))
    return edges


NODES = parse_nodes()
EDGES = parse_edges()


def segments_cross(s1, s2):
    (x1, y1), (x2, y2) = s1
    (x3, y3), (x4, y4) = s2

    def d(ax, ay, bx, by, cx, cy):
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    d1 = d(x3, y3, x4, y4, x1, y1)
    d2 = d(x3, y3, x4, y4, x2, y2)
    d3 = d(x1, y1, x2, y2, x3, y3)
    d4 = d(x1, y1, x2, y2, x4, y4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def segment_hits_rect(seg, rect, margin=2):
    (x1, y1), (x2, y2) = seg
    rx1, ry1 = rect["x"] + margin, rect["y"] + margin
    rx2, ry2 = rect["x"] + rect["w"] - margin, rect["y"] + rect["h"] - margin
    for t in [i / 24 for i in range(25)]:
        px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        if rx1 < px < rx2 and ry1 < py < ry2:
            return True
    return False


def on_boundary(p, rect, tol=3):
    x, y = p
    near_edge = (abs(x - rect["x"]) < tol or abs(x - rect["x"] - rect["w"]) < tol
                 or abs(y - rect["y"]) < tol or abs(y - rect["y"] - rect["h"]) < tol)
    inside_band = (rect["x"] - tol <= x <= rect["x"] + rect["w"] + tol
                   and rect["y"] - tol <= y <= rect["y"] + rect["h"] + tol)
    return near_edge and inside_band


class TestArchGeometry(unittest.TestCase):
    def test_parsed(self):
        self.assertGreaterEqual(len(NODES), 10)
        self.assertGreaterEqual(len(EDGES), 8)

    def test_no_edge_crossings(self):
        flat = [(i, s) for i, segs in enumerate(EDGES) for s in segs]
        for a in range(len(flat)):
            for b in range(a + 1, len(flat)):
                i1, s1 = flat[a]
                i2, s2 = flat[b]
                if i1 == i2:
                    continue
                self.assertFalse(
                    segments_cross(s1, s2),
                    f"edge{i1} {s1} crosses edge{i2} {s2}",
                )

    def test_no_edge_through_unrelated_node(self):
        for i, segs in enumerate(EDGES):
            endpoints = [segs[0][0], segs[-1][1]]
            for s in segs:
                for nid, rect in NODES.items():
                    # exempt nodes this edge starts/ends on
                    if any(on_boundary(p, rect) for p in endpoints):
                        continue
                    self.assertFalse(
                        segment_hits_rect(s, rect),
                        f"edge{i} segment {s} passes through node {nid}",
                    )

    def test_nodes_do_not_overlap(self):
        items = list(NODES.items())
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                n1, r1 = items[a]
                n2, r2 = items[b]
                overlap = not (
                    r1["x"] + r1["w"] <= r2["x"] or r2["x"] + r2["w"] <= r1["x"]
                    or r1["y"] + r1["h"] <= r2["y"] or r2["y"] + r2["h"] <= r1["y"]
                )
                self.assertFalse(overlap, f"nodes {n1} and {n2} overlap")


if __name__ == "__main__":
    unittest.main()
