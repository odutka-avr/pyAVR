# -*- coding: utf-8 -*-
"""Step 8 — direct sun hours on the outer facade.

Self-contained: no ladybug, no external dependency. Solar position is
computed from latitude and date in MEAN SOLAR TIME, which is what the
spec settled on — solar noon is 12:00 by construction, so longitude,
time zone and daylight saving all drop out.

Occlusion is tested with `Solid.IntersectWithCurve` where a blocker is a
closed volume, which works on in-memory solids and needs no view — see
FACE_MODE below for what happens when it is not.

COST is the whole design problem here. A 1 m grid over 22 000 m2 of
facade is 22 000 points; times 40 sun vectors times 138 obstructions is
120 million intersection tests, which is not happening. Four things cut
it down, in order of how much they save:

  1. Sample ONE row per wall, at window height, rather than gridding the
     full storey. Insolation is assessed at the window, not at the skirting.
  2. Drop any sun vector behind the wall's own normal before casting —
     the wall shades itself, and that is roughly half of them.
  3. Bounding-box reject each obstruction before touching its solids,
     and stop at the first hit.
  4. FACE_MODE (a blocker whose solid is not a closed volume — an open
     mesh DirectShape import is the usual case) has two paths, chosen by
     face count. Below FACE_MODE_REVIT_THRESHOLD: a Python-side FaceGrid,
     see that class. Above it: ReferenceIntersector, Revit's own
     accelerated ray cast, ONE native call per ray instead of a managed
     per-face loop, faster on a large enough mesh despite being
     view-scoped. See `_default_view3d` for what that scoping costs.
"""

import math

from Autodesk.Revit.DB import (
    XYZ, Line, SolidCurveIntersectionOptions, BuiltInParameter,
    SetComparisonResult, View3D, ReferenceIntersector, FindReferenceTarget,
    FilteredElementCollector,
)

from tadpole import geom
from tadpole.config import (
    SUN_LATITUDE_FALLBACK, SUN_DAY_OF_YEAR, SUN_START_HOUR, SUN_END_HOUR,
    SUN_TIMESTEP, SUN_SAMPLE_HEIGHT_MM, SUN_COLUMN_MM, SUN_SURFACE_OFFSET_MM,
    SUN_REQUIRED_HOURS, SUN_NORTH_SIGN, FACE_GRID_CELL_MM,
    SUN_CONTEXT_REACH_MM, FACE_MODE_REVIT_THRESHOLD,
)

MM = 304.8
FACE_GRID_CELL = FACE_GRID_CELL_MM / MM       # internal feet
CONTEXT_REACH = SUN_CONTEXT_REACH_MM / MM     # internal feet


# ----------------------------------------------------------------------
# solar position
# ----------------------------------------------------------------------

def declination(day_of_year):
    """Solar declination, RADIANS. Spencer's Fourier series.

    Was Cooper's single-term approximation, which is off by about 0.4
    degrees at the equinox — the same 0.4 degrees that kept this from
    agreeing with the office DSH tool and with the Grasshopper original.
    Both of those use Spencer, so this does now.
    """
    g = 2.0 * math.pi / 365.0 * (day_of_year - 1)
    return (0.006918
            - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))


def sun_vectors(latitude_deg, north_angle=0.0,
                day_of_year=SUN_DAY_OF_YEAR,
                start_hour=SUN_START_HOUR, end_hour=SUN_END_HOUR,
                timestep=SUN_TIMESTEP):
    """Unit vectors pointing from the site TOWARD the sun.

    Mean solar time, so `hour` is solar hour directly. Returns
    [(solar_hour, XYZ)], sun-below-horizon steps already removed.

    Each step is sampled at its MIDDLE, and there are exactly `steps` of
    them. Sampling the ends instead gave steps + 1 samples, each still
    counted as a whole 1/timestep, so a fully sunlit point scored one
    timestep more than the window is long — 10.25 h out of a 10 h day at
    4 steps/hour. Mid-step is also symmetric about noon, which endpoint
    sampling is not once a step is dropped for the horizon.
    """
    phi = math.radians(latitude_deg)
    delta = declination(day_of_year)          # already radians
    theta = math.radians(north_angle * SUN_NORTH_SIGN)

    out = []
    steps = int(round((end_hour - start_hour) * timestep))
    for i in range(steps):
        hour = start_hour + (float(i) + 0.5) / timestep
        H = math.radians(15.0 * (hour - 12.0))

        altitude = math.asin(
            math.sin(phi) * math.sin(delta) +
            math.cos(phi) * math.cos(delta) * math.cos(H))
        if altitude <= 0.0:
            continue

        # azimuth measured from south, then turned to a compass bearing
        x = math.sin(H)
        y = math.cos(H) * math.sin(phi) - math.tan(delta) * math.cos(phi)
        azimuth = math.atan2(x, y) + math.pi

        east = math.cos(altitude) * math.sin(azimuth)
        north = math.cos(altitude) * math.cos(azimuth)
        up = math.sin(altitude)

        # rotate from true north into project coordinates
        ex = east * math.cos(theta) - north * math.sin(theta)
        ny = east * math.sin(theta) + north * math.cos(theta)

        out.append((hour, XYZ(ex, ny, up).Normalize()))
    return out


def site_latitude(doc):
    """Latitude in degrees, and whether it looks like Revit's default."""
    try:
        site = doc.SiteLocation
        latitude = math.degrees(site.Latitude)
        longitude = math.degrees(site.Longitude)
    except Exception:
        return SUN_LATITUDE_FALLBACK, True

    # Revit ships pointing at Boston
    is_default = (abs(latitude - 42.36) < 0.2 and abs(longitude + 71.06) < 0.2)
    return latitude, is_default


def true_north(doc):
    """(degrees between project and true north, whether it was READ).

    The two answers have to be separable. A failed read and a genuinely
    unrotated site both give 0.0, and they mean opposite things: one says
    the sun path needs no turning, the other says nobody knows which way
    it should be turned. Reporting a bare 0.00 for both hides an error
    that would put every shadow on the wrong facade.
    """
    try:
        position = doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)
        return math.degrees(position.Angle), True
    except Exception:
        return 0.0, False


def true_north_angle(doc):
    """Degrees between project north and true north."""
    return true_north(doc)[0]


# ----------------------------------------------------------------------
# obstructions
# ----------------------------------------------------------------------

SOLID_MODE = 'solid'
FACE_MODE = 'faces'


class Obstruction(object):
    __slots__ = ('lo', 'hi', 'solids', 'label', 'eid', 'mode', 'face_grid',
                 'ref_intersector')

    def __init__(self, lo, hi, solids, label=None, eid=None,
                 mode=SOLID_MODE, face_grid=None, ref_intersector=None):
        self.lo = lo
        self.hi = hi
        self.solids = solids
        self.label = label      # use class, for the probe report
        self.eid = eid          # element id, so a failure is clickable
        self.mode = mode        # how this blocker has to be tested
        # A FaceGrid, only built for FACE_MODE - see _face_grid. None
        # for a SOLID_MODE blocker, or a FACE_MODE one big enough to use
        # ref_intersector instead - see build_obstructions.
        self.face_grid = face_grid
        # (ReferenceIntersector, this element's own ElementId), only set
        # for a FACE_MODE blocker over FACE_MODE_REVIT_THRESHOLD faces
        # AND with a usable View3D - see build_obstructions and
        # _new_ref_intersector. Takes priority over face_grid in is_lit
        # when set.
        self.ref_intersector = ref_intersector


def _solid_bounds(solids):
    """Model-space bounds of a list of solids.

    Solid.GetBoundingBox returns a box in the solid's OWN coordinate
    system, with the transform that puts it back into model space. Using
    its Min/Max raw would place a family instance's blocker wherever the
    family origin happens to be, which for a Generic Model is usually not
    where the building is.
    """
    lo_x = lo_y = lo_z = None
    hi_x = hi_y = hi_z = None
    for solid in solids:
        try:
            box = solid.GetBoundingBox()
        except Exception:
            continue
        if box is None:
            continue
        transform = getattr(box, 'Transform', None)
        mn, mx = box.Min, box.Max
        corners = [XYZ(x, y, z)
                   for x in (mn.X, mx.X)
                   for y in (mn.Y, mx.Y)
                   for z in (mn.Z, mx.Z)]
        for corner in corners:
            if transform is not None:
                try:
                    corner = transform.OfPoint(corner)
                except Exception:
                    pass
            if lo_x is None:
                lo_x = hi_x = corner.X
                lo_y = hi_y = corner.Y
                lo_z = hi_z = corner.Z
                continue
            lo_x = min(lo_x, corner.X); hi_x = max(hi_x, corner.X)
            lo_y = min(lo_y, corner.Y); hi_y = max(hi_y, corner.Y)
            lo_z = min(lo_z, corner.Z); hi_z = max(hi_z, corner.Z)
    if lo_x is None:
        return None, None
    return XYZ(lo_x, lo_y, lo_z), XYZ(hi_x, hi_y, hi_z)


def _face_bounds(face):
    """World-space AABB of one face, from its own edge loop vertices.

    Exact and cheap for a planar face - no Triangulate() needed just for
    an extent, the loop's own endpoints already give it. None on any
    failure, so a bad face is skipped rather than crashing the index.
    """
    try:
        loops = face.GetEdgesAsCurveLoops()
    except Exception:
        return None
    if loops is None or loops.Count == 0:
        return None
    lo_x = lo_y = lo_z = None
    hi_x = hi_y = hi_z = None
    for loop in loops:
        for crv in loop:
            for i in (0, 1):
                try:
                    p = crv.GetEndPoint(i)
                except Exception:
                    continue
                if lo_x is None:
                    lo_x = hi_x = p.X
                    lo_y = hi_y = p.Y
                    lo_z = hi_z = p.Z
                    continue
                lo_x = min(lo_x, p.X); hi_x = max(hi_x, p.X)
                lo_y = min(lo_y, p.Y); hi_y = max(hi_y, p.Y)
                lo_z = min(lo_z, p.Z); hi_z = max(hi_z, p.Z)
    if lo_x is None:
        return None
    return XYZ(lo_x, lo_y, lo_z), XYZ(hi_x, hi_y, hi_z)


class FaceGrid(object):
    """A uniform 3D grid over one blocker's faces, for FACE_MODE.

    What makes a FACE_MODE blocker affordable. `hits_faces` is a bare
    linear scan: every face gets a real `face.Intersect` call, for every
    ray. A flat per-face bounding-box pre-check (cheaper per item, still
    O(faces) per ray) is not enough on its own either - a sun ray is a
    long diagonal line, and for a mesh roughly as large as the ray's own
    reach, the RAY'S bounding box can cover nearly the whole mesh even
    though the actual line only grazes a sliver of it. What is needed is
    to walk the cells the line itself passes through, not its box - see
    `_walk_cells`. On an open mesh DirectShape (a triangulated SketchUp
    import is the usual culprit, one PlanarFace per triangle, tens of
    thousands of them), that turns O(rays x faces) into roughly
    O(rays x cells actually crossed), which is what makes the sun study
    affordable instead of stalling on it.

    Cell size is FACE_GRID_CELL_MM (config). A face is registered in
    every cell its own AABB overlaps - one, usually, a handful for a
    face that straddles a boundary - never the whole grid.
    """
    __slots__ = ('cell', 'cells')

    def __init__(self, face_entries, cell_size):
        self.cell = cell_size
        self.cells = {}
        for entry in face_entries:
            _face, lo, hi = entry
            for key in self._keys(lo, hi):
                self.cells.setdefault(key, []).append(entry)

    def _keys(self, lo, hi):
        c = self.cell
        x0, x1 = int(math.floor(lo.X / c)), int(math.floor(hi.X / c))
        y0, y1 = int(math.floor(lo.Y / c)), int(math.floor(hi.Y / c))
        z0, z1 = int(math.floor(lo.Z / c)), int(math.floor(hi.Z / c))
        for ix in range(x0, x1 + 1):
            for iy in range(y0, y1 + 1):
                for iz in range(z0, z1 + 1):
                    yield (ix, iy, iz)

    def faces_in(self, key):
        return self.cells.get(key)


def _cull_box(lo, hi, min_z, plan_bounds):
    """True if the box lo..hi can be skipped - below min_z, or beyond
    plan_bounds' reach. Shared by the per-blocker and per-solid culls so
    the two can never quietly disagree about what "too low" or "too far"
    means.
    """
    if min_z is not None and hi.Z < min_z:
        return True
    if plan_bounds is not None:
        bx0, by0, bx1, by1 = plan_bounds
        gap_x = max(bx0 - hi.X, lo.X - bx1, 0.0)
        gap_y = max(by0 - hi.Y, lo.Y - by1, 0.0)
        if math.hypot(gap_x, gap_y) > CONTEXT_REACH:
            return True
    return False


def _face_grid(solids, cell_size, min_z=None, plan_bounds=None):
    """Build one `FaceGrid` from every face of every solid - ONCE.

    Culled per SOLID, not just per blocker, before a single face is
    touched. `build_obstructions` already culls the blocker as a whole,
    but that only catches the case where EVERY solid in the element
    fails - a DirectShape import (a whole street of small buildings
    merged into one element, typically one Solid each, is the usual
    shape for that) can easily have most of its solids entirely below
    min_z or beyond reach while one is not, and the blocker-level box
    alone cannot tell the difference: a single tall or nearby solid keeps
    the whole element's box - and therefore every face of every OTHER
    solid in it - alive.

    `solid.GetBoundingBox()` is one cheap call per solid; walking a
    face's edge loops to bound it (`_face_bounds`) is not, so checking
    the solid first is worth it whenever an element is made of more than
    one.

    Precomputes each SURVIVING face's own world-space box in Python and
    buckets it into the grid, so the hot path never has to ask Revit for
    a face's geometry again - only whether a ray crosses it, and only
    for faces the ray's own cell walk reaches.
    """
    entries = []
    for solid in solids:
        if min_z is not None or plan_bounds is not None:
            slo, shi = _solid_bounds([solid])
            if slo is not None and _cull_box(slo, shi, min_z, plan_bounds):
                continue
        try:
            faces = solid.Faces
        except Exception:
            continue
        for face in faces:
            bounds = _face_bounds(face)
            if bounds is None:
                continue
            lo, hi = bounds
            # Same exact box either way - _face_bounds already paid for
            # it. Checking it here catches a solid that survived the
            # coarser check above (it has SOME relevant face) but still
            # has other faces of its own that do not - a big terrain or
            # ground-plane solid spanning well below min_z is the usual
            # shape of that, not just a whole separate small building.
            if _cull_box(lo, hi, min_z, plan_bounds):
                continue
            entries.append((face, lo, hi))
    if not entries:
        return None
    return FaceGrid(entries, cell_size)


# ----------------------------------------------------------------------
# the Revit-native path for a very dense FACE_MODE blocker
# ----------------------------------------------------------------------

def _default_view3d(doc):
    """A View3D to run ReferenceIntersector against - active, or default.

    Prefers doc.ActiveView if it is already a non-template View3D -
    whatever the user was last looking at. Otherwise the model's default
    `{3D}` view by name, then any other non-template View3D as a last
    resort. None if nothing usable exists, so the caller can fall back
    to FaceGrid rather than crash on this.

    Deliberately does NOT create a view. A created-and-deleted temp view
    would guarantee correctness (everything visible, no filters) but
    needs a transaction, and this whole analysis pass is read-only by
    design. Using an existing view instead means the result depends on
    THAT view's own visibility, filters, phase and worksets - an element
    hidden there is silently never tested. See FACE_MODE_REVIT_THRESHOLD
    in config.py.
    """
    try:
        active = doc.ActiveView
        if isinstance(active, View3D) and not active.IsTemplate:
            return active
    except Exception:
        pass

    try:
        views = list(FilteredElementCollector(doc).OfClass(View3D))
    except Exception:
        return None

    for view in views:
        try:
            if not view.IsTemplate and view.Name == u'{3D}':
                return view
        except Exception:
            continue
    for view in views:
        try:
            if not view.IsTemplate:
                return view
        except Exception:
            continue
    return None


def _new_ref_intersector(view3d):
    """(ReferenceIntersector or None, reason string or None).

    Shared across every big blocker in one build_obstructions call - it
    is not element-scoped by construction (see _ref_intersector_hit for
    what that costs), so nothing is gained by building a fresh one per
    element.

    The reason is real evidence, not a guess. This used to swallow any
    exception into a single "unavailable" bucket with a hand-written
    message that ASSUMED the cause was always "no usable 3D view" - which
    reported the wrong cause the moment the actual view WAS fine and the
    real failure was the constructor or a property set throwing for some
    other reason (an older API without this piece, or a view this
    document genuinely cannot resolve it against). describe_exception in
    roofs.py exists for exactly this lesson: report what actually broke.
    """
    if view3d is None:
        return None, 'no usable 3D view'
    try:
        intersector = ReferenceIntersector(view3d)
        intersector.Target = FindReferenceTarget.Face
        intersector.FindReferencesInRevitLinks = False
        return intersector, None
    except Exception as exc:
        return None, '{0}: {1}'.format(type(exc).__name__, exc)


def _ref_intersector_hit(intersector, element_id, origin, direction, length):
    """Does the ray hit THIS element within `length`, via Revit's own
    accelerated ray cast - ONE native call per ray, whatever the face
    count behind it, instead of a managed per-face loop.

    `Find` is not filtered to one element at the API level (`intersector`
    is the one shared ReferenceIntersector built by _new_ref_intersector,
    scanning everything visible in the view) and is not documented as
    sorted, so every returned reference is checked rather than assuming
    the first is nearest or is even this element.
    """
    try:
        hits = intersector.Find(origin, direction)
    except Exception:
        return False
    if not hits:
        return False
    for hit in hits:
        try:
            if hit.Proximity > length:
                continue
            if hit.GetReference().ElementId == element_id:
                return True
        except Exception:
            continue
    return False


def _walk_cells(origin, direction, length, cell_size):
    """Grid cell keys the segment origin -> origin + direction*length
    passes through, via 3D DDA (Amanatides & Woo).

    `direction` must be a unit vector. This is what makes `FaceGrid`
    actually sub-linear for a long ray: it visits only the handful of
    cells the LINE crosses, not every cell inside the line's (much
    bigger, for a diagonal ray) bounding box.

    TIES ARE NOT RARE ENOUGH TO IGNORE. Advancing only one axis when two
    or three t_max values are equal cuts the corner - it can skip a cell
    the ray demonstrably passes through, verified against brute-force
    sampling: a plain single-axis tie-break missed real cells on an exact
    (1,1,1) direction. A missed cell here is not a slow ray, it is a
    wrong "lit" - an occluding face in that cell is never tested. So
    every axis tied for the smallest step is advanced together, and every
    combination of the tied axes is yielded, covering the shared
    edge/corner the ray actually touches from every side.
    """
    c = cell_size
    x, y, z = origin.X, origin.Y, origin.Z
    dx, dy, dz = direction.X, direction.Y, direction.Z

    ix = int(math.floor(x / c))
    iy = int(math.floor(y / c))
    iz = int(math.floor(z / c))

    def axis(d, p, i):
        if d > 1e-12:
            boundary = (i + 1) * c
            return 1, (boundary - p) / d, c / d
        if d < -1e-12:
            boundary = i * c
            return -1, (boundary - p) / d, c / -d
        return 0, float('inf'), float('inf')

    step_x, t_max_x, t_delta_x = axis(dx, x, ix)
    step_y, t_max_y, t_delta_y = axis(dy, y, iy)
    step_z, t_max_z, t_delta_z = axis(dz, z, iz)

    yield (ix, iy, iz)

    t = 0.0
    # x3 headroom over the geometric step count: an exact-tie ray visits
    # every combination of its tied axes each step, not just one cell, so
    # it can take more steps than a non-tied ray of the same length.
    max_steps = int(length / c) * 3 + 16
    steps = 0
    while t < length and steps < max_steps:
        steps += 1
        t_min = min(t_max_x, t_max_y, t_max_z)
        tol = 1e-9 * max(1.0, abs(t_min))
        tied_x = step_x != 0 and (t_max_x - t_min) <= tol
        tied_y = step_y != 0 and (t_max_y - t_min) <= tol
        tied_z = step_z != 0 and (t_max_z - t_min) <= tol
        if not (tied_x or tied_y or tied_z):
            break

        nx0, ny0, nz0 = ix, iy, iz
        nx1 = ix + step_x if tied_x else ix
        ny1 = iy + step_y if tied_y else iy
        nz1 = iz + step_z if tied_z else iz
        for cx in ((nx0, nx1) if tied_x else (nx0,)):
            for cy in ((ny0, ny1) if tied_y else (ny0,)):
                for cz in ((nz0, nz1) if tied_z else (nz0,)):
                    if (cx, cy, cz) != (nx0, ny0, nz0):
                        yield (cx, cy, cz)

        if tied_x:
            ix += step_x; t_max_x += t_delta_x
        if tied_y:
            iy += step_y; t_max_y += t_delta_y
        if tied_z:
            iz += step_z; t_max_z += t_delta_z
        t = t_min


def _encloses(lo, hi, inner_lo, inner_hi, tol=1.0 / 304.8):
    """Does the box lo..hi contain inner_lo..inner_hi, within 1 mm?"""
    return (lo.X <= inner_lo.X + tol and hi.X >= inner_hi.X - tol and
            lo.Y <= inner_lo.Y + tol and hi.Y >= inner_hi.Y - tol and
            lo.Z <= inner_lo.Z + tol and hi.Z >= inner_hi.Z - tol)


def centre_ray(lo, hi):
    """A short ray straight through the middle of a box, on the X axis.

    Short on purpose: it has to test the solid, not the tolerance of a
    two-kilometre line against a thirty-metre block.
    """
    cy = (lo.Y + hi.Y) * 0.5
    cz = (lo.Z + hi.Z) * 0.5
    size = max(hi.X - lo.X, hi.Y - lo.Y, hi.Z - lo.Z)
    if size <= 0:
        size = 1.0
    stand_off = size * 2.0 + 10.0
    start = XYZ((lo.X + hi.X) * 0.5 - stand_off, cy, cz)
    end = XYZ(start.X + stand_off * 2.0, cy, cz)
    try:
        return Line.CreateBound(start, end)
    except Exception:
        return None


def pick_mode(lo, hi, solids, options):
    """How this blocker has to be tested. (mode, works, error).

    Asked ONCE per element rather than per ray. Volume-based occlusion is
    preferred where it works — it is cheaper and it cannot be fooled by a
    face the ray only grazes — and face-based is the fallback for
    geometry the volume test refuses to answer for.
    """
    ray = centre_ray(lo, hi)
    if ray is None:
        return SOLID_MODE, False, 'could not build a test ray'

    hit, error = hits_solid(solids, ray, options)
    if hit:
        return SOLID_MODE, True, error
    if hits_faces(solids, ray):
        return FACE_MODE, True, error
    return SOLID_MODE, False, error


def build_obstructions(records, stats=None, options=None,
                        min_z=None, plan_bounds=None):
    """Every collected element becomes a blocker, context included.

    A facade must be able to shade itself and be shaded by its
    neighbours, so nothing is excluded — not even the element the test
    point sits on.

    `stats` is an optional dict that comes back filled in with what
    happened, per use class. This used to drop an element silently when
    it had no bounding box, and that is indistinguishable in the result
    from an element that simply casts no shadow — which is exactly the
    shape of "it ignores generic models".

    THE BOUNDS COME FROM THE SOLIDS, not from the element.

    `_ray_hits_box` is a cheap reject in front of the real intersection
    test, so it is only sound while its box actually encloses the solids
    behind it. `Element.get_BoundingBox(None)` does not guarantee that
    for a family instance: `BoundingBoxXYZ` carries its own `Transform`,
    and reading `.Min`/`.Max` raw can describe a box at the family
    origin while `GetInstanceGeometry()` returns solids out on the site.

    The blocker then counts as shading and rejects every ray, so the
    element is in the obstruction list, is reported as shading, and casts
    no shadow — which is how "it ignores generic models" happens with a
    report that says 374 of them are shading.

    Deriving the box from the solids that will actually be intersected
    makes the two consistent by construction. `box disagreed` counts how
    often the element's own box would have been wrong.

    `min_z` and `plan_bounds` (both optional, both from
    inputs.study_bounds) cull a blocker BEFORE pick_mode or a FaceGrid is
    ever built for it — the two most expensive things this function does
    for a FACE_MODE element.

      min_z: every sun vector in this study has a positive Z component
      (sun_vectors already drops anything below the horizon), so a ray's
      Z only ever increases from its origin. A blocker whose own highest
      point is still below the lowest sample point in the study cannot
      be crossed by any ray in it - not an approximation, a proof.

      plan_bounds: SUN_CONTEXT_REACH_MM from the tested walls' own plan
      extent. This ONE is an approximation - a low sun and a tall enough
      far blocker could in principle still shade from beyond it - see
      the constant's own comment in config.py.
    """
    if options is None:
        options = intersection_options()
    out = []

    # Resolved lazily - only when a blocker actually crosses
    # FACE_MODE_REVIT_THRESHOLD - and at most once for this whole call,
    # since the view cannot change between blockers in one run. `False`
    # is the not-yet-tried sentinel; a real attempt leaves either a
    # working ReferenceIntersector or None (tried, unavailable) here.
    ref_intersector = [False]

    def get_ref_intersector():
        if ref_intersector[0] is False:
            doc = records[0].element.Document if records else None
            view3d = _default_view3d(doc) if doc is not None else None
            ref_intersector[0] = _new_ref_intersector(view3d)
        return ref_intersector[0]

    def note(rec, key):
        if stats is None:
            return
        per = stats.setdefault(rec.use_class, {})
        per[key] = per.get(key, 0) + 1

    for rec in records:
        if not rec.has_geometry:
            note(rec, 'no geometry')
            continue

        solids = [s for s in geom.iter_solids(rec.element)]
        if not solids:
            note(rec, 'no solids')
            continue

        lo, hi = _solid_bounds(solids)
        if lo is None:
            note(rec, 'no bounds')
            continue

        if _cull_box(lo, hi, min_z, None):
            note(rec, 'below lowest sample point')
            continue
        if _cull_box(lo, hi, None, plan_bounds):
            note(rec, 'beyond context reach')
            continue

        # Diagnosis only — the element's box is never used for the test.
        try:
            box = rec.element.get_BoundingBox(None)
        except Exception:
            box = None
        if box is None:
            note(rec, 'no element box')
        elif not _encloses(box.Min, box.Max, lo, hi):
            note(rec, 'box disagreed')

        mode, works, error = pick_mode(lo, hi, solids, options)
        note(rec, mode + ' mode')
        if not works:
            note(rec, 'blocks nothing')
            if error and stats is not None:
                per = stats.setdefault(rec.use_class, {})
                per.setdefault('errors', {})
                per['errors'][error] = per['errors'].get(error, 0) + 1

        # Built ONCE here, only for what will actually need it - a
        # SOLID_MODE blocker never looks at a face again after pick_mode.
        face_grid = None
        ref_pair = None
        if mode == FACE_MODE:
            try:
                face_count = sum(s.Faces.Size for s in solids)
            except Exception:
                face_count = 0
            if face_count > FACE_MODE_REVIT_THRESHOLD:
                intersector, reason = get_ref_intersector()
                if intersector is not None:
                    ref_pair = (intersector, rec.element.Id)
                    note(rec, 'ref intersector mode')
                else:
                    note(rec, 'ref intersector unavailable - used grid')
                    if stats is not None:
                        per = stats.setdefault(rec.use_class, {})
                        per.setdefault('errors', {})
                        per['errors'][reason] = per['errors'].get(reason, 0) + 1
            if ref_pair is None:
                # min_z/plan_bounds passed through so _face_grid can cull
                # PER SOLID, not just at the whole-blocker level already
                # applied above - see _face_grid's own docstring.
                face_grid = _face_grid(solids, FACE_GRID_CELL, min_z,
                                       plan_bounds)

        out.append(Obstruction(lo, hi, solids, rec.use_class,
                               getattr(rec, 'id', None), mode, face_grid,
                               ref_pair))
        note(rec, 'shading')
        _widen(stats, rec.use_class, lo, hi)

    return out


def _widen(stats, use_class, lo, hi):
    """Accumulate where a class's blockers actually SIT, in model space.

    The decisive check for "it is in the list but casts no shadow". If a
    class's blockers span a different part of the model from the walls
    being tested, no ray can reach them and no amount of tuning will
    help — a DirectShape whose geometry came back untransformed lands at
    the origin while the building is a kilometre away, and nothing else
    in this report would show it.
    """
    if stats is None:
        return
    per = stats.setdefault(use_class, {})
    box = per.get('extent')
    if box is None:
        per['extent'] = [lo.X, lo.Y, lo.Z, hi.X, hi.Y, hi.Z]
        return
    box[0] = min(box[0], lo.X); box[3] = max(box[3], hi.X)
    box[1] = min(box[1], lo.Y); box[4] = max(box[4], hi.Y)
    box[2] = min(box[2], lo.Z); box[5] = max(box[5], hi.Z)


def _ray_hits_box(origin, direction, length, lo, hi):
    """Slab test. Cheap reject before any solid is touched."""
    t_near = 0.0
    t_far = length
    for o, d, a, b in ((origin.X, direction.X, lo.X, hi.X),
                       (origin.Y, direction.Y, lo.Y, hi.Y),
                       (origin.Z, direction.Z, lo.Z, hi.Z)):
        if abs(d) < 1e-12:
            if o < a or o > b:
                return False
            continue
        t1 = (a - o) / d
        t2 = (b - o) / d
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > t_near:
            t_near = t1
        if t2 < t_far:
            t_far = t2
        if t_near > t_far:
            return False
    return True


def hits_solid(solids, ray, options):
    """Does the ray pass INSIDE any of these solids?

    Returns (hit, error). The error is returned rather than swallowed:
    a thrown IntersectWithCurve looks exactly like a clean miss, and a
    blocker that always throws is a blocker that never shades.
    """
    error = None
    for solid in solids:
        try:
            hit = solid.IntersectWithCurve(ray, options)
        except Exception as exc:
            if error is None:
                error = '{0}: {1}'.format(type(exc).__name__, exc)
            continue
        if hit is not None and hit.SegmentCount > 0:
            return True, error
    return False, error


def hits_faces(solids, ray):
    """Does the ray cross any FACE of these solids?

    The fallback for geometry that IntersectWithCurve will not treat as
    enclosing anything. Crossing a face is a weaker question than being
    inside a volume — it needs no closed shell — and for occlusion it is
    the question that actually matters: something is in the way.
    """
    for solid in solids:
        try:
            faces = solid.Faces
        except Exception:
            continue
        for face in faces:
            try:
                if face.Intersect(ray) == SetComparisonResult.Overlap:
                    return True
            except Exception:
                continue
    return False


def hits_faces_indexed(face_grid, origin, direction, length, ray):
    """Same question as `hits_faces`, via the grid instead of a scan.

    `hits_faces` is a bare linear scan - every face pays for a real
    `face.Intersect` call, for every ray. This walks only the cells
    `_walk_cells` says the ray's LINE actually crosses - not its
    (much bigger, for a diagonal ray) bounding box - and only tests the
    faces registered in those cells, each once even if a face spans
    several visited cells. `_ray_hits_box` still runs first as a final,
    cheap per-face check before the real `face.Intersect` call, since a
    cell can hold faces the ray's exact line still misses.
    """
    if face_grid is None:
        return False
    seen = set()
    for key in _walk_cells(origin, direction, length, face_grid.cell):
        entries = face_grid.faces_in(key)
        if not entries:
            continue
        for face, lo, hi in entries:
            fid = id(face)
            if fid in seen:
                continue
            seen.add(fid)
            if not _ray_hits_box(origin, direction, length, lo, hi):
                continue
            try:
                if face.Intersect(ray) == SetComparisonResult.Overlap:
                    return True
            except Exception:
                continue
    return False


def is_lit(origin, direction, obstructions, reach, options):
    """True when nothing stands between this point and the sun."""
    end = XYZ(origin.X + direction.X * reach,
              origin.Y + direction.Y * reach,
              origin.Z + direction.Z * reach)
    try:
        ray = Line.CreateBound(origin, end)
    except Exception:
        return False

    for blocker in obstructions:
        if not _ray_hits_box(origin, direction, reach, blocker.lo, blocker.hi):
            continue
        if blocker.mode == FACE_MODE:
            if blocker.ref_intersector is not None:
                intersector, element_id = blocker.ref_intersector
                if _ref_intersector_hit(intersector, element_id, origin,
                                        direction, reach):
                    return False
                continue
            if hits_faces_indexed(blocker.face_grid, origin, direction,
                                  reach, ray):
                return False
            continue
        hit, _error = hits_solid(blocker.solids, ray, options)
        if hit:
            return False
    return True


# ----------------------------------------------------------------------
# evaluating one wall run
# ----------------------------------------------------------------------

class Column(object):
    """One sampled position along a wall run."""

    def __init__(self, t0, t1, point, hours, required=None):
        self.t0 = t0
        self.t1 = t1
        self.point = point
        self.hours = hours
        # Carried on the column rather than read from config at compare
        # time, so a run cannot be judged against a threshold other than
        # the one it was given and reported with.
        self.required = (SUN_REQUIRED_HOURS if required is None
                         else float(required))

    @property
    def lit_enough(self):
        return self.hours >= self.required


def evaluate_run(seg, t0, t1, vectors, obstructions, reach, options,
                 required=None):
    """Sun hours along one outer wall run, one column at a time.

    Columns are sampled at window height rather than gridding the whole
    storey — insolation is judged at the window, and a full grid costs
    three times as much for a number nobody uses.
    """
    from tadpole import classify

    step = SUN_COLUMN_MM / MM
    offset = SUN_SURFACE_OFFSET_MM / MM
    height = SUN_SAMPLE_HEIGHT_MM / MM

    span = t1 - t0
    if span <= step * 0.5:
        count = 1
    else:
        count = int(math.ceil(span / step))

    z = seg.z0 + min(height, max(0.0, (seg.z1 - seg.z0) * 0.5))
    normal = seg.normal
    per_step = 1.0 / float(SUN_TIMESTEP)

    columns = []
    for i in range(count):
        a = t0 + span * (float(i) / count)
        b = t0 + span * (float(i + 1) / count)
        mid = (a + b) * 0.5

        points = classify.segment_points(seg, mid, mid)
        if points is None:
            continue
        base = points[0]
        origin = XYZ(base.X + normal.X * offset,
                     base.Y + normal.Y * offset,
                     z)

        hours = 0.0
        for _hour, vector in vectors:
            # the wall shades itself from anything behind it
            if vector.DotProduct(normal) <= 0.0:
                continue
            if is_lit(origin, vector, obstructions, reach, options):
                hours += per_step

        columns.append(Column(a, b, origin, hours, required))

    return columns


def _contiguous_runs(columns, want_lit):
    """Merge neighbouring columns that agree on the threshold.

    Returns [(t0, t1, mean hours)]. Both `dark_runs` and `lit_runs` go
    through here so the two can never drift apart — between them they
    must account for every column exactly once.
    """
    runs = []
    current = None
    for column in columns:
        if column.lit_enough != want_lit:
            if current is not None:
                runs.append(current)
                current = None
            continue
        if current is None:
            current = [column.t0, column.t1, [column.hours]]
        else:
            current[1] = column.t1
            current[2].append(column.hours)
    if current is not None:
        runs.append(current)
    return [(a, b, sum(h) / len(h)) for a, b, h in runs]


def dark_runs(columns):
    """Contiguous stretches that fail the threshold, merged."""
    return _contiguous_runs(columns, False)


def lit_runs(columns):
    """Contiguous stretches that MEET the threshold, merged.

    The complement of `dark_runs` over the same columns — the lit facade
    the cutter is allowed to put a window on.
    """
    return _contiguous_runs(columns, True)


def model_reach(obstructions):
    """A ray long enough to leave the model behind."""
    if not obstructions:
        return 1000.0
    lo_x = min([o.lo.X for o in obstructions])
    lo_y = min([o.lo.Y for o in obstructions])
    lo_z = min([o.lo.Z for o in obstructions])
    hi_x = max([o.hi.X for o in obstructions])
    hi_y = max([o.hi.Y for o in obstructions])
    hi_z = max([o.hi.Z for o in obstructions])
    diagonal = math.sqrt((hi_x - lo_x) ** 2 + (hi_y - lo_y) ** 2 +
                         (hi_z - lo_z) ** 2)
    return max(diagonal * 1.5, 100.0)


def intersection_options():
    return SolidCurveIntersectionOptions()


def stamp_hours(element, hours, verdict=None, required=None):
    """Persist the measured hours where another script can read them.

    AVF cannot be read back and a project parameter needs a shared
    parameter file to exist first. Comments always exists, is
    schedulable, and survives a save.

    The string carries the MEAN hours over the stretch, the verdict, and
    the threshold it was judged against - because a bare number is not
    reviewable a month later when the threshold has moved.

        TDPL sun=4.25h lit (req 3.0h)
    """
    text = 'TDPL sun={0:.2f}h'.format(hours)
    if verdict:
        text += ' {0}'.format(verdict)
    text += ' (req {0:g}h)'.format(
        SUN_REQUIRED_HOURS if required is None else float(required))
    try:
        param = element.get_Parameter(
            BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if param is not None and not param.IsReadOnly:
            param.Set(text)
            return True
    except Exception:
        pass
    return False


# ----------------------------------------------------------------------
# a controlled probe: does a blocker block?
# ----------------------------------------------------------------------

def probe_occlusion(obstructions, options, limit=12, prefer=None):
    """Fire a ray straight THROUGH each blocker and see if it registers.

    Everything else in this module measures the sun, which mixes the
    occlusion test up with wall orientation, sample position, sun vectors
    and the self-shading skip. When a blocker is in the list, in the
    right place, with a correct bounding box, and still casts no shadow,
    none of that is the question any more — the question is whether
    `Solid.IntersectWithCurve` reports a hit on THIS solid at all.

    So this ignores the sun entirely. It takes each blocker's own centre,
    stands well outside it on the X axis, and aims straight at the middle.
    A ray like that MUST pass through a closed solid. If it does not, the
    solid cannot block anything, whatever it looks like in the model —
    which is what a DirectShape carrying an open shell does: it reports
    faces and a volume, and contains nothing.

    Returns [(label, eid, size_m, box_hit, solid_hit, volume_m3, faces)].
    """
    picked = []
    if prefer:
        picked = [o for o in obstructions if o.label == prefer][:limit]
    if len(picked) < limit:
        for blocker in obstructions:
            if blocker not in picked:
                picked.append(blocker)
            if len(picked) >= limit:
                break

    rows = []
    for blocker in picked:
        lo, hi = blocker.lo, blocker.hi
        cx = (lo.X + hi.X) * 0.5
        cy = (lo.Y + hi.Y) * 0.5
        cz = (lo.Z + hi.Z) * 0.5
        size = max(hi.X - lo.X, hi.Y - lo.Y, hi.Z - lo.Z)
        if size <= 0:
            size = 1.0

        stand_off = size * 2.0 + 10.0
        start = XYZ(cx - stand_off, cy, cz)
        length = stand_off * 2.0
        direction = XYZ(1.0, 0.0, 0.0)

        box_hit = _ray_hits_box(start, direction, length, lo, hi)

        volume = 0.0
        faces = 0
        for solid in blocker.solids:
            try:
                volume += solid.Volume
                faces += solid.Faces.Size
            except Exception:
                pass

        try:
            ray = Line.CreateBound(start, XYZ(start.X + length, cy, cz))
        except Exception:
            ray = None

        solid_hit = False
        face_hit = False
        error = None
        if ray is not None:
            solid_hit, error = hits_solid(blocker.solids, ray, options)
            if not solid_hit:
                face_hit = hits_faces(blocker.solids, ray)

        rows.append((blocker.label, blocker.eid, size * 0.3048,
                     box_hit, solid_hit, face_hit,
                     volume * 0.3048 ** 3, faces, blocker.mode, error))
    return rows


def arc_study(records, vectors, obstructions, reach, options, required=None, step_deg=10.0):
    """The sun on the ROUNDED walls (user, 2026-10-20, Str200 V1): a curved face is no wall run, so the study never
    saw it. Every vertical arc at the foot of a curved face of `records` is sampled every `step_deg` at window height,
    a hand off its surface, against the same sun and the same obstructions as the walls; the samples go to
    `geom.ARC_SUN` by (arc, level). Returns how many arcs were studied. Never raises."""
    from tadpole import arcs
    required = SUN_REQUIRED_HOURS if required is None else required
    offset = SUN_SURFACE_OFFSET_MM / MM
    height = SUN_SAMPLE_HEIGHT_MM / MM
    per_step = 1.0 / float(SUN_TIMESTEP)
    count = 0
    for rec in records:
        try:
            if not rec.has_geometry:
                continue
            faces = rec.geometry['faces'].get(geom.NONPLANAR, [])
        except Exception:
            continue
        for face in faces:
            try:
                loop, _n = geom.outer_loop(face)
                if loop is None:
                    continue
                curves = [c for c in loop]
                zs = [c.GetEndPoint(k).Z for c in curves for k in (0, 1)]
                z0, z1 = min(zs), max(zs)
                if z1 - z0 < 1.0:
                    continue                    # not a wall
                for crv in curves:
                    if crv.GetType().Name != 'Arc':
                        continue
                    a, b = crv.GetEndPoint(0), crv.GetEndPoint(1)
                    if abs(a.Z - z0) > 0.01 or abs(b.Z - z0) > 0.01:
                        continue                # the arc at its foot only
                    c, r = crv.Center, crv.Radius
                    key = (round(c.X, 3), round(c.Y, 3), round(r, 3))
                    n = max(1, int(math.ceil(math.degrees(crv.Length / r) / step_deg)))
                    samples = []
                    for i in range(n):
                        p = crv.Evaluate((i + 0.5) / n, True)
                        nx, ny = p.X - c.X, p.Y - c.Y
                        length = math.hypot(nx, ny) or 1.0
                        nx, ny = nx / length, ny / length
                        try:
                            fn = face.ComputeNormal(face.Project(p).UVPoint)
                            if fn.X * nx + fn.Y * ny < 0.0:
                                nx, ny = -nx, -ny
                        except Exception:
                            pass
                        origin = XYZ(p.X + nx * offset, p.Y + ny * offset,
                                     z0 + min(height, max(0.0, (z1 - z0) * 0.5)))
                        hours = 0.0
                        for _hour, vector in vectors:
                            if vector.X * nx + vector.Y * ny <= 0.0:
                                continue
                            if is_lit(origin, vector, obstructions, reach, options):
                                hours += per_step
                        samples.append((p.X, p.Y, nx, ny, hours >= required))
                    arcs.ARC_SUN.setdefault((key, rec.level_name), []).extend(samples)
                    count += 1
            except Exception:
                continue
    return count
