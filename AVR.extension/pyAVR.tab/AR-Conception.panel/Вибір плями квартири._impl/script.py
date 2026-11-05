# -*- coding: utf-8 -*-
"""
Вибір плями квартири.

Prompts the user to pick a roof element on the active view representing
an apartment footprint ("пляма"). Only roofs whose type name matches one
of the recognised apartment-footprint boundary types are accepted:

    1k boundary - 400mm
    2k boundary - 400mm
    3k boundary - 400mm

After a valid pick, a message shows the roof's type name, element id and
area (in square metres). Press the button again to pick another
footprint - each press performs exactly one pick-and-report cycle.
"""

import copy
import math
import os
import sys

# See "Select Apartment.pushbutton\script.py"'s own __persistentengine__
# comment - both buttons that can create/reuse the "Банк квартир" window
# set this the same way, keeping this one a persistent, well-documented
# pyRevit engine rather than a fresh one per click.
__persistentengine__ = True

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    Color,
    CurveLoop,
    Element,
    ElementId,
    FilledRegion,
    FilledRegionType,
    FilteredElementCollector,
    Level,
    Line,
    OverrideGraphicSettings,
    Options,
    PlanarFace,
    Solid,
    Transaction,
    UnitTypeId,
    UnitUtils,
    ViewDetailLevel,
    XYZ,
)
from Autodesk.Revit.UI import TaskDialog
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from System.Collections.Generic import List

try:
    from Autodesk.Revit.Exceptions import OperationCanceledException
except Exception:
    OperationCanceledException = Exception

from pyrevit import revit, script

logger = script.get_logger()

uidoc = revit.uidoc
doc = revit.doc

# The bridge to the "Банк квартир" window lives in the extension-level
# lib folder, shared across every bundle in this extension - pyRevit
# adds it automatically, but append it explicitly too in case that
# auto-discovery ever changes.
_BUNDLE_DIR = os.path.dirname(__file__)
_EXTENSION_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_BUNDLE_DIR)))
_EXTENSION_LIB_DIR = os.path.join(_EXTENSION_DIR, "lib")

if _EXTENSION_LIB_DIR not in sys.path:
    sys.path.append(_EXTENSION_LIB_DIR)

try:
    import dkui_bridge
except Exception:
    dkui_bridge = None

try:
    import apartment_bank_window
except Exception:
    apartment_bank_window = None

import backend_api
import facade_fit

BOUNDARY_TYPE_NAMES = [
    "1k boundary - 400mm",
    "2k boundary - 400mm",
    "3k boundary - 400mm",
]

_NORMALISED_BOUNDARY_TYPE_NAMES = [
    name.strip().lower() for name in BOUNDARY_TYPE_NAMES
]


class _RoofSelectionFilter(ISelectionFilter):
    """Restrict picking to roof elements only. While "Розпланування поверху"
    is active, the grey/green footprint regions count too (a click on one is
    a click on the footprint under it - see pick_apartment_footprint)."""

    def __init__(self):
        try:
            self._region_to_roof = dkui_bridge.get_floor_region_map() if dkui_bridge is not None else {}
        except Exception:
            self._region_to_roof = {}

    def AllowElement(self, element):
        try:
            if self._region_to_roof and _element_id_int(element.Id) in self._region_to_roof:
                return True
        except Exception:
            pass

        try:
            category = element.Category
            return (
                category is not None
                and _element_id_int(category.Id) == int(BuiltInCategory.OST_Roofs)
            )
        except Exception:
            return False

    def AllowReference(self, reference, position):
        return True


def _element_id_int(element_id):
    # Revit 2024 introduced ElementId.Value (Int64) alongside the older,
    # now-deprecated IntegerValue; support either.
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def _element_name(element):
    if element is None:
        return ""

    # Element.Name is an explicit interface property in the Revit API,
    # which IronPython's normal attribute lookup can't see - accessing it
    # as element.Name raises "AttributeError: Name". Going through the
    # base Element.Name property descriptor works around that.
    try:
        return Element.Name.__get__(element)
    except Exception:
        try:
            return element.Name
        except Exception:
            return ""


def _area_in_square_meters(element):
    area_parameter = element.LookupParameter("Area")

    if area_parameter is None or not area_parameter.HasValue:
        try:
            area_parameter = element.get_Parameter(
                BuiltInParameter.HOST_AREA_COMPUTED
            )
        except Exception:
            area_parameter = None

    if area_parameter is None or not area_parameter.HasValue:
        return None

    internal_value = area_parameter.AsDouble()

    try:
        return UnitUtils.ConvertFromInternalUnits(
            internal_value, UnitTypeId.SquareMeters
        )
    except Exception:
        # Fallback for older Revit unit APIs (sq ft -> sq m).
        return internal_value * 0.09290304


# ============================================================
# WINDOW PLACEMENT CLASSIFICATION (geometry-based)
#
# Determines, for each side of the picked footprint's outer polygon,
# whether it is "inside" (another roof - of any type, not just the
# boundary types - sits right past it, i.e. a shared apartment wall)
# or "outside" (nothing there, so that side is an exterior wall with
# windows). The count and arrangement of outside sides then maps to
# one of the four window-placement classifications.
# ============================================================


# The adjacency test is otherwise purely 2D (X/Y): without this, a roof
# stacked directly above/below the picked one on a different floor - with
# the same X/Y footprint - would wrongly count as a neighbour on every
# side. Only candidates whose lowest face sits within this many feet of
# the picked roof's are considered "the same floor".
_WINDOW_PLACEMENT_Z_TOLERANCE_FEET = 1.0


def _iter_solids(geometry_element):
    """Recursively yield every non-degenerate Solid in a GeometryElement,
    descending into GeometryInstance (nested family/instance) geometry."""
    for geom_obj in geometry_element:
        if isinstance(geom_obj, Solid):
            try:
                has_volume = geom_obj.Volume > 1e-6
            except Exception:
                has_volume = False

            if has_volume:
                yield geom_obj
        else:
            try:
                instance_geometry = geom_obj.GetInstanceGeometry()
            except Exception:
                instance_geometry = None

            if instance_geometry is not None:
                for solid in _iter_solids(instance_geometry):
                    yield solid


def _polygon_signed_area(points):
    area = 0.0
    count = len(points)

    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        area += x1 * y2 - x2 * y1

    return area / 2.0


def _bottom_footprint(element):
    """Return (points, z) for the outer boundary of the element's lowest,
    downward-facing planar face - points as (x, y) internal-feet tuples,
    z as that face's elevation - or (None, None)."""
    options = Options()
    options.ComputeReferences = False

    try:
        options.DetailLevel = ViewDetailLevel.Fine
    except Exception:
        pass

    try:
        geometry = element.get_Geometry(options)
    except Exception:
        geometry = None

    if geometry is None:
        return None, None

    best_face = None
    best_face_z = None

    for solid in _iter_solids(geometry):
        for face in solid.Faces:
            if not isinstance(face, PlanarFace):
                continue

            try:
                normal = face.FaceNormal
            except Exception:
                continue

            if normal.Z > -0.9:
                continue

            try:
                face_z = face.Origin.Z
            except Exception:
                continue

            if best_face is None or face_z < best_face_z:
                best_face = face
                best_face_z = face_z

    if best_face is None:
        return None, None

    try:
        edge_loops = best_face.EdgeLoops
    except Exception:
        return None, None

    best_loop_points = None
    best_loop_area = 0.0

    for loop_index in range(edge_loops.Size):
        loop_points = []

        for edge in edge_loops.get_Item(loop_index):
            try:
                curve = edge.AsCurve()
                start_point = curve.GetEndPoint(0)
            except Exception:
                loop_points = []
                break

            loop_points.append((start_point.X, start_point.Y))

        if len(loop_points) < 3:
            continue

        loop_area = abs(_polygon_signed_area(loop_points))

        if loop_area > best_loop_area:
            best_loop_area = loop_area
            best_loop_points = loop_points

    return best_loop_points, best_face_z


def _point_in_polygon(point, polygon):
    """Standard ray-casting point-in-polygon test."""
    x, y = point
    inside = False
    count = len(polygon)
    j = count - 1

    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]

        if (yi > y) != (yj > y):
            x_intersect = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_intersect:
                inside = not inside

        j = i

    return inside


def _longest_edge_angle(points):
    """Return the direction (radians) of the polygon's longest edge.

    Used as the footprint's "dominant orientation" so a bounding-box-like
    4-direction grouping still works for apartments not aligned to the
    project's X/Y axes.
    """
    count = len(points)
    longest_length_sq = -1.0
    longest_angle = 0.0

    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        dx = x2 - x1
        dy = y2 - y1
        length_sq = dx * dx + dy * dy

        if length_sq > longest_length_sq:
            longest_length_sq = length_sq
            longest_angle = math.atan2(dy, dx)

    return longest_angle


# Roof type name fragment identifying "МЗК" (Місце Загального
# Користування - common-use area: corridor/lobby/stairwell) boundary
# roofs, matched case-insensitively as a substring since actual type
# names may vary (e.g. "мзк boundary - 400mm").
_MZK_TYPE_NAME_SUBSTRING = "мзк"

# How close (feet) a МЗК roof's own overall bounding-box CENTRE Z needs
# to be to the picked footprint's own centre Z to count as "the same
# floor" - see _roof_bbox_center_z and _collect_mzk_footprints for why
# this is a SEPARATE, dedicated search from the general same-floor
# neighbour search below (which stays bottom-face-based, still correct
# for window-placement/added-part classification): a МЗК roof's own
# volume can have a genuinely different overall Thickness than an
# apartment's own boundary roof even on the same Base Level (confirmed
# directly in Revit - one МЗК roof here had Thickness=2900mm), so their
# BOTTOM faces are what actually align, not necessarily their centres -
# but a МЗК roof's own centre only ever needs to be compared against
# the picked footprint's own centre, both of which are consistently
# "one storey's worth" of volume, so centre-to-centre is reliable there.
# ~200mm.
_MZK_CENTER_Z_TOLERANCE_FEET = 0.2 / 0.3048


def _roof_bbox_center_z(element):
    """Z midpoint of element's own overall bounding box - its FULL
    geometry's own extents (Revit's own BoundingBox, not a particular
    face) - or None if it has no bounding box in this document."""
    try:
        bbox = element.get_BoundingBox(None)
    except Exception:
        bbox = None

    if bbox is None:
        return None

    return (bbox.Min.Z + bbox.Max.Z) / 2.0


def _roof_level_id(element):
    """The Level element this roof is actually hosted/based on
    (Element.LevelId - a real Revit level reference, not a coordinate),
    as a plain int, or None if it has none/isn't resolvable."""
    try:
        level_id = element.LevelId
    except Exception:
        return None

    if level_id is None:
        return None

    try:
        level_id_int = _element_id_int(level_id)
    except Exception:
        return None

    # ElementId.InvalidElementId == -1 - "no level", not a real match.
    return level_id_int if level_id_int != -1 else None


def _collect_mzk_footprints(document, picked_element, picked_id):
    """Find every МЗК-type roof "on the same floor" as picked_element.

    Primary test: same Base Level (_roof_level_id - an actual Revit
    Level reference, not a coordinate guess) as picked_element. This is
    what "same floor" really means, and needs no tolerance at all - a
    building modelled with a separate Level per storey (confirmed here:
    the project browser lists 01 through 08+ as distinct levels) makes
    this exact. Found necessary after a coordinate-based comparison
    (bounding-box centre, ±200mm) kept matching a МЗК roof one storey
    below: its own centre happened to land within that tolerance of the
    picked footprint's centre even though it's a genuinely different
    Level, because "a little bit different" (the user's own words) roof
    geometry between floors can shift a bounding-box centre by more than
    a small coordinate tolerance can absorb, while never changing which
    Level element it's actually hosted on.

    Falls back to the coordinate-based comparison
    (_roof_bbox_center_z/_MZK_CENTER_Z_TOLERANCE_FEET) only when a level
    id isn't resolvable for the picked element or a given candidate (a
    roof somehow not level-hosted) - better than skipping it outright.

    Returns a list of outer footprint polygons (each from
    _bottom_footprint - the actual 2D shape used for the side-adjacency
    test still comes from the bottom face; only the "is this the same
    floor" decision uses the level/centre)."""
    picked_level_id = _roof_level_id(picked_element)
    picked_center_z = _roof_bbox_center_z(picked_element) if picked_level_id is None else None

    if picked_level_id is None and picked_center_z is None:
        return []

    mzk_footprints = []

    collector = (
        FilteredElementCollector(document)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
    )

    for candidate in collector:
        if _element_id_int(candidate.Id) == picked_id:
            continue

        try:
            candidate_type = document.GetElement(candidate.GetTypeId())
            candidate_type_name = _element_name(candidate_type)
        except Exception:
            candidate_type_name = ""

        if _MZK_TYPE_NAME_SUBSTRING not in str(candidate_type_name or "").strip().lower():
            continue

        if picked_level_id is not None:
            candidate_level_id = _roof_level_id(candidate)
            if candidate_level_id is None or candidate_level_id != picked_level_id:
                continue
        else:
            candidate_center_z = _roof_bbox_center_z(candidate)
            if (
                candidate_center_z is None
                or abs(candidate_center_z - picked_center_z) > _MZK_CENTER_Z_TOLERANCE_FEET
            ):
                continue

        candidate_footprint, _candidate_z = _bottom_footprint(candidate)

        if candidate_footprint and len(candidate_footprint) >= 3:
            mzk_footprints.append(candidate_footprint)

    return mzk_footprints


def _collect_footprint_and_neighbors(document, picked_element):
    """Return (footprint, other_footprints, footprint_z, mzk_footprints)
    for the picked element: its own outer footprint polygon (the outline
    of its lowest downward-facing face - see _bottom_footprint), the
    footprint polygons of every other roof (any type) on the same floor
    (by BOTTOM face - see this function's own history below), that face's
    own Z elevation, and - found separately, see _collect_mzk_footprints
    - the outer footprint polygons of every МЗК-type roof on the same
    floor (by overall bounding-box CENTRE instead). Shared by both
    classifiers so the (expensive - scans every roof's geometry) lookup
    only runs once per pick.

    "Same floor" for the general neighbour search below is decided by
    each roof's own BOTTOM face (the same elevation _bottom_footprint
    already returns) - briefly tried comparing each roof's own vertical
    CENTRE instead for this too, on the theory that it would be more
    robust to bottom-face modelling noise between different apartments'
    own boundary volumes. That turned out to be WORSE, not better, for
    THIS search: an apartment's own boundary roof's Thickness can differ
    from its immediate neighbours' too, so centre-to-centre isn't any
    more reliable here than bottom-face-to-bottom-face - back to bottom
    faces for this one. МЗК roofs get their own dedicated centre-based
    search instead (_collect_mzk_footprints) rather than reusing this
    one's own result filtered by name."""
    footprint, picked_z = _bottom_footprint(picked_element)

    if not footprint or len(footprint) < 3:
        return None, [], None, []

    picked_id = _element_id_int(picked_element.Id)

    other_footprints = []

    collector = (
        FilteredElementCollector(document)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
    )

    for candidate in collector:
        if _element_id_int(candidate.Id) == picked_id:
            continue

        candidate_footprint, candidate_z = _bottom_footprint(candidate)

        if not candidate_footprint or len(candidate_footprint) < 3:
            continue

        # Skip roofs on a different floor - same X/Y footprint, wrong
        # elevation - so they never count as a "neighbour".
        if (
            picked_z is not None
            and candidate_z is not None
            and abs(candidate_z - picked_z) > _WINDOW_PLACEMENT_Z_TOLERANCE_FEET
        ):
            continue

        other_footprints.append(candidate_footprint)

    mzk_footprints = _collect_mzk_footprints(document, picked_element, picked_id)

    return footprint, other_footprints, picked_z, mzk_footprints


# A side only has windows when it looks at open space: a wall (the edge of
# another roof - a neighbouring apartment, a МЗК, a stair core, ...) closer than
# this straight ahead of it makes the side "not outside" (no windows there),
# the same as a side that touches a roof. 0 turns the check off.
_FRONT_CLEARANCE_FT = 15.0 / 0.3048


# Extra reach (beyond _WINDOW_PLACEMENT_TEST_OFFSET_FEET's own ~200mm)
# allowed when deciding whether a footprint side is next to a МЗК roof -
# see _mzk_side_overlap_interval_ft. Real modelled gaps between a
# footprint's own boundary and a neighbouring МЗК roof's own boundary
# can exceed that fixed wall-thickness reach even when the two are
# genuinely the same corridor wall (corridor wall thickness/tolerance
# variance between different apartments' own boundary models) - this is
# purely additive slack for THAT case. ~50mm.
_MZK_SIDE_EXTRA_TOLERANCE_FEET = 0.05 / 0.3048


# Shortest a merged footprint side itself can be to even be considered
# as a candidate for МЗК-facing OR exterior classification at all - a
# step/notch's own short connecting "riser" edge (typically well under
# this) is a structural connector, not a real facade wall, and
# shouldn't be treated as either even if it happens to touch nothing
# (looking "exterior") or sit right next to a МЗК roof (looking
# "МЗК-facing"). ~500mm - comfortably above any real riser depth seen
# in this project's own footprints (100-300mm), comfortably below any
# real wall length.
_MIN_FACADE_SIDE_LENGTH_FT = 0.5 / 0.3048

# _classify_window_placement's own additional filter: the TOTAL exposed
# length accumulated in one of its 4 direction buckets must reach this
# much before that bucket counts as a genuine second (or third) outside
# side - guards against a small misalignment with a neighbouring roof
# (the two boundaries not quite lining up) leaving a sliver-thin exposed
# gap that's still individually over _MIN_FACADE_SIDE_LENGTH_FT, without
# being a real exterior side. ~300mm.
_MIN_OUTSIDE_SIDE_LENGTH_FT = 0.3 / 0.3048


def _point_segment_distance_ft(point, seg_start, seg_end):
    """Shortest distance from a point to a 2D line segment."""
    px, py = point
    ax, ay = seg_start
    bx, by = seg_end
    ab_x = bx - ax
    ab_y = by - ay
    length_sq = ab_x * ab_x + ab_y * ab_y

    if length_sq < 1e-12:
        return math.hypot(px - ax, py - ay)

    t = ((px - ax) * ab_x + (py - ay) * ab_y) / length_sq
    t = max(0.0, min(1.0, t))
    closest_x = ax + ab_x * t
    closest_y = ay + ab_y * t

    return math.hypot(px - closest_x, py - closest_y)


def _segments_intersect(p1, p2, p3, p4):
    """Standard orientation-based 2D segment intersection test
    (including touching endpoints/collinear overlap)."""

    def _orientation(a, b, c):
        value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(value) < 1e-12:
            return 0
        return 1 if value > 0 else -1

    def _on_segment(a, b, c):
        return (
            min(a[0], b[0]) - 1e-9 <= c[0] <= max(a[0], b[0]) + 1e-9
            and min(a[1], b[1]) - 1e-9 <= c[1] <= max(a[1], b[1]) + 1e-9
        )

    o1 = _orientation(p1, p2, p3)
    o2 = _orientation(p1, p2, p4)
    o3 = _orientation(p3, p4, p1)
    o4 = _orientation(p3, p4, p2)

    if o1 != o2 and o3 != o4:
        return True

    if o1 == 0 and _on_segment(p1, p2, p3):
        return True
    if o2 == 0 and _on_segment(p1, p2, p4):
        return True
    if o3 == 0 and _on_segment(p3, p4, p1):
        return True
    if o4 == 0 and _on_segment(p3, p4, p2):
        return True

    return False


def _segment_distance_ft(p1, p2, q1, q2):
    """Minimum distance between two 2D line segments (p1-p2, q1-q2); 0.0
    if they intersect or touch."""
    if _segments_intersect(p1, p2, q1, q2):
        return 0.0

    return min(
        _point_segment_distance_ft(p1, q1, q2),
        _point_segment_distance_ft(p2, q1, q2),
        _point_segment_distance_ft(q1, p1, p2),
        _point_segment_distance_ft(q2, p1, p2),
    )


def _normalize_angle_diff_rad(angle_rad):
    while angle_rad > math.pi:
        angle_rad -= 2.0 * math.pi
    while angle_rad <= -math.pi:
        angle_rad += 2.0 * math.pi
    return angle_rad


def _side_line_ft(side_edges):
    """(start_point, end_point, dir_x, dir_y) for a merged side, or None
    if it's degenerate (zero length)."""
    first_point = side_edges[0][0]
    last_point = side_edges[-1][1]

    dx = last_point[0] - first_point[0]
    dy = last_point[1] - first_point[1]
    length = math.hypot(dx, dy)

    if length < 1e-9:
        return None

    return first_point, last_point, dx / length, dy / length


# How far off from parallel-to-the-OUTSIDE-SIDE (degrees) an inside
# wall may be and still count as a candidate for
# _r_ap_outside_ray_depth_extremes_ft - see that function's own
# docstring.
_R_AP_WALL_PARALLEL_TOLERANCE_DEGREES = 30.0


# How small a turn (degrees) between two consecutive edges still counts
# as "the same wall" rather than a genuine corner - a notch or a small
# modelling kink shouldn't split one true side into two.
_ADDED_PART_ANGLE_MERGE_THRESHOLD_DEGREES = 10.0


def _merge_collinear_sides(points, angle_threshold_degrees):
    """Group the polygon's consecutive raw edges into 'true sides': an
    edge is merged into the previous side whenever the turn between them
    is smaller than angle_threshold_degrees. Returns a list of sides,
    each a list of (start_point, end_point) edge tuples."""
    count = len(points)

    if count < 3:
        return []

    edges = [(points[i], points[(i + 1) % count]) for i in range(count)]

    def _edge_angle(edge):
        (x1, y1), (x2, y2) = edge
        return math.atan2(y2 - y1, x2 - x1)

    threshold_radians = math.radians(angle_threshold_degrees)

    def _angle_difference(angle_a, angle_b):
        return abs((angle_a - angle_b + math.pi) % (2 * math.pi) - math.pi)

    sides = [[edges[0]]]

    for index in range(1, count):
        previous_angle = _edge_angle(edges[index - 1])
        current_angle = _edge_angle(edges[index])

        if _angle_difference(current_angle, previous_angle) < threshold_radians:
            sides[-1].append(edges[index])
        else:
            sides.append([edges[index]])

    # The polygon's start/end vertex is an arbitrary cut point - if the
    # first and last sides are themselves collinear, they're really one
    # side that got split by that cut.
    if len(sides) > 1:
        first_angle = _edge_angle(sides[0][0])
        last_angle = _edge_angle(sides[-1][-1])

        if _angle_difference(first_angle, last_angle) < threshold_radians:
            sides[0] = sides[-1] + sides[0]
            sides.pop()

    return sides


# ============================================================
# THE BACKEND'S CLASSIFICATION
#
# What this script needs to know about a picked footprint - its window placement, its added part and the sides that
# touch a МЗК / the outside / the inside - is decided by the backend (core/classify.py): one POST
# /footprint/classify answers every question at once, and the answer is kept for the calls that follow with the
# same polygons. There is no local copy of those rules here.
# ============================================================

_classification = {"key": None, "keep": None, "result": None}


def _backend_classification(footprint, other_footprints, mzk_footprints):
    """The backend's whole answer for these polygons; repeated questions
    about the SAME polygons reuse it."""
    key = (id(footprint), id(other_footprints), id(mzk_footprints),
           len(footprint), len(other_footprints), len(mzk_footprints))
    if _classification["key"] != key or _classification["keep"][0] is not footprint:
        _classification["result"] = backend_api.classify_footprint(footprint, other_footprints, mzk_footprints)
        _classification["key"] = key
        _classification["keep"] = (footprint, other_footprints, mzk_footprints)    # keeps the ids valid
    return _classification["result"]


def _classify_window_placement(footprint, other_footprints, mzk_footprints):
    window = _backend_classification(footprint, other_footprints, mzk_footprints)["windowPlacement"]
    return window["code"], window["text"], copy.deepcopy(window["outsideEdges"])


def _classify_added_part(footprint, other_footprints, mzk_footprints):
    added = _backend_classification(footprint, other_footprints, mzk_footprints)["addedPart"]
    return added["code"], added["text"]


def _outside_facing_side_target_ft(footprint, other_footprints, mzk_footprints):
    return copy.deepcopy(_backend_classification(footprint, other_footprints, mzk_footprints)["outsideTarget"])


# The "Банк квартир" apartment-area filter is widened by this many
# square metres on each side of the picked footprint's own area, so a
# reasonable spread of matching apartment images still shows up.
_AREA_FILTER_MARGIN_SQUARE_METERS = 7

# The picked roof's own "Area" parameter is the footprint's gross area,
# not the apartment's actual (net) area used to name/filter apartment
# images - it's scaled down by a room-count-dependent coefficient before
# building the area filter, matching the ratio used elsewhere for these
# apartment types.
_AREA_COEFFICIENT_BY_ROOM_COUNT_CODE = {
    "0": 0.77,  # студіо
    "1": 0.77,  # однокімнатні
    "2": 0.79,  # двокімнатні
    "3": 0.84,  # трьохкімнатні
}


# ============================================================
# PLACEMENT (for "Запустити" in Банк квартир - see
# apartment_group_placement.py in the extension-level lib)
#
# The picked roof is a real 3D solid (a boxed-out "boundary" element),
# not a flat plate, so "its lowest side" means the lowest (smallest Z)
# of its faces - X/Y/Z all come from that face's own centre: X/Y from
# _polygon_centroid(footprint) (footprint is already that face's own
# outline - see _bottom_footprint), Z from that same face's own
# elevation. Level is the roof's own base level; Offset is that Z minus
# the level's elevation; Angle is a compass-style bearing (radians,
# clockwise from north/+Y) of the footprint's longest edge - exactly
# what a person would type into the original script's Angle input by
# eye from a site plan, just computed automatically from the footprint
# instead.
# ============================================================

def _roof_level(document, element):
    try:
        level_id = element.LevelId
    except Exception:
        level_id = None

    if level_id is None:
        return None

    try:
        level = document.GetElement(level_id)
    except Exception:
        level = None

    return level if isinstance(level, Level) else None


def _log_mzk_detection_diagnostics(footprint, mzk_footprints, mzk_target):
    """Write the actual numbers behind the МЗК-side detection to the
    shared debug log (via apartment_bank_window._debug_log - a plain
    module-level function, safe to call cross-engine, see that
    function's own docstring) for every merged side of the footprint:
    how many МЗК roofs were even found as same-floor candidates, and for
    each side, the nearest one's own distance (mm) and angle-off-
    parallel (degrees) - regardless of whether that side ended up
    passing _mzk_side_overlap_interval_ft's own thresholds or not.

    This is what tells us, when a footprint that visibly IS next to a
    МЗК roof still doesn't get one detected, exactly which of the three
    possible causes it is: zero same-floor МЗК candidates at all (a
    _collect_footprint_and_neighbors same-floor-filtering problem - see
    its own docstring for a past example: comparing bottom faces vs
    vertical centres), a genuine gap wider than the current tolerance
    (_MZK_SIDE_EXTRA_TOLERANCE_FEET too small), or an angle beyond the
    current parallel allowance (_MZK_SIDE_PARALLEL_TOLERANCE_DEGREES too
    small) - without needing another round of guessing.

    Best-effort only: swallows and logs its own failures, never raises."""
    if apartment_bank_window is None:
        return

    try:
        sides = _merge_collinear_sides(footprint, _ADDED_PART_ANGLE_MERGE_THRESHOLD_DEGREES)
        lines = [
            u"DKUI: МЗК-детекція - {} мзк-полігон(ів) на цьому поверсі; "
            u"{} сторін(и) плями:".format(len(mzk_footprints), len(sides))
        ]

        for index, side_edges in enumerate(sides):
            line = _side_line_ft(side_edges)
            if line is None:
                continue

            first_point, last_point, dir_x, dir_y = line
            side_angle = math.atan2(dir_y, dir_x)

            best_distance_ft = None
            best_angle_deg = None

            for mzk_footprint in mzk_footprints:
                count = len(mzk_footprint)
                for edge_index in range(count):
                    q1 = mzk_footprint[edge_index]
                    q2 = mzk_footprint[(edge_index + 1) % count]
                    other_dx = q2[0] - q1[0]
                    other_dy = q2[1] - q1[1]

                    if other_dx * other_dx + other_dy * other_dy < 1e-12:
                        continue

                    distance_ft = _segment_distance_ft(first_point, last_point, q1, q2)

                    if best_distance_ft is None or distance_ft < best_distance_ft:
                        other_angle = math.atan2(other_dy, other_dx)
                        angle_diff = abs(_normalize_angle_diff_rad(side_angle - other_angle))
                        angle_diff = min(angle_diff, abs(math.pi - angle_diff))
                        best_distance_ft = distance_ft
                        best_angle_deg = math.degrees(angle_diff)

            if best_distance_ft is None:
                lines.append(u"  Сторона {}: немає жодного мзк-полігону для порівняння.".format(index))
            else:
                lines.append(
                    u"  Сторона {}: найближчий мзк-полігон - відстань {:.0f}мм, "
                    u"кут розбіжності {:.1f}°.".format(
                        index, best_distance_ft * 304.8, best_angle_deg
                    )
                )

        lines.append(
            u"  Результат: {}".format(
                u"МЗК сторону знайдено." if mzk_target is not None else u"МЗК сторону НЕ знайдено."
            )
        )

        apartment_bank_window._debug_log(u"\n".join(lines))
    except Exception as error:
        apartment_bank_window._debug_log(
            u"DKUI: не вдалося записати діагностику МЗК-детекції: {}".format(error)
        )


# Colours matching the convention the user has been using to annotate
# screenshots when reporting a wrong placement (green = МЗК side, red =
# exterior/outside side) - see _draw_side_debug_lines. The footprint
# fill itself keeps its own pattern's default colour unchanged - only
# its boundary line colour (grey) and transparency are overridden.
_DEBUG_MZK_LINE_COLOR = Color(0, 176, 80)
_DEBUG_OUTSIDE_LINE_COLOR = Color(255, 0, 0)

# R+AP-only, pick-time ray-cast debug lines (see
# _r_ap_outside_ray_depth_extremes_ft) - brown for the closest crossing,
# purple for the furthest, same shorter/longer convention as
# apartment_group_placement.py's own post-placement depth lines.
_DEBUG_R_AP_RAY_NEAR_COLOR = Color(139, 69, 19)
_DEBUG_R_AP_RAY_FAR_COLOR = Color(128, 0, 128)

# Picking a roof draws NOTHING in the view any more (no crosshatch region, no green / red side lines): the
# switch keeps the drawing code below for debugging. Elements drawn by an earlier pick are still removed.
_DRAW_PICK_ELEMENTS = False

_DEBUG_FOOTPRINT_FILL_LINE_COLOR = Color(128, 128, 128)
_DEBUG_FOOTPRINT_FILL_TRANSPARENCY = 75
# Revit's own built-in FilledRegionType name - a diagonal hatch pattern
# with no solid fill, so the footprint underneath still reads clearly
# through it. Falls back to whatever FilledRegionType exists first in
# the document if this one isn't present.
_DEBUG_FOOTPRINT_FILL_TYPE_NAME = "Diagonal Crosshatch - Transparent"
_DEBUG_LINE_WEIGHT = 8

# Minimum length (feet, ~150mm) a debug line is stretched to if its own
# real endpoints come out shorter than this (or identical) - see the
# drawing loop's own comment for why: Line.CreateBound throws on a
# zero-length segment, which was silently dropping the МЗК line whenever
# its own touching sub-range collapsed to (near) a single point.
_DEBUG_MIN_LINE_LENGTH_FT = 0.5


def _draw_side_debug_lines(
    document, view, footprint, mzk_targets, outside_targets, z_ft, extra_lines=None,
):
    """Draw temporary debug visuals in `view` for the just-picked
    footprint, so a wrong detection is obvious at a glance in Revit
    itself instead of having to wait for a placement (or a screenshot
    annotated by hand) to find out:

    - A green, diagonal-crosshatch, transparent FILLED REGION over the
      picked footprint's own polygon (exactly what
      _bottom_footprint/_collect_footprint_and_neighbors is working
      with - useful on its own if the wrong ROOF/shape got picked up,
      not just the wrong side of the right one).
    - A green DetailLine along exactly the touching sub-range of EVERY
      side _mzk_touching_sides_ft found next to a МЗК roof (mzk_targets
      - a list, not just the first/chosen one: a footprint can genuinely
      have more than one such side, and even where it doesn't, seeing
      every candidate is what makes this trustworthy as a debug aid).
    - A red DetailLine along EVERY exterior side
      _outside_touching_sides_ft found (outside_targets - same "show
      every candidate, not just the one actually used" reasoning).
    - Whatever's in extra_lines (optional) - a list of (target_dict,
      Color) pairs, each drawn the same way as the above (same shape:
      startXFt/YFt, endXFt/YFt, dirXFt/YFt). Not currently used by any
      caller (R+AP's own depth-measurement lines are drawn later, on
      the placed apartment, by apartment_group_placement.py's own
      _draw_r_ap_depth_debug_lines instead - see that function's own
      comment for why) - kept as a general hook for future debug
      visuals drawn at pick time.

    Deletes whichever elements were drawn for the PREVIOUS pick first
    (see dkui_bridge.set_/pop_debug_side_line_ids), so repeated picks
    don't leave a trail behind.

    Best-effort only: any failure here is logged and swallowed, never
    raised - a debug visual aid is not worth breaking the actual
    footprint-pick/placement-data flow over."""
    if dkui_bridge is not None:
        try:
            previous_ids = dkui_bridge.pop_debug_side_line_ids()
        except Exception:
            previous_ids = []

        if previous_ids:
            try:
                cleanup_t = Transaction(document, "DKUI: прибрати попередні діагностичні елементи")
                cleanup_t.Start()
                for id_int in previous_ids:
                    try:
                        document.Delete(ElementId(id_int))
                    except Exception:
                        pass
                cleanup_t.Commit()
            except Exception as error:
                if apartment_bank_window is not None:
                    apartment_bank_window._debug_log(
                        u"DKUI: не вдалося прибрати попередні діагностичні елементи: {}".format(error)
                    )

    if view is None or not _DRAW_PICK_ELEMENTS:
        return

    new_ids = []

    try:
        t = Transaction(document, "DKUI: діагностичні елементи плями/сторін")
        t.Start()

        if footprint and len(footprint) >= 3:
            try:
                region_type = None
                for candidate_region_type in FilteredElementCollector(document).OfClass(FilledRegionType):
                    if _element_name(candidate_region_type) == _DEBUG_FOOTPRINT_FILL_TYPE_NAME:
                        region_type = candidate_region_type
                        break
                if region_type is None:
                    region_type = FilteredElementCollector(document).OfClass(FilledRegionType).FirstElement()

                if region_type is not None:
                    curve_loop = CurveLoop()
                    count = len(footprint)
                    for index in range(count):
                        p1 = footprint[index]
                        p2 = footprint[(index + 1) % count]
                        curve_loop.Append(
                            Line.CreateBound(XYZ(p1[0], p1[1], z_ft), XYZ(p2[0], p2[1], z_ft))
                        )

                    region = FilledRegion.Create(
                        document, region_type.Id, view.Id, List[CurveLoop]([curve_loop])
                    )

                    # Deliberately NOT overriding the surface pattern's
                    # own foreground colour/visibility - keep whatever
                    # "Diagonal Crosshatch - Transparent" already looks
                    # like by default. Only the boundary line colour and
                    # transparency are overridden.
                    region_override = OverrideGraphicSettings()
                    region_override.SetProjectionLineColor(_DEBUG_FOOTPRINT_FILL_LINE_COLOR)
                    try:
                        region_override.SetSurfaceTransparency(_DEBUG_FOOTPRINT_FILL_TRANSPARENCY)
                    except Exception:
                        pass
                    view.SetElementOverrides(region.Id, region_override)

                    new_ids.append(_element_id_int(region.Id))
            except Exception as error:
                if apartment_bank_window is not None:
                    apartment_bank_window._debug_log(
                        u"DKUI: не вдалося намалювати заливку плями: {}".format(error)
                    )

        line_targets = [(mzk_target, _DEBUG_MZK_LINE_COLOR) for mzk_target in (mzk_targets or [])]
        line_targets.extend(
            (outside_target, _DEBUG_OUTSIDE_LINE_COLOR)
            for outside_target in (outside_targets or [])
        )
        line_targets.extend(extra_lines or [])

        for target, color in line_targets:
            if target is None:
                continue

            start_x = target.get("startXFt")
            start_y = target.get("startYFt")
            end_x = target.get("endXFt")
            end_y = target.get("endYFt")

            if start_x is None or start_y is None or end_x is None or end_y is None:
                continue

            # A degenerate (near-zero-length) segment - e.g. the МЗК
            # side's own touching sub-range being a single sample point
            # - makes Line.CreateBound throw ("start and end points are
            # identical"), which would otherwise silently drop just this
            # one line while the transaction (and the other line/the
            # fill) still succeeds. Extend it a little along its own
            # direction instead of skipping it outright, so there's
            # still something visible marking where it is.
            if math.hypot(end_x - start_x, end_y - start_y) < _DEBUG_MIN_LINE_LENGTH_FT:
                dir_x = target.get("dirXFt") or 1.0
                dir_y = target.get("dirYFt") or 0.0
                dir_length = math.hypot(dir_x, dir_y)
                if dir_length < 1e-9:
                    dir_x, dir_y = 1.0, 0.0
                else:
                    dir_x, dir_y = dir_x / dir_length, dir_y / dir_length
                mid_x = (start_x + end_x) / 2.0
                mid_y = (start_y + end_y) / 2.0
                half_length = _DEBUG_MIN_LINE_LENGTH_FT / 2.0
                start_x = mid_x - dir_x * half_length
                start_y = mid_y - dir_y * half_length
                end_x = mid_x + dir_x * half_length
                end_y = mid_y + dir_y * half_length

            try:
                line = Line.CreateBound(
                    XYZ(start_x, start_y, z_ft), XYZ(end_x, end_y, z_ft)
                )
                detail_curve = document.Create.NewDetailCurve(view, line)

                override = OverrideGraphicSettings()
                override.SetProjectionLineColor(color)
                override.SetProjectionLineWeight(_DEBUG_LINE_WEIGHT)
                view.SetElementOverrides(detail_curve.Id, override)

                new_ids.append(_element_id_int(detail_curve.Id))
            except Exception as error:
                if apartment_bank_window is not None:
                    apartment_bank_window._debug_log(
                        u"DKUI: не вдалося намалювати діагностичну лінію: {}".format(error)
                    )

        t.Commit()
    except Exception as error:
        if apartment_bank_window is not None:
            apartment_bank_window._debug_log(
                u"DKUI: не вдалося намалювати діагностичні елементи сторін: {}".format(error)
            )

    if dkui_bridge is not None and new_ids:
        try:
            dkui_bridge.set_debug_side_line_ids(new_ids)
        except Exception:
            pass


def _store_apartment_placement(
    document, element, footprint, footprint_z,
    window_placement_code, added_part_code, mzk_footprints, other_footprints,
    classifier_outside_edges=None,
):
    """Compute and remember where/how "Запустити" should place this
    apartment's group later, once the user picks one in Банк квартир -
    see dkui_bridge.set_last_apartment_placement's docstring for why this
    is remembered rather than computed fresh at Запустити time (the roof
    itself isn't necessarily still selected/available then).

    Two alignment modes, chosen here and carried out in
    apartment_group_placement.place_apartment_group:

    - "entranceToMzkSide" (рядове footprints, both Відсутня and
      Присутня): the apartment's own entrance door (a FamilyInstance
      whose family name contains "Вхід") gets its host wall's own outer
      FACE aligned onto the footprint's side that faces a МЗК-type
      neighbour, and a second wall - one meeting the entrance wall at a
      >20° corner, with the same thickness - gets its own centre aligned
      onto whichever adjacent footprint side it ends up closest to. See
      _mzk_facing_side_target_ft and apartment_group_placement's
      find_neighbor_wall/_solve_entrance_alignment_ft. This is what an
      apartment actually needs: its front door opening onto the
      corridor/common area, not just "somewhere in the middle of the
      footprint". The footprint's own EXTERIOR side (the one facing no
      neighbour at all - see _outside_facing_side_target_ft) is also
      included in the payload when found, so
      apartment_group_placement.py can independently align the
      apartment's own window-bearing wall(s) to face it, and cross-check
      that against the door-based alignment - see that module's
      _solve_window_alignment_ft and place_apartment_group's own comment
      for why: the entrance door's family-NAME match can occasionally
      grab the wrong wall (e.g. a balcony/technical door also named with
      "вхід"), while the window wall's Windows-CATEGORY match can't.
    - "footprintCenter" (fallback: not рядове, or no МЗК-facing side
      found): the old behaviour - the apartment's own centre lands on
      the footprint's own centre, rotated to the footprint's longest
      edge.

    Either way, Z is always the footprint's own lowest-face elevation,
    and it's always the apartment's WALLS' lowest point (not its overall
    centre) that lands there - see
    apartment_group_placement.get_group_center_at_lowest_wall_ft.

    Any failure here (missing level/elevation/contour, or an unexpected
    error) explicitly clears the stored placement and shows a TaskDialog -
    not just a logger.warning, which is easy to miss - so a footprint pick
    that silently failed to compute placement data doesn't later look like
    "Запустити" itself is broken."""
    if dkui_bridge is None:
        return

    try:
        level = _roof_level(document, element)

        if level is None or footprint_z is None or not footprint:
            dkui_bridge.set_last_apartment_placement(None)
            TaskDialog.Show(
                "Пляма квартири",
                "Не вдалося визначити параметри розміщення для цієї плями "
                "(рівень, нижня грань або контур не визначено).\n\n"
                "«Запустити» не зможе розмістити квартиру, обрану за цією "
                "плямою.",
            )
            return

        # The payload itself is composed by the backend (core/placement_payload.py); what stays here is what needs Revit:
        # the level and element ids above, the debug drawing and the bridge.
        composed = (backend_api.placement_payload(
                _element_id_int(element.Id), _element_id_int(level.Id), footprint_z, footprint,
                other_footprints, mzk_footprints, window_placement_code, added_part_code,
                classifier_outside_edges))
        mzk_sides = composed["mzkSides"]
        _log_mzk_detection_diagnostics(footprint, mzk_footprints, mzk_sides[0] if mzk_sides else None)
        no_lines = composed["isRAp"]        # R+AP footprints get no side lines, only the fill
        _draw_side_debug_lines(
            document, uidoc.ActiveView, footprint,
            [] if no_lines else mzk_sides,
            [] if no_lines else composed["outsideSidesForDrawing"],
            footprint_z,
            extra_lines=[],
        )
        if apartment_bank_window is not None:
            for note in composed["notes"]:
                apartment_bank_window._debug_log(note)
        dkui_bridge.set_last_apartment_placement(composed["payload"])
        return
    except Exception as error:
        # Not logger.warning - that writes to pyRevit's own stdout-backed
        # output console, which auto-opens a window the moment anything
        # is written to it (same reasoning as apartment_group_placement.
        # py's own _log). File-only instead.
        if apartment_bank_window is not None:
            apartment_bank_window._debug_log(
                u"DKUI: не вдалося зберегти параметри розміщення: {}".format(error)
            )
        try:
            dkui_bridge.set_last_apartment_placement(None)
        except Exception:
            pass
        TaskDialog.Show(
            "Пляма квартири",
            "Не вдалося обчислити параметри розміщення для цієї плями:\n\n"
            "{}\n\n"
            "«Запустити» не зможе розмістити квартиру, обрану за цією "
            "плямою.".format(error),
        )


def _room_count_code_from_type_name(type_name):
    """First character of the roof's type name, e.g. "2k boundary -
    400mm" -> "0"/"1"/"2"/"3" (matching the apartment-bank's own
    apartment-type codes), or None if it isn't one of those."""
    stripped_name = str(type_name or "").strip()

    if stripped_name and stripped_name[0] in "0123":
        return stripped_name[0]

    return None


_DIMENSION_TOLERANCE_MM = 500


def _nominal_dimensions_mm(points):
    """(shorter, longer) side in mm of the footprint's bounding rectangle,
    measured along its own dominant (longest-edge) direction."""
    angle = _longest_edge_angle(points)
    cos_a, sin_a = math.cos(-angle), math.sin(-angle)
    xs, ys = [], []
    for x, y in points:
        xs.append(x * cos_a - y * sin_a)
        ys.append(x * sin_a + y * cos_a)

    side_a = (max(xs) - min(xs)) * 304.8
    side_b = (max(ys) - min(ys)) * 304.8
    return (min(side_a, side_b), max(side_a, side_b))


def _send_to_apartment_bank(
    type_name, area_square_meters, window_placement_code, added_part_code,
    dimensions_mm=None, footprint=None, facade_depth_mm=None,
):
    """Push the picked footprint's parameters into the "Банк квартир"
    filters. If that window is already open, send immediately; if not,
    open it and queue the payload for its own NavigationCompleted
    handler to apply once its page has actually finished loading."""
    if dkui_bridge is None:
        return

    room_count_code = _room_count_code_from_type_name(type_name)

    payload = {
        "apartmentType": room_count_code,
        "windowPlacement": window_placement_code,
        "addedPart": added_part_code,
        # One footprint picked: the bank should show at least 3 suggestions,
        # most suitable first ("Найбільш підходяще" compares the outline).
        "minMatches": 3,
    }
    if footprint:
        payload["footprintsFt"] = [[[point[0], point[1]] for point in footprint]]

    if area_square_meters is not None:
        area_coefficient = _AREA_COEFFICIENT_BY_ROOM_COUNT_CODE.get(
            room_count_code, 1.0
        )
        adjusted_area_square_meters = area_square_meters * area_coefficient

        payload["areaMin"] = int(
            round(adjusted_area_square_meters - _AREA_FILTER_MARGIN_SQUARE_METERS)
        )
        payload["areaMax"] = int(
            round(adjusted_area_square_meters + _AREA_FILTER_MARGIN_SQUARE_METERS)
        )

    if dimensions_mm is not None:
        payload["dimWidth"] = int(round(dimensions_mm[0]))
        payload["dimLength"] = int(round(dimensions_mm[1]))
        payload["dimTolerance"] = _DIMENSION_TOLERANCE_MM

    if facade_depth_mm is not None:
        # A Рядове footprint: width is its facade (along the outside wall), length its depth (outside wall
        # to the inner wall); the layouts are compared with them side for side (facade_fit).
        payload["dimWidth"] = int(round(facade_depth_mm[0]))
        payload["dimLength"] = int(round(facade_depth_mm[1]))
        payload["dimOrdered"] = True
        payload["orderedDims"] = [[facade_depth_mm[0], facade_depth_mm[1]]]

    if apartment_bank_window is None:
        return

    try:
        # Set BEFORE show_apartment_selector(): whether this is the
        # first window this session or an existing one being reused,
        # show_apartment_selector() always ends with a fresh page
        # Navigate() now (see its own reuse-path comment for why), whose
        # NavigationCompleted pops and sends this once the page has
        # actually finished (re)loading - never sent directly from here,
        # which would race against the page being replaced.
        dkui_bridge.set_pending_footprint_payload(payload)
        apartment_bank_window.show_apartment_selector()
    except Exception as error:
        if apartment_bank_window is not None:
            apartment_bank_window._debug_log(
                u"DKUI: не вдалося відкрити Банк квартир: {}".format(error)
            )


def pick_apartment_footprint():
    try:
        reference = uidoc.Selection.PickObject(
            ObjectType.Element,
            _RoofSelectionFilter(),
            "Виберіть пляму квартири (дах)",
        )
    except OperationCanceledException:
        return
    except Exception as error:
        if apartment_bank_window is not None:
            apartment_bank_window._debug_log(
                u"Вибір плями квартири скасовано: {}".format(error)
            )
        return

    element = doc.GetElement(reference)

    # Floor-layout mode: a click on a footprint's region means the footprint.
    if element is not None and dkui_bridge is not None:
        try:
            roof_id = dkui_bridge.get_floor_region_map().get(_element_id_int(element.Id))
            if roof_id is not None:
                element = doc.GetElement(ElementId(int(roof_id)))
        except Exception:
            pass

    if element is None:
        TaskDialog.Show(
            "Пляма квартири",
            "Не вдалося визначити обраний елемент.",
        )
        return

    element_type = doc.GetElement(element.GetTypeId())
    type_name = _element_name(element_type)

    if str(type_name).strip().lower() not in _NORMALISED_BOUNDARY_TYPE_NAMES:
        TaskDialog.Show(
            "Пляма квартири",
            "Обраний елемент не є типом плями квартири.\n\n"
            "Тип: {}\n\n"
            "Очікувані типи:\n{}".format(
                type_name,
                "\n".join(BOUNDARY_TYPE_NAMES),
            ),
        )
        return

    area_square_meters = _area_in_square_meters(element)

    footprint, other_footprints, footprint_z, mzk_footprints = _collect_footprint_and_neighbors(
        doc, element
    )

    window_placement_code, _window_placement_text, classifier_outside_edges = (
        _classify_window_placement(footprint, other_footprints, mzk_footprints)
    )
    added_part_code, _added_part_text = _classify_added_part(
        footprint, other_footprints, mzk_footprints
    )

    _store_apartment_placement(
        doc, element, footprint, footprint_z,
        window_placement_code, added_part_code, mzk_footprints, other_footprints,
        classifier_outside_edges,
    )

    facade_depth_mm = None
    if window_placement_code == "R":
        try:
            outside_target = _outside_facing_side_target_ft(footprint, other_footprints, mzk_footprints)
            if outside_target is not None:
                facade_depth_mm = facade_fit.extents_mm(
                    footprint, outside_target["dirXFt"], outside_target["dirYFt"]
                )
        except Exception:
            facade_depth_mm = None

    _send_to_apartment_bank(
        type_name, area_square_meters, window_placement_code, added_part_code,
        _nominal_dimensions_mm(footprint),
        footprint=footprint,
        facade_depth_mm=facade_depth_mm,
    )


if __name__ == "__main__":
    pick_apartment_footprint()
