# -*- coding: utf-8 -*-
"""Facade and depth of a Рядове footprint, and how an apartment layout is compared with them.

A Рядове apartment has windows on one side only. What decides whether a layout fits a footprint is not
"short side / long side" but the two sides the windows give it: the FACADE (along the outside wall, between
the perpendicular inner walls) and the DEPTH (from the outside wall to the inner wall). A layout that is
wide on its window side must not land on a footprint that is narrow on its outside side, whatever the
short/long sizes say.

Everything here is pure: the footprint's outside direction comes from the placement payload
("outsideDirXFt"/"outsideDirYFt", see the footprint picker), a layout's own facade/depth from
layout_orientation (measured in the apartment file once, remembered).
"""

import math

FT_TO_MM = 304.8
# Footprints of one group whose facade or depth differ by more than this are not one orientation.
SPAN_MM = 1000.0
# A layout bigger than the footprint on a side overflows it: that side counts double.
_OVERSIZE_WEIGHT = 2.0


def extents_mm(points_ft, dir_x, dir_y):
    """(along, across) in mm: the extent of the outline along the direction and perpendicular to it;
    None without an outline or a direction."""
    length = math.hypot(dir_x, dir_y)
    if not points_ft or len(points_ft) < 3 or length < 1e-9:
        return None
    unit_x, unit_y = dir_x / length, dir_y / length
    along = [point[0] * unit_x + point[1] * unit_y for point in points_ft]
    across = [-point[0] * unit_y + point[1] * unit_x for point in points_ft]
    return ((max(along) - min(along)) * FT_TO_MM, (max(across) - min(across)) * FT_TO_MM)


def facade_direction(placement):
    """The direction of the footprint's outside side from its placement payload, or None."""
    if not placement:
        return None
    dir_x, dir_y = placement.get("outsideDirXFt"), placement.get("outsideDirYFt")
    if dir_x is None or dir_y is None:
        return None
    return float(dir_x), float(dir_y)


def footprint_dims_mm(window_code, footprint, placement):
    """(facade, depth) in mm of a footprint, only for Рядове ones whose outside side is known."""
    if window_code != "R":
        return None
    direction = facade_direction(placement)
    if direction is None:
        return None
    return extents_mm(footprint, direction[0], direction[1])


def ordered_dims(members, placements):
    """[(facade, depth)] of the members (dicts with "id", "window", "footprint"), or None when any of them
    has none (the others then say nothing either: a group is compared as one)."""
    by_roof = {}
    for placement in placements or []:
        if placement.get("roofId") is not None:
            by_roof[placement["roofId"]] = placement
    result = []
    for member in members:
        dims = footprint_dims_mm(member.get("window"), member.get("footprint"), by_roof.get(member.get("id")))
        if dims is None:
            return None
        result.append(dims)
    return result or None


def consistent(dims_list, span=SPAN_MM):
    """Whether the facades, and the depths, each lie within `span` of one another."""
    for axis in (0, 1):
        values = [dims[axis] for dims in dims_list]
        if max(values) - min(values) > span:
            return False
    return True


def average(dims_list):
    count = float(len(dims_list))
    return (sum(dims[0] for dims in dims_list) / count, sum(dims[1] for dims in dims_list) / count)


def split_by_orientation(members, dims_list, span=SPAN_MM):
    """[(members, dims list)]: the members cut into runs that have one orientation (facade and depth each
    within `span` of every other member's of the run); members without dims stay together at the end with
    None. Without dims for anyone the members stay one run."""
    if not dims_list:
        return [(list(members), None)]
    runs = []
    for member, dims in zip(members, dims_list):
        for run_members, run_dims in runs:
            if all(abs(dims[axis] - other[axis]) <= span for other in run_dims for axis in (0, 1)):
                run_members.append(member)
                run_dims.append(dims)
                break
        else:
            runs.append(([member], [dims]))
    return runs


def _side_cost(delta):
    return abs(delta) * (_OVERSIZE_WEIGHT if delta > 0 else 1.0)


def forward_cost_mm(layout, footprints):
    """How far the layout's (facade, depth) is from the footprints' (facade, depth), averaged: the layout's
    facade is compared with the footprint's facade only, its depth with the depth only."""
    total = 0.0
    for facade, depth in footprints:
        total += _side_cost(layout[0] - facade) + _side_cost(layout[1] - depth)
    return total / len(footprints)


def forward_match(layout, width, length, tolerance):
    """Whether the layout's (facade, depth) is within `tolerance` of (width, length) - the footprint's
    facade and depth - side for side."""
    return abs(layout[0] - width) <= tolerance and abs(layout[1] - length) <= tolerance
