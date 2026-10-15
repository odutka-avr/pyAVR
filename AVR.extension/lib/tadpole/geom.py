# -*- coding: utf-8 -*-
"""Step 2 and Step 3 — geometry extraction and face classification.

Runs on both the IronPython 2.7 and the CPython 3 pyRevit engines: no
f-strings, no type hints, no Python-3-only syntax.
"""

import math

from Autodesk.Revit.DB import (
    Options, Solid, GeometryInstance, PlanarFace, ViewDetailLevel,
    UnitUtils,
)

from tadpole.config import ANGLE_TOL_DEG


# Thresholds derived from the angle, per spec §1.3 / Step 3.
C = math.cos(math.radians(ANGLE_TOL_DEG))   # 0.99985 at 1 degree
S = math.sin(math.radians(ANGLE_TOL_DEG))   # 0.01745 at 1 degree

# Face kinds
UP = 'horizontal_up'        # slab top / roof
DOWN = 'horizontal_down'    # soffit / underside  <- what button 2 uses
VERTICAL = 'vertical'       # facade
DROPPED = 'dropped'         # tilted past tolerance from both
NONPLANAR = 'nonplanar'     # no single normal; routed out, never classified


def mm_to_internal(value_mm):
    """Millimetres -> Revit internal units (decimal feet).

    Spec §1.3: the API works in feet whatever the document displays, and
    getting this wrong does not throw, it silently scales by 3.28.
    """
    try:
        from Autodesk.Revit.DB import UnitTypeId
        return UnitUtils.ConvertToInternalUnits(float(value_mm),
                                                UnitTypeId.Millimeters)
    except ImportError:
        # Revit 2020 and earlier
        from Autodesk.Revit.DB import DisplayUnitType
        return UnitUtils.ConvertToInternalUnits(
            float(value_mm), DisplayUnitType.DUT_MILLIMETERS)


def geometry_options():
    opt = Options()
    opt.ComputeReferences = False
    opt.IncludeNonVisibleObjects = False
    opt.DetailLevel = ViewDetailLevel.Fine
    return opt


def iter_solids(element, opt=None):
    """Yield every non-degenerate Solid belonging to an element."""
    if opt is None:
        opt = geometry_options()
    geo = element.get_Geometry(opt)
    if geo is None:
        return
    for solid in _walk(geo):
        yield solid


def _walk(geo_element):
    for obj in geo_element:
        if isinstance(obj, Solid):
            if obj.Volume > 1e-9 and obj.Faces.Size > 0:
                yield obj
        elif isinstance(obj, GeometryInstance):
            inst = obj.GetInstanceGeometry()
            if inst is not None:
                for solid in _walk(inst):
                    yield solid


def classify_face(face):
    """Return (kind, normal_z) for one face.

    Non-planar faces are routed out rather than classified — they have no
    single normal, so feeding them to the test would be meaningless.
    """
    if not isinstance(face, PlanarFace):
        return NONPLANAR, None

    nz_signed = face.FaceNormal.Z
    nz = abs(nz_signed)

    if nz >= C:
        return (UP if nz_signed > 0 else DOWN), nz_signed
    if nz <= S:
        return VERTICAL, nz_signed
    return DROPPED, nz_signed


def classify_element(element, opt=None):
    """Classify every face of an element.

    Returns a dict:
        counts   {kind: int}
        faces    {kind: [PlanarFace, ...]}
        solids   int
        area     {kind: float}   total face area, internal units
    """
    counts = {UP: 0, DOWN: 0, VERTICAL: 0, DROPPED: 0, NONPLANAR: 0}
    faces = {UP: [], DOWN: [], VERTICAL: [], DROPPED: [], NONPLANAR: []}
    area = {UP: 0.0, DOWN: 0.0, VERTICAL: 0.0, DROPPED: 0.0, NONPLANAR: 0.0}
    n_solids = 0

    for solid in iter_solids(element, opt):
        n_solids += 1
        for face in solid.Faces:
            kind, _nz = classify_face(face)
            counts[kind] += 1
            faces[kind].append(face)
            try:
                area[kind] += face.Area
            except Exception:
                pass

    return {
        'counts': counts,
        'faces': faces,
        'area': area,
        'solids': n_solids,
    }


_EMPTY_COUNTS = {UP: 0, DOWN: 0, VERTICAL: 0, DROPPED: 0, NONPLANAR: 0}
_EMPTY_FACES = {UP: [], DOWN: [], VERTICAL: [], DROPPED: [], NONPLANAR: []}
_EMPTY_AREA = {UP: 0.0, DOWN: 0.0, VERTICAL: 0.0, DROPPED: 0.0, NONPLANAR: 0.0}


def count_solids(element, opt=None):
    """`has_geometry` and nothing else - no face touched at all.

    A DirectShape built from an imported mesh (SketchUp context is the
    repeat offender) often lands as a Solid with one PlanarFace per
    original triangle - tens or hundreds of thousands of them.
    classify_element's per-face loop calls Area on every one of those,
    and that per-face Revit API cost, at that count, is what actually
    stalls collection - not the collector, not the booleans downstream.

    Only worth calling for an element nothing will ever ask classified
    faces from. Context (`other`) qualifies whenever
    PARTY_WALLS_AGAINST_CONTEXT is off: sun.py's build_obstructions only
    ever calls iter_solids directly for shading, never reads
    `.geometry['faces']`, and classify_walls only looks at a context
    record's VERTICAL faces when party walls against context are on.
    """
    n_solids = 0
    for _solid in iter_solids(element, opt):
        n_solids += 1
    return {
        'counts': dict(_EMPTY_COUNTS),
        'faces': dict((k, list(v)) for k, v in _EMPTY_FACES.items()),
        'area': dict(_EMPTY_AREA),
        'solids': n_solids,
    }


def lowest_down_face(element, opt=None):
    """The bottom face of a floor — lowest DOWN-facing horizontal face.

    This is what button 2 places a roof on. Returns the PlanarFace, or
    None if the element has no down-facing horizontal face at all.
    """
    best = None
    best_z = None
    for solid in iter_solids(element, opt):
        for face in solid.Faces:
            kind, _nz = classify_face(face)
            if kind != DOWN:
                continue
            z = face.Origin.Z
            if best_z is None or z < best_z:
                best = face
                best_z = z
    return best


def outer_loop(face):
    """The outer boundary of a planar face, as a CurveLoop.

    Picks the longest loop by total curve length. Inner loops (openings)
    are discarded — a footprint roof takes a single boundary, and holes
    are out of scope for this button.
    """
    loops = face.GetEdgesAsCurveLoops()
    if loops is None or loops.Count == 0:
        return None, 0

    best = None
    best_len = -1.0
    total = loops.Count
    for loop in loops:
        length = 0.0
        for crv in loop:
            try:
                length += crv.Length
            except Exception:
                pass
        if length > best_len:
            best_len = length
            best = loop
    return best, total
