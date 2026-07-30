"""Geometric test for the architecture diagram: no two edges may cross,
and no edge may pass through a node it doesn't connect to.
Parses NODES/EDGES + the routing rules out of frontend/arch.js so the test
follows the source of truth."""

import json
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
        nid, x, y, w, h = m.group(1), *map(int, m.groups()[1:])
        nodes[nid] = {"x": x, "y": y, "w": w, "h": h}
    return nodes


def parse_edges():
    return re.findall(r"\['(\w+)', '(\w+)', '(\w+)'\]", ARCH)


NODES = parse_nodes()
EDGES = parse_edges()
MID_Y = 270  # keep in sync with arch.js vertdiag


def center(n):
    return (n["x"] + n["w"] / 2, n["y"] + n["h"] / 2)


def edge_segments(a, b, kind):
    """Reproduce arch.js edgePath as a list of line segments."""
    A, B = NODES[a], NODES[b]
    (acx, acy), (bcx, bcy) = center(A), center(B)
    if kind == "flow":
        if A["x"] < B["x"]:
            return [((A["x"] + A["w"], acy), (B["x"], bcy))]
        return [((A["x"], acy), (B["x"] + B["w"], bcy))]
    if kind == "ctl":
        top_y = 12
        return [
            ((acx, A["y"]), (acx, top_y)),
            ((acx, top_y), (bcx, top_y)),
            ((bcx, top_y), (bcx, B["y"])),
        ]
    if kind == "vert":
        if acy < bcy:
            return [((acx, A["y"] + A["h"]), (acx, B["y"]))]
        return [((acx, A["y"]), (acx, B["y"] + B["h"]))]
    # vertdiag: down from A, horizontal along MID_Y, into B's right side
    return [
        ((acx, A["y"] + A["h"]), (acx, MID_Y)),
        ((acx, MID_Y), (B["x"] + B["w"], MID_Y)),
        ((B["x"] + B["w"], MID_Y), (B["x"] + B["w"], bcy)),
    ]


def segments_cross(s1, s2):
    """Proper intersection of two segments (shared endpoints don't count)."""
    (x1, y1), (x2, y2) = s1
    (x3, y3), (x4, y4) = s2

    def d(ax, ay, bx, by, cx, cy):
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    d1 = d(x3, y3, x4, y4, x1, y1)
    d2 = d(x3, y3, x4, y4, x2, y2)
    d3 = d(x1, y1, x2, y2, x3, y3)
    d4 = d(x1, y1, x2, y2, x4, y4)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    return False


def segment_hits_rect(seg, rect, margin=2):
    """Does the segment pass through the rectangle interior?"""
    (x1, y1), (x2, y2) = seg
    rx1, ry1 = rect["x"] + margin, rect["y"] + margin
    rx2, ry2 = rect["x"] + rect["w"] - margin, rect["y"] + rect["h"] - margin
    # sample points along the segment
    for t in [i / 20 for i in range(21)]:
        px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        if rx1 < px < rx2 and ry1 < py < ry2:
            return True
    return False


class TestArchGeometry(unittest.TestCase):
    def test_parsed(self):
        self.assertGreaterEqual(len(NODES), 10)
        self.assertGreaterEqual(len(EDGES), 9)

    def test_no_edge_crossings(self):
        all_segs = []
        for a, b, kind in EDGES:
            for seg in edge_segments(a, b, kind):
                all_segs.append((f"{a}->{b}", seg))
        for i in range(len(all_segs)):
            for j in range(i + 1, len(all_segs)):
                n1, s1 = all_segs[i]
                n2, s2 = all_segs[j]
                if n1 == n2:
                    continue
                self.assertFalse(
                    segments_cross(s1, s2),
                    f"edges {n1} and {n2} cross: {s1} x {s2}",
                )

    def test_no_edge_through_unrelated_node(self):
        for a, b, kind in EDGES:
            for seg in edge_segments(a, b, kind):
                for nid, rect in NODES.items():
                    if nid in (a, b):
                        continue
                    self.assertFalse(
                        segment_hits_rect(seg, rect),
                        f"edge {a}->{b} passes through node {nid}",
                    )

    def test_nodes_do_not_overlap(self):
        items = list(NODES.items())
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                n1, r1 = items[i]
                n2, r2 = items[j]
                overlap = not (
                    r1["x"] + r1["w"] <= r2["x"] or r2["x"] + r2["w"] <= r1["x"]
                    or r1["y"] + r1["h"] <= r2["y"] or r2["y"] + r2["h"] <= r1["y"]
                )
                self.assertFalse(overlap, f"nodes {n1} and {n2} overlap")


if __name__ == "__main__":
    unittest.main()
