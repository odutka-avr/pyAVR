# -*- coding: utf-8 -*-
"""«Перекриття»: a 200 mm floor slab round the whole floor.

When "Виправлення геометрії квартири та додавання сходово-ліфтового вузла" runs on a floor plan, a Floor
of 200 mm is made for the floor the plan shows. Its outline is the building footprint of the floor (every
roof the plan shows: the apartment footprints, the МЗК, the stair cores) PLUS what sticks out of it: the walls
of the placed apartments and stair cores (balcony and loggia walls reach beyond the footprint) and the
outlines of their balcony / loggia rooms. The facade insulation walls (foam, mineral wool) are left out: the
slab ends at the masonry. The outline is the union of all those shapes, made with Revit's own boolean
operation on thin solids, so touching and overlapping shapes become one outline (holes stay holes).

The slab is made again each run (the old one is deleted) and is copied together with the floor when it is
copied onto the identical floors (floor_copy). Its element is remembered per level in placed_apartments.

Runs on IronPython 2.7 (pyRevit) and CPython 3 (the tests); only the functions under "Revit side" touch Revit.
"""

import math

SLAB_THICKNESS_MM = 200.0
_FT_PER_MM = 1.0 / 304.8
_MIN_EDGE_FT = 0.01                 # shorter edges of a shape are dropped (Revit refuses tiny curves)
_MIN_SLAB_AREA_FT2 = 10.0           # a piece of the union smaller than this (~1 m2) is a sliver, not a slab
_BALCONY_WORDS = (u"балкон", u"лоджі")
SLAB_TYPE_NAME = u"Froglet Перекриття 200"
SLAB_MARK = u"Froglet перекриття"       # written to the slab's Comments: finds ours among the floors of a level
SLAB_TOP_MARK = u"Froglet перекриття над верхнім поверхом"      # the slab over the top floor
_OVERLAP_FT = 0.02                     # how much touching shapes are grown before the union (~6 mm)


# ------------------------------------------------------------------ Revit-free parts

def signed_area(points):
    total = 0.0
    for index in range(len(points)):
        x1, y1 = points[index][0], points[index][1]
        x2, y2 = points[(index + 1) % len(points)][0], points[(index + 1) % len(points)][1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def clean_polygon(points, min_edge=_MIN_EDGE_FT):
    """The polygon counter-clockwise, without repeated points and tiny edges; [] when nothing is left."""
    cleaned = []
    for point in points:
        if not cleaned or math.hypot(point[0] - cleaned[-1][0], point[1] - cleaned[-1][1]) >= min_edge:
            cleaned.append((float(point[0]), float(point[1])))
    while len(cleaned) > 1 and math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) < min_edge:
        cleaned.pop()
    if len(cleaned) < 3 or abs(signed_area(cleaned)) < 1e-6:
        return []
    if signed_area(cleaned) < 0:
        cleaned.reverse()
    return cleaned


def wall_rectangle(p0, p1, width):
    """The plan of a straight wall: its centre line `p0`-`p1` thickened by `width` (feet), as 4 points."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length < 1e-6 or width <= 0:
        return []
    nx, ny = -dy / length * width / 2.0, dx / length * width / 2.0
    return [(p0[0] + nx, p0[1] + ny), (p1[0] + nx, p1[1] + ny), (p1[0] - nx, p1[1] - ny), (p0[0] - nx, p0[1] - ny)]


def grow_polygon(polygon, delta):
    """The counter-clockwise `polygon` pushed outward by `delta` on every side (mitred corners). Shapes that only
    touch (a wall against a footprint) then overlap a little, so Revit's union makes ONE piece of them; the slab
    ends `delta` farther out, a few millimetres. A polygon with a hairpin corner is returned as it is."""
    count = len(polygon)
    grown = []
    for index in range(count):
        prev_point, point, next_point = polygon[index - 1], polygon[index], polygon[(index + 1) % count]
        d1 = (point[0] - prev_point[0], point[1] - prev_point[1])
        d2 = (next_point[0] - point[0], next_point[1] - point[1])
        l1, l2 = math.hypot(d1[0], d1[1]), math.hypot(d2[0], d2[1])
        if l1 < 1e-9 or l2 < 1e-9:
            return list(polygon)
        n1 = (d1[1] / l1, -d1[0] / l1)           # outward (right-hand) normals of a counter-clockwise polygon
        n2 = (d2[1] / l2, -d2[0] / l2)
        factor = 1.0 + n1[0] * n2[0] + n1[1] * n2[1]
        if factor < 0.2:
            return list(polygon)
        grown.append((point[0] + (n1[0] + n2[0]) * delta / factor, point[1] + (n1[1] + n2[1]) * delta / factor))
    return grown


def is_balcony_name(name):
    name = (name or u"").lower()
    return any(word in name for word in _BALCONY_WORDS)


def shapes_of_the_floor(footprints, walls, balcony_rooms):
    """All the shapes whose union is the slab outline, as clean counter-clockwise polygons:
    `footprints` [polygon], `walls` [(p0, p1, width)] of the apartments and cores (insulation left out),
    `balcony_rooms` [polygon]."""
    shapes = []
    for polygon in footprints:
        shapes.append(clean_polygon(polygon))
    for p0, p1, width in walls:
        shapes.append(clean_polygon(wall_rectangle(p0, p1, width)))
    for polygon in balcony_rooms:
        shapes.append(clean_polygon(polygon))
    return [shape for shape in shapes if shape]


def storey_height(floor_heights):
    """The height of the top storey (feet) from the heights of the floors, or None with fewer than two."""
    heights = sorted(floor_heights)
    return heights[-1] - heights[-2] if len(heights) >= 2 else None


def keep_real_pieces(areas_ft2, minimum=_MIN_SLAB_AREA_FT2):
    """Indexes of the union pieces that are big enough to be a slab."""
    return [index for index, area in enumerate(areas_ft2) if abs(area) >= minimum]


# ------------------------------------------------------------------ Revit side

def _id_int(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def _loop(polygon, z):
    from Autodesk.Revit.DB import CurveLoop, Line, XYZ

    loop = CurveLoop()
    for index in range(len(polygon)):
        a, b = polygon[index], polygon[(index + 1) % len(polygon)]
        loop.Append(Line.CreateBound(XYZ(a[0], a[1], z), XYZ(b[0], b[1], z)))
    return loop


def union_outline(shapes, z):
    """([loops of each separate piece of the union of `shapes` (polygons): its outer loop and holes], how many
    shapes Revit could not unite): the bottom faces of the unified thin solids."""
    from Autodesk.Revit.DB import (
        BooleanOperationsType, BooleanOperationsUtils, CurveLoop, GeometryCreationUtilities, PlanarFace, XYZ,
    )
    from System.Collections.Generic import List

    union, skipped = None, 0
    for polygon in shapes:
        try:
            loops = List[CurveLoop]()
            loops.Add(_loop(polygon, z))
            solid = GeometryCreationUtilities.CreateExtrusionGeometry(loops, XYZ.BasisZ, 1.0)
            union = solid if union is None else BooleanOperationsUtils.ExecuteBooleanOperation(
                union, solid, BooleanOperationsType.Union)
        except Exception:
            skipped += 1
    if union is None:
        return [], skipped
    pieces = []
    for face in union.Faces:
        try:
            if isinstance(face, PlanarFace) and face.FaceNormal.Z < -0.99:
                pieces.append(list(face.GetEdgesAsCurveLoops()))
        except Exception:
            skipped += 1
    return pieces, skipped


def _loop_points(loop):
    return [(curve.GetEndPoint(0).X, curve.GetEndPoint(0).Y) for curve in loop]


def slab_type(document):
    """The floor type of 200 mm: an existing one of that thickness, else a copy of a floor type with a single
    200 mm layer (made once, SLAB_TYPE_NAME). Call it inside a transaction."""
    import apartment_group_placement as agp
    from Autodesk.Revit.DB import (
        BuiltInParameter, CompoundStructure, CompoundStructureLayer, FilteredElementCollector, FloorType,
        MaterialFunctionAssignment,
    )
    from System.Collections.Generic import List

    wanted = SLAB_THICKNESS_MM * _FT_PER_MM
    candidates = []
    for floor_type in FilteredElementCollector(document).OfClass(FloorType):
        try:
            if floor_type.IsFoundationSlab:
                continue
            thickness = floor_type.get_Parameter(BuiltInParameter.FLOOR_ATTR_DEFAULT_THICKNESS_PARAM).AsDouble()
        except Exception:
            continue
        candidates.append((floor_type, thickness))
    for floor_type, thickness in candidates:
        if agp._element_name(floor_type) == SLAB_TYPE_NAME and abs(thickness - wanted) < 0.005:
            return floor_type
    for floor_type, thickness in candidates:
        if abs(thickness - wanted) < 0.005:
            return floor_type
    if not candidates:
        raise Exception(u"У проєкті немає типів перекриття.")

    base = candidates[0][0]
    layers = list(base.GetCompoundStructure().GetLayers())
    material_id = layers[0].MaterialId
    new_type = base.Duplicate(SLAB_TYPE_NAME)
    layer = CompoundStructureLayer(wanted, MaterialFunctionAssignment.Structure, material_id)
    new_type.SetCompoundStructure(CompoundStructure.CreateSimpleCompoundStructure(List[CompoundStructureLayer]([layer])))
    return new_type


def _create_floor(document, loops, floor_type, level, mark=SLAB_MARK, offset=0.0):
    """A Floor from `loops` (outer loop first, then holes) on `level`, `offset` feet above it, marked `mark` in its
    Comments: Floor.Create (Revit 2022+), else NewFloor with the outer loop only (older Revit has no holes in it)."""
    from Autodesk.Revit.DB import BuiltInParameter, CurveArray, CurveLoop, Floor
    from System.Collections.Generic import List

    if hasattr(Floor, "Create"):
        curve_loops = List[CurveLoop]()
        for loop in loops:
            curve_loops.Add(loop)
        slab = Floor.Create(document, curve_loops, floor_type.Id, level.Id)
    else:
        curves = CurveArray()
        for curve in loops[0]:
            curves.Append(curve)
        slab = document.Create.NewFloor(curves, floor_type, level, False)
    try:
        height = slab.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
        if height is not None and not height.IsReadOnly:
            height.Set(offset)
    except Exception:
        pass
    try:
        comment = slab.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if comment is not None and not comment.IsReadOnly:
            comment.Set(mark)
    except Exception:
        pass
    return slab


def _outer_area(loops):
    return max(abs(signed_area(_loop_points(loop))) for loop in loops)


def _one_slab(document, pieces, floor_type, level, mark=SLAB_MARK, offset=0.0):
    """ONE Floor for the floor: the loops of all `pieces` (separate parts of the outline, each an outer loop and its
    holes) together. When Revit refuses a floor of several parts, the largest part alone. Returns (slab, how many
    parts were left out)."""
    pieces = sorted(pieces, key=_outer_area, reverse=True)
    if len(pieces) > 1:
        try:
            return _create_floor(document, [loop for loops in pieces for loop in loops], floor_type, level, mark, offset), 0
        except Exception:
            pass
    return _create_floor(document, pieces[0], floor_type, level, mark, offset), len(pieces) - 1


def floor_footprints(document, floor):
    """The outlines of the floor's roofs (the apartment footprints, the МЗК, the stair cores): [[(x, y)]]."""
    import apartment_groups

    picker = apartment_groups._picker()
    picker.doc = document
    footprints = []
    for roof in floor["roofs"]:
        try:
            polygon, _z = picker._bottom_footprint(roof)
        except Exception:
            polygon = None
        if polygon:
            footprints.append([(point[0], point[1]) for point in polygon])
    return footprints


def floor_parts(document, floor):
    """(footprint polygons, protruding walls, balcony room polygons) of the floor `floor` (floor_copy.floors_of_sections):
    every roof of the floor is a footprint; the walls and balcony / loggia rooms are those of the floor's
    apartments and stair cores (the facade insulation walls are left out)."""
    import apartment_groups
    import duplicate_walls
    import placed_apartments
    import room_tag_tidy
    import apartment_group_placement as agp
    from Autodesk.Revit.DB import BuiltInParameter, ElementId, Line, Wall
    from Autodesk.Revit.DB.Architecture import Room

    footprints = floor_footprints(document, floor)

    live =dict((entry["roofId"], entry) for entry in placed_apartments.live_entries(document) if entry.get("roofId") is not None)
    cores = placed_apartments.live_cores(document)
    group_ids = [_id_int(document.GetElement(ElementId(int(live[roof_id]["groupId"]))).Id)
                 for roof_id in floor["roofKeys"] if roof_id in live]
    for roof_id in floor.get("coreKeys", {}):
        group = document.GetElement(cores[roof_id]) if roof_id in cores else None
        if group is not None:
            group_ids.append(_id_int(group.Id))

    walls, rooms = [], []
    for group_id in group_ids:
        group = document.GetElement(ElementId(int(group_id)))
        for member_id in (group.GetMemberIds() if group is not None else []):
            member = document.GetElement(member_id)
            try:
                if isinstance(member, Wall):
                    curve = member.Location.Curve
                    if isinstance(curve, Line) and not duplicate_walls._is_foam_facade_wall(member):
                        a, b = curve.GetEndPoint(0), curve.GetEndPoint(1)
                        walls.append(((a.X, a.Y), (b.X, b.Y), member.Width))
                elif isinstance(member, Room):
                    name = member.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()
                    outline = room_tag_tidy._outline(member) if is_balcony_name(name) else None
                    if outline:
                        rooms.append(outline)
            except Exception:
                continue
    return footprints, walls, rooms


def _top_key(level_id):
    return u"top:{}".format(level_id)


def slabs_of_level(document, level_id, mark=SLAB_MARK, key=None):
    """Our slabs of the level: the remembered ones (under `key`, the level id by default), plus any floor on that
    level marked as ours (`mark` in its Comments) - so a slab the record lost is found and never left as a second
    one."""
    import placed_apartments
    from Autodesk.Revit.DB import BuiltInParameter, FilteredElementCollector, Floor

    found, seen = [], set()
    for uid in placed_apartments.live_slabs(document).get(level_id if key is None else key, []):
        element = document.GetElement(uid)
        if isinstance(element, Floor) and _id_int(element.Id) not in seen:
            seen.add(_id_int(element.Id))
            found.append(element)
    try:
        for element in FilteredElementCollector(document).OfClass(Floor):
            if _id_int(element.Id) in seen or _id_int(element.LevelId) != level_id:
                continue
            comment = element.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if comment is not None and comment.AsString() == mark:
                seen.add(_id_int(element.Id))
                found.append(element)
    except Exception:
        pass
    return found


def _delete_slabs(document, level_id):
    for old in slabs_of_level(document, level_id):
        document.Delete(old.Id)


def make_slab(document, floor):
    """Make (again) the 200 mm slab of the floor `floor` (a laid-out floor: every footprint has its apartment).
    Returns {"done", "message"}."""
    import floor_copy
    import placed_apartments
    from Autodesk.Revit.DB import ElementId

    level = document.GetElement(ElementId(int(floor["levelId"])))
    footprints, walls, rooms = floor_parts(document, floor)
    shapes = shapes_of_the_floor(footprints, walls, rooms)
    if not shapes:
        return {"done": False, "message": u"Перекриття {}: немає плям.".format(floor["levelName"])}
    pieces, skipped = union_outline([grow_polygon(shape, _OVERLAP_FT) for shape in shapes], level.Elevation)
    pieces = [loops for loops in pieces if keep_real_pieces([_outer_area(loops)])]
    if not pieces:
        return {"done": False, "message": u"Перекриття {}: контур не вдалося побудувати.".format(floor["levelName"])}

    uids, left_out = [], 0
    transaction = floor_copy._transaction(document, u"Froglet: перекриття")
    try:
        _delete_slabs(document, int(floor["levelId"]))          # one slab per floor: the old one goes
        slab, left_out = _one_slab(document, pieces, slab_type(document), level)
        uids.append(slab.UniqueId)
        placed_apartments.record_slabs(document, int(floor["levelId"]), uids)
        floor_copy._finish(transaction, True)
    except Exception:
        floor_copy._finish(transaction, False)
        raise
    notes = []
    if skipped:
        notes.append(u"{} фігур не вдалося об'єднати".format(skipped))
    if left_out:
        notes.append(u"{} окремих частин контуру не ввійшло: Revit не прийняв перекриття з кількох частин".format(left_out))
    message = u"Перекриття 200 мм на {}: одне (плям {}, стін {}, балконів/лоджій {}{}).".format(
        floor["levelName"], len(footprints), len(walls), len(rooms), u"; " + u"; ".join(notes) if notes else u"")
    return {"done": True, "message": message}


def copy_slab(document, source_level_id, target_floor):
    """Put the slab of the level `source_level_id` on the floor `target_floor` too (same outline, moved to the
    target level; the target's old slab goes). Call it inside a transaction. Returns how many slabs were made."""
    import placed_apartments
    from Autodesk.Revit.DB import ElementId

    sources = slabs_of_level(document, int(source_level_id))
    if not sources:
        return 0
    target_level = document.GetElement(ElementId(int(target_floor["levelId"])))
    _delete_slabs(document, int(target_floor["levelId"]))          # one slab per floor: the target's old one goes
    source = sources[0]
    pieces = source_loops(source, target_level.Elevation)
    if not pieces:
        return 0
    slab, _left_out = _one_slab(document, pieces, document.GetElement(source.GetTypeId()), target_level)
    placed_apartments.record_slabs(document, int(target_floor["levelId"]), [slab.UniqueId])
    return 1


def _solids(element):
    from Autodesk.Revit.DB import GeometryInstance, Options, Solid

    options = Options()
    for item in element.get_Geometry(options):
        if isinstance(item, Solid) and item.Faces.Size > 0:
            yield item
        elif isinstance(item, GeometryInstance):
            for inner in item.GetInstanceGeometry():
                if isinstance(inner, Solid) and inner.Faces.Size > 0:
                    yield inner


def source_loops(slab, target_elevation):
    """The loops of the bottom face(s) of the slab, one list per part, flattened to `target_elevation`."""
    from Autodesk.Revit.DB import PlanarFace

    pieces = []
    for solid in _solids(slab):
        for face in solid.Faces:
            if isinstance(face, PlanarFace) and face.FaceNormal.Z < -0.99:
                pieces.append([_loop(_loop_points(loop), target_elevation) for loop in face.GetEdgesAsCurveLoops()])
    return pieces


def _level_above(document, level):
    """The next level up in the project, or None."""
    from Autodesk.Revit.DB import FilteredElementCollector, Level

    higher = [other for other in FilteredElementCollector(document).OfClass(Level) if other.Elevation > level.Elevation + 0.1]
    return min(higher, key=lambda other: other.Elevation) if higher else None


def top_storey_height(document, floors):
    """The height (feet) of the building's top storey: the top floor's height above the one below it, else up to the
    next level of the project; None when neither is known."""
    from Autodesk.Revit.DB import ElementId

    top = max(floors, key=lambda floor: floor["z"])
    height = storey_height([floor["z"] for floor in floors])
    if height is None:
        level = document.GetElement(ElementId(int(top["levelId"])))
        above = _level_above(document, level)
        height = above.Elevation - level.Elevation if above is not None else None
    return height if height is not None and height > 0.0 else None


def make_top_slab(document, floors):
    """The slab over the top floor of `floors` (floor_copy.floors_of_sections): the outline of the ROOFS of that
    floor (the apartment footprints, the МЗК, the stair cores - what the building's roof covers), not of what its
    floor slab has: nothing sticks out of the roofs for balconies. It is one storey up - the top floor's height above
    the one below it, else up to the next level of the project. One slab (SLAB_TOP_MARK), made again each time; of
    the type of the floor slabs. The floor slabs of the other floors are the slabs under them, so with this one the
    building has a slab under the first floor, between all floors and over the last. The shaft of a stair core
    ends under it (stair_stack). Returns {"done", "message"}."""
    import floor_copy
    import placed_apartments
    from Autodesk.Revit.DB import ElementId

    if not floors:
        return {"done": False, "message": u""}
    top = max(floors, key=lambda floor: floor["z"])
    level = document.GetElement(ElementId(int(top["levelId"])))

    height = top_storey_height(document, floors)
    if height is None:
        return {"done": False, "message": u"Перекриття над {}: не створено - невідома висота поверху (одного поверху мало, вище немає рівня).".format(top["levelName"])}

    shapes = [shape for shape in (clean_polygon(polygon) for polygon in floor_footprints(document, top)) if shape]
    pieces, _skipped = union_outline([grow_polygon(shape, _OVERLAP_FT) for shape in shapes], level.Elevation) if shapes else ([], 0)
    pieces = [loops for loops in pieces if keep_real_pieces([_outer_area(loops)])]
    if not pieces:
        return {"done": False, "message": u"Перекриття над {}: контур плям даху не вдалося побудувати.".format(top["levelName"])}

    own = slabs_of_level(document, int(top["levelId"]))
    floor_type = document.GetElement(own[0].GetTypeId()) if own else slab_type(document)

    key = _top_key(int(top["levelId"]))
    transaction = floor_copy._transaction(document, u"Froglet: перекриття над верхнім поверхом")
    try:
        for old in slabs_of_level(document, int(top["levelId"]), SLAB_TOP_MARK, key):
            document.Delete(old.Id)
        slab, _left_out = _one_slab(document, pieces, floor_type, level, SLAB_TOP_MARK, height)
        placed_apartments.record_slabs(document, key, [slab.UniqueId])
        floor_copy._finish(transaction, True)
    except Exception:
        floor_copy._finish(transaction, False)
        raise
    return {"done": True, "message": u"Перекриття над верхнім поверхом ({}): на висоті {:.2f} м над рівнем.".format(
        top["levelName"], height * 0.3048)}


def _floors_to_slab(document, uiapp, roof_ids):
    """(chosen floors, entries, all floors): the floors the run is about - the one of the active plan, else those
    of the picked apartments' footprints - {roof id: apartment entry}, and every floor of the sections."""
    import floor_copy

    view = uiapp.ActiveUIDocument.ActiveView
    sections = floor_copy.active_sections(document, view)
    if not sections:
        return None, {}, []
    floors, _originals, entries = floor_copy.laid_out_floors(document, sections)
    level_id = floor_copy.active_level_id(view)
    chosen = [floor for floor in floors if floor["levelId"] == level_id] if level_id is not None else \
        [floor for floor in floors if set(floor["roofKeys"]) & set(roof_ids)]
    return chosen, entries, floors


def run(uiapp, document, roof_ids=()):
    """The step of "Виправлення геометрії...": the 200 mm slab of the floor of the active plan (or of the picked
    apartments' floors), only when the floor is laid out completely. Returns {"label", "done", "message"}."""
    import floor_copy

    label = u"Перекриття"
    floors, entries, all_floors = _floors_to_slab(document, uiapp, roof_ids)
    if floors is None:
        return {"label": label, "done": False, "message": u"Перекриття: відкрийте вид секції, створений Froglet."}
    if not floors:
        return {"label": label, "done": False, "message": u"Перекриття: не знайдено поверх."}
    messages, done = [], False
    for floor in floors:
        if not floor_copy.is_laid_out(floor, entries):
            messages.append(u"Перекриття {}: не створено - поверх розпланований не повністю.".format(floor["levelName"]))
            continue
        result = make_slab(document, floor)
        done = done or result["done"]
        messages.append(result["message"])
    top = max(all_floors, key=lambda floor: floor["z"]) if all_floors else None
    if done and top is not None and any(floor["levelId"] == top["levelId"] for floor in floors):
        messages.append(make_top_slab(document, all_floors)["message"])       # (the top floor's slab is there: the one over it)
    return {"label": label, "done": done, "message": u" ".join(messages)}