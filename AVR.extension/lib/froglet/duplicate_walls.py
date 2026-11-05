# -*- coding: utf-8 -*-
"""«Видалення дублюючих міжквартирних стін».

For the picked apartments (model groups):
  1. every picked group is ungrouped and its group type deleted from the
     project browser (a type still used by another instance is kept);
  2. walls of DIFFERENT picked apartments that overlap each other (parallel,
     overlapping along their length, bodies overlapping) are duplicates - one
     wall of each pair is deleted. A wall that carries doors/windows is kept
     in preference; otherwise the one with the higher Id goes;
  3. every apartment is grouped again, as its own group, under its old name.

All in one TransactionGroup: any failure rolls everything back.
"""

import math
import traceback

import apartment_group_placement as agp
import backend_api
import placed_apartments

from Autodesk.Revit.DB import (
    BuiltInCategory, ElementId, ElementTransformUtils, FamilyInstance, Group, Line, Transaction,
    TransactionGroup, TransactionStatus, Transform, Wall, XYZ,
)
from System.Collections.Generic import List

_PARALLEL_SIN = math.sin(math.radians(3.0))


def _log(message):
    agp._log(u"[DUPLICATE WALLS] " + message)


def _id_int(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def _transaction(document, name):
    transaction = Transaction(document, name)
    transaction.Start()
    agp.apply_silent_failure_handling(transaction)
    return transaction


def _commit(transaction):
    status = transaction.Commit()
    if status != TransactionStatus.Committed:
        raise Exception(u"транзакцію не завершено ({})".format(status))


def _wall_record(wall, owner):
    try:
        curve = wall.Location.Curve
    except Exception:
        return None
    if not isinstance(curve, Line):
        return None
    p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
    length = math.hypot(p1.X - p0.X, p1.Y - p0.Y)
    if length < 1e-6:
        return None
    try:
        insert_count = wall.FindInserts(True, True, True, True).Count
    except Exception:
        insert_count = 0
    return {
        "el": wall, "id": _id_int(wall.Id), "owner": owner,
        "p0": (p0.X, p0.Y), "p1": (p1.X, p1.Y), "z": p0.Z, "length": length,
        "dir": ((p1.X - p0.X) / length, (p1.Y - p0.Y) / length),
        "width": wall.Width, "inserts": insert_count > 0, "insert_count": insert_count,
        "dims": len(_dimension_ids_of(wall)),
        # What the planners need to know about the wall's TYPE (so they never touch
        # the Revit element): the foam facade layer and the mineral-wool facade layer.
        "isFoam": _is_foam_facade_wall(wall), "isFacade": _is_facade_wall(wall),
    }


def _dimension_ids_of(wall):
    """Ids of the dimensions that are tied to `wall`."""
    try:
        from Autodesk.Revit.DB import Dimension, ElementClassFilter
        return list(wall.GetDependentElements(ElementClassFilter(Dimension)))
    except Exception:
        return []


def _core_walls(document, entries):
    """Walls of the stair/lift cores (groups recorded as cores, plus any other
    model group whose type name says "Stairs"), read from their groups."""
    group_ids = set()
    try:
        for uid in placed_apartments.live_cores(document).values():
            element = document.GetElement(uid)
            if isinstance(element, Group):
                group_ids.add(_id_int(element.Id))
    except Exception:
        pass

    apartment_ids = set(entries)
    from Autodesk.Revit.DB import FilteredElementCollector
    for group in FilteredElementCollector(document).OfClass(Group):
        try:
            if group.ViewSpecific or _id_int(group.Id) in apartment_ids:
                continue
            if u"stairs" in (agp._element_name(group.GroupType) or u"").lower():
                group_ids.add(_id_int(group.Id))
        except Exception:
            continue

    walls = []
    for group_id in group_ids:
        group = document.GetElement(ElementId(int(group_id)))
        for member_id in group.GetMemberIds():
            element = document.GetElement(member_id)
            if isinstance(element, Wall):
                record = _wall_record(element, u"core")
                if record is not None:
                    walls.append(record)
    return walls


_MIN_PIECE_FT = 0.5            # a stub shorter than this is not worth keeping


def _remaining_intervals(length, cut_intervals):
    """The stretches of a wall of `length` that are left once `cut_intervals`
    are taken off; stubs shorter than _MIN_PIECE_FT are dropped."""
    merged = []
    for low, high in sorted(cut_intervals):
        if merged and low <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))

    pieces, cursor = [], 0.0
    for low, high in merged:
        if low > cursor:
            pieces.append((cursor, low))
        cursor = max(cursor, high)
    if cursor < length:
        pieces.append((cursor, length))
    return [(a, b) for a, b in pieces if b - a >= _MIN_PIECE_FT]


def _keep_only(document, record, pieces, info):
    """Make the wall cover only `pieces` (feet from its start): the first piece
    is the wall itself shortened, every further piece a copy of it."""
    wall = record["el"]
    curve = wall.Location.Curve
    start, end = curve.GetEndPoint(0), curve.GetEndPoint(1)
    span = start.DistanceTo(end)
    unit = XYZ((end.X - start.X) / span, (end.Y - start.Y) / span, (end.Z - start.Z) / span)

    def _segment(a, b):
        return Line.CreateBound(
            XYZ(start.X + unit.X * a, start.Y + unit.Y * a, start.Z + unit.Z * a),
            XYZ(start.X + unit.X * b, start.Y + unit.Y * b, start.Z + unit.Z * b),
        )

    # Copies first, from the untouched wall; then the wall is shortened.
    for a, b in pieces[1:]:
        new_ids = list(ElementTransformUtils.CopyElement(document, wall.Id, XYZ(0.0, 0.0, 0.0)))
        copy = document.GetElement(new_ids[0])
        copy.Location.Curve = _segment(a, b)
        info[record["owner"]]["members"].append(new_ids[0])
    wall.Location.Curve = _segment(*pieces[0])


def _columns_of(document, info):
    """The structural columns among the ungrouped apartments' members, each
    with its plan rectangle."""
    columns = []
    wanted = (int(BuiltInCategory.OST_StructuralColumns), int(BuiltInCategory.OST_Columns))
    for group_id, item in info.items():
        for member_id in item["members"]:
            element = document.GetElement(member_id)
            if not isinstance(element, FamilyInstance) or element.Category is None:
                continue
            if _id_int(element.Category.Id) not in wanted:
                continue
            box = element.get_BoundingBox(None)
            if box is None:
                continue
            columns.append({
                "el": element, "id": _id_int(element.Id), "owner": group_id,
                "rect": (box.Min.X, box.Min.Y, box.Max.X, box.Max.Y),
            })
    return columns


def _reference_side(document, reference):
    """(wall Id, side) of a reference to a plane face of a straight wall - side is
    +1 or -1 depending on which side of the wall's centre line the face is on -
    or None for anything else."""
    try:
        wall = document.GetElement(reference.ElementId)
        if not isinstance(wall, Wall):
            return None
        face = wall.GetGeometryObjectFromReference(reference)
        origin = face.Origin
        curve = wall.Location.Curve
        start, end = curve.GetEndPoint(0), curve.GetEndPoint(1)
        length = math.hypot(end.X - start.X, end.Y - start.Y)
        if length < 1e-9:
            return None
        normal = (-(end.Y - start.Y) / length, (end.X - start.X) / length)
        side = (origin.X - start.X) * normal[0] + (origin.Y - start.Y) * normal[1]
        return (_id_int(wall.Id), 1 if side > 0 else -1)
    except Exception:
        return None


def _face_reference(document, wall_id, side):
    """A reference to the face of the wall on the given side of its centre line."""
    from Autodesk.Revit.DB import HostObjectUtils, ShellLayerType

    wall = document.GetElement(ElementId(int(wall_id)))
    if not isinstance(wall, Wall):
        return None
    for shell in (ShellLayerType.Exterior, ShellLayerType.Interior):
        try:
            for reference in HostObjectUtils.GetSideFaces(wall, shell):
                found = _reference_side(document, reference)
                if found and found[1] == side:
                    return reference
        except Exception:
            continue
    return None


def _snapshot_dimensions(document, info):
    """For every dimension tied to a wall of the ungrouped apartments: its view,
    line, type and, per reference, the wall face (which side of the wall) it
    measures to - so it can be put back on that face after the walls were
    moved/trimmed."""
    from Autodesk.Revit.DB import Dimension

    dim_ids = set()
    for item in info.values():
        for member_id in item["members"]:
            element = document.GetElement(member_id)
            if isinstance(element, Wall):
                for dim_id in _dimension_ids_of(element):
                    dim_ids.add(_id_int(dim_id))

    snapshots = []
    for dim_id in dim_ids:
        dimension = document.GetElement(ElementId(dim_id))
        if not isinstance(dimension, Dimension):
            continue
        try:
            references = dimension.References
            sides = [_reference_side(document, references.get_Item(i)) for i in range(references.Size)]
            if not any(sides):
                continue
            snapshots.append({
                "id": dim_id, "view": dimension.OwnerViewId, "line": dimension.Curve,
                "type": dimension.GetTypeId(), "sides": sides,
            })
        except Exception:
            continue
    return snapshots


def _repair_dimensions(document, snapshots):
    """Put every dimension back on the wall faces it measured to. A dimension
    that Revit re-pointed to another face (a wall was moved, split or deleted) is
    made again with the right faces, in its old view, line and type, and the
    wrong one is deleted. Returns how many were repaired."""
    from Autodesk.Revit.DB import Dimension, ReferenceArray

    repaired = 0
    for snapshot in snapshots:
        try:
            dimension = document.GetElement(ElementId(snapshot["id"]))
            view = document.GetElement(snapshot["view"])
            if view is None:
                continue

            if isinstance(dimension, Dimension):
                current = dimension.References
                flipped = []
                for index, wanted in enumerate(snapshot["sides"]):
                    if wanted is None or index >= current.Size:
                        continue
                    now = _reference_side(document, current.get_Item(index))
                    if now is None or now[0] != wanted[0] or now[1] != wanted[1]:
                        flipped.append(index)
                if not flipped:
                    continue
                references = ReferenceArray()
                usable = True
                for index in range(current.Size):
                    reference = current.get_Item(index)
                    if index in flipped:
                        reference = _face_reference(document, *snapshot["sides"][index])
                        if reference is None:
                            usable = False
                            break
                    references.Append(reference)
            else:
                # Revit removed it with a wall: make it again if every reference
                # can be found.
                references = ReferenceArray()
                usable = all(snapshot["sides"])
                for wanted in snapshot["sides"]:
                    if not usable:
                        break
                    reference = _face_reference(document, *wanted)
                    if reference is None:
                        usable = False
                        break
                    references.Append(reference)

            if not usable:
                continue
            new_dimension = document.Create.NewDimension(view, snapshot["line"], references)
            try:
                dimension_type = document.GetElement(snapshot["type"])
                if dimension_type is not None and new_dimension.DimensionType.Id != dimension_type.Id:
                    new_dimension.DimensionType = dimension_type
            except Exception:
                pass
            if isinstance(dimension, Dimension):
                document.Delete(dimension.Id)
            repaired += 1
        except Exception:
            _log(u"  розмір Id {} не вдалося виправити: {}".format(snapshot["id"], traceback.format_exc()))
    return repaired


def _free_attached_detail_groups(document, group):
    """Ungroup the detail groups attached to `group` (its "D" dimensions and "T"
    tags): their members stay as ordinary elements. Returns how many groups
    were ungrouped."""
    from Autodesk.Revit.DB import FilteredElementCollector

    attached = []
    for candidate in FilteredElementCollector(document).OfClass(Group):
        try:
            if candidate.ViewSpecific and candidate.AttachedParentId == group.Id:
                attached.append(candidate)
        except Exception:
            continue

    freed = 0
    for detail_group in attached:
        try:
            detail_group.UngroupMembers()
            freed += 1
        except Exception:
            _log(u"  прикріплену групу не вдалося розгрупувати: {}".format(traceback.format_exc()))
    if freed:
        _log(u"  прикріплених груп (розміри/позначки) відокремлено: {} - вони залишаться.".format(freed))
    return freed


def _tag_angle_parameter(tag):
    """The tag's rotation parameter ("Angle"), or None."""
    from Autodesk.Revit.DB import BuiltInParameter

    for lookup in (
        lambda: tag.get_Parameter(BuiltInParameter.TAG_ANGLE_PARAM),
        lambda: tag.LookupParameter(u"Angle"),
        lambda: tag.LookupParameter(u"Кут"),
    ):
        try:
            parameter = lookup()
        except Exception:
            parameter = None
        if parameter is not None and not parameter.IsReadOnly:
            return parameter
    return None


def _upright_room_tags(document, info):
    """The room tags of the apartments (the framed apartment tag included) turn
    with the apartment when it was placed rotated, and stay turned once the
    detail groups they sat in are ungrouped - their text then runs sideways.
    Set their Angle to 0, so they read horizontally in the view. Returns how
    many tags were turned back."""
    from Autodesk.Revit.DB import BuiltInParameter, FilteredElementCollector
    from Autodesk.Revit.DB.Architecture import Room, RoomTag

    room_ids = set()
    for item in info.values():
        new_id = item.get("new_group_id")
        group = document.GetElement(new_id) if new_id is not None else None
        if group is None or not group.IsValidObject:
            continue
        for member_id in group.GetMemberIds():
            if isinstance(document.GetElement(member_id), Room):
                room_ids.add(_id_int(member_id))
    if not room_ids:
        return 0

    fixed = checked = without_parameter = 0
    transaction = _transaction(document, u"Upright room tags")
    try:
        for tag in FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_RoomTags).WhereElementIsNotElementType():
            try:
                if not isinstance(tag, RoomTag):
                    continue
                room = tag.Room
                if room is None or _id_int(room.Id) not in room_ids:
                    continue
                checked += 1
                changed = False

                angle = _tag_angle_parameter(tag)
                if angle is None:
                    without_parameter += 1
                elif abs(angle.AsDouble()) > 1e-6:
                    angle.Set(0.0)
                    changed = True

                # A tag turned by a quarter turn can also come out as "Vertical".
                for orientation_id in (BuiltInParameter.ROOM_TAG_ORIENTATION_PARAM, BuiltInParameter.TAG_ORIENTATION_PARAM):
                    try:
                        orientation = tag.get_Parameter(orientation_id)
                        if orientation is not None and not orientation.IsReadOnly and orientation.AsInteger() != 0:
                            orientation.Set(0)
                            changed = True
                    except Exception:
                        pass

                if changed:
                    fixed += 1
            except Exception:
                _log(u"  позначку Id {} вирівняти не вдалося: {}".format(_id_int(tag.Id), traceback.format_exc()))
        _commit(transaction)
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    _log(u"  позначки приміщень: перевірено {}, повернуто горизонтально {}{}.".format(
        checked, fixed, u", без параметра кута {}".format(without_parameter) if without_parameter else u""))
    return fixed


def _unique_type_name(document, group, wanted):
    """Give `group`'s type `wanted`, or wanted + " 2", " 3"... if taken."""
    name = wanted
    for number in range(2, 200):
        try:
            group.GroupType.Name = name
            return name
        except Exception:
            name = u"{} {}".format(wanted, number)
    return None


_SIDE_PARALLEL_SIN = math.sin(math.radians(5.0))


_BEDROOM_MIN_SMALL_FT = 2.9 / 0.3048     # a bedroom (type 1) stays at least 2900 x 3600 mm
_BEDROOM_MIN_BIG_FT = 3.6 / 0.3048
_ENTRY_ROOM_MIN_FT = 1.6 / 0.3048         # the room with the main door stays at least 1600 mm wide
_LOGGIA_MIN_SMALL_FT = 1.5 / 0.3048       # a loggia / balcony stays at least 3000 x 1500 mm
_LOGGIA_MIN_BIG_FT = 3.0 / 0.3048
_KITCHEN_MIN_FT = 2.3 / 0.3048            # "Кухня-їдальня": the room's narrowest part at least 2300 mm
_CORRIDOR_MIN_FT = 1.6 / 0.3048           # "Коридор": no side shorter than 1600 mm
_BATH_MIN_SMALL_FT = 2.1 / 0.3048         # a combined bathroom ("Суміщений санвузол") stays at least 2100 x 2400 mm
_BATH_MIN_BIG_FT = 2.4 / 0.3048


_unfitted = {}                 # footprint (roof) id -> {"name", "gap" (feet, the largest), "walls"}


def reset_unfitted():
    _unfitted.clear()


def unfitted_apartments():
    """[(footprint id, {"name", "gap", "walls"})] of the apartments whose walls could not be put on
    the footprint's side without shrinking a room below its minimum (since reset_unfitted)."""
    return list(_unfitted.items())


_NO_OVERLAP_PUSH_MAX_FT = 3.0    # an item is never pushed further than this (~0.9 m) out of a wall
_BIG_ITEM_FT = 8.0                # items longer than this (~2.4 m) are not checked


def _loose_items(document, info):
    """[(owner, element, bounding box)]: furniture, casework, generic models and
    plumbing fixtures of the apartments that are not hosted on anything."""
    wanted = (
        int(BuiltInCategory.OST_Furniture), int(BuiltInCategory.OST_GenericModel),
        int(BuiltInCategory.OST_PlumbingFixtures), int(BuiltInCategory.OST_Casework),
    )
    result = []
    for group_id, item in info.items():
        for member_id in item["members"]:
            element = document.GetElement(member_id)
            if not isinstance(element, FamilyInstance) or element.Category is None:
                continue
            if _id_int(element.Category.Id) not in wanted:
                continue
            try:
                if element.Host is not None:
                    continue
            except Exception:
                pass
            result.append((group_id, element))
    return result


def _wall_overlap(rect, wall):
    """(push towards +normal, push towards -normal) that takes the plan rectangle
    out of the wall's body, or None when they do not overlap."""
    min_x, min_y, max_x, max_y = rect
    d = wall["dir"]
    n = (-d[1], d[0])
    p0 = wall["p0"]
    along, across = [], []
    for x in (min_x, max_x):
        for y in (min_y, max_y):
            rel_x, rel_y = x - p0[0], y - p0[1]
            along.append(rel_x * d[0] + rel_y * d[1])
            across.append(rel_x * n[0] + rel_y * n[1])
    half = wall["width"] / 2.0
    if min(max(along), wall["length"]) - max(min(along), 0.0) < _FOLLOW_OVERLAP_FT:
        return None
    if min(across) >= half - 0.02 or max(across) <= -half + 0.02:
        return None
    return half - min(across), max(across) + half


def _overlap_pairs(items, records):
    """{(item id, wall id)} of the items whose plan box is inside a wall body."""
    pairs = set()
    for owner, element in items:
        box = element.get_BoundingBox(None)
        if box is None:
            continue
        rect = (box.Min.X, box.Min.Y, box.Max.X, box.Max.Y)
        for record in records.get(owner, []):
            if _wall_overlap(rect, record) is not None:
                pairs.add((_id_int(element.Id), record["id"]))
    return pairs


def _clear_items_from_walls(document, items, records, before):
    """After the walls were moved/stretched: an item that now stands IN a wall it
    was not in before is pushed out of it, across the wall, to the side its centre
    is on. (Covers the walls that were stretched, trimmed or pushed by others.)"""
    document.Regenerate()
    fresh = {}
    for owner, owner_records in records.items():
        for record in owner_records:
            new = _wall_record(record["el"], owner)
            if new is not None:
                fresh.setdefault(owner, []).append(new)

    pushed = 0
    for owner, element in items:
        box = element.get_BoundingBox(None)
        if box is None:
            continue
        rect = (box.Min.X, box.Min.Y, box.Max.X, box.Max.Y)
        if max(rect[2] - rect[0], rect[3] - rect[1]) > _BIG_ITEM_FT:
            continue        # a large block (a whole kitchen zone...), not a piece of furniture

        # One push per item: out of the wall that needs the smallest move, then
        # looked at again (a second wall may still be in the way).
        for _attempt in range(2):
            best = None
            for record in fresh.get(owner, []):
                if (_id_int(element.Id), record["id"]) in before:
                    continue
                overlap = _wall_overlap(rect, record)
                if overlap is None:
                    continue
                d = record["dir"]
                n = (-d[1], d[0])
                centre = ((rect[0] + rect[2]) / 2.0 - record["p0"][0]) * n[0] + ((rect[1] + rect[3]) / 2.0 - record["p0"][1]) * n[1]
                push = (overlap[0] + _CLEARANCE_FT) if centre >= 0 else -(overlap[1] + _CLEARANCE_FT)
                if best is None or abs(push) < abs(best[0]):
                    best = (push, n, record)
            if best is None:
                break
            push, n, record = best
            if abs(push) > _NO_OVERLAP_PUSH_MAX_FT:
                _log(u"  елемент Id {} у стіні Id {}, але виштовхнути його надто далеко ({:.2f} м) - залишено.".format(
                    _id_int(element.Id), record["id"], abs(push) * agp.FT_TO_M))
                break
            try:
                ElementTransformUtils.MoveElement(document, element.Id, XYZ(n[0] * push, n[1] * push, 0.0))
                pushed += 1
                _log(u"  елемент Id {} «{}» опинився у стіні Id {} - виштовхнуто на {:.2f} м.".format(
                    _id_int(element.Id), agp._element_name(element), record["id"], abs(push) * agp.FT_TO_M))
                box = element.get_BoundingBox(None)
                rect = (box.Min.X, box.Min.Y, box.Max.X, box.Max.Y)
            except Exception:
                _log(u"  елемент Id {} виштовхнути зі стіни не вдалося: {}".format(
                    _id_int(element.Id), traceback.format_exc()))
                break
    return pushed


# Insulation layers that go when they stand against an inside wall: any wall type with one of these in
# its name (AVR_Фасад_Пінопласт150_ШтукатуркаБіла20_170, AVR_Фасад_Мінвата150_Штукатурка20_170 2, ...)
_FOAM_WORDS = (u"пінопласт", u"мінвата")


def _wall_type_name(wall):
    """The wall type's name. WallType.Name is invisible to IronPython's attribute lookup (it raises
    AttributeError) - agp._element_name goes through the base descriptor. Reading it the plain way
    made every wall look like "not foam" and "not a facade layer" in Revit."""
    try:
        return agp._element_name(wall.WallType) or u""
    except Exception:
        return u""


def _is_foam_facade_wall(wall):
    name = _wall_type_name(wall).lower()
    return any(word in name for word in _FOAM_WORDS)


def _align_internal_walls(document, info, entries):
    """Walls as thick as the apartment's door wall go to the closest inside
    side of its footprint, centre line on the side; walls that ended on a moved
    wall are stretched with it. In a Рядове + Присутня apartment a wall on an
    inside side can be two walls one next to the other: the outer one (towards
    the neighbour) is deleted and the inner one goes onto the side by its
    centre line. Returns (how many walls were moved, how many deleted)."""
    return (_align_internal_walls_remote(document, info, entries))


_EXTERIOR_REACH_FT = 5.0 / 0.3048      # an exterior wall is looked for this far (~5 m) in and out of the side's line:


def _align_external_walls(document, info, entries):
    """Every exterior wall is two walls next to each other. The line BETWEEN them
    goes onto the footprint's outside side, so the outer wall (the facade layer)
    ends up beyond the side and the inner one inside it. Both walls of a pair
    move together; the walls that ended on them are stretched, and the rooms keep
    their minimum sizes (see _plan_with_room_minimums). Returns how many walls
    were moved."""
    return (_align_external_walls_remote(document, info, entries))


_FACADE_WALL_TYPE = u"Фасад_Мінвата150_Штукатурка20_170"   # AVR_Фасад_Мінвата150_Штукатурка20_170 2
_PARALLEL_SIN_10 = math.sin(math.radians(10.0))


def _is_facade_wall(wall):
    return _FACADE_WALL_TYPE in _wall_type_name(wall)


_FOLLOW_OVERLAP_FT = 0.3       # ... and alongside it at least this much


_CLEARANCE_FT = 0.05     # a pushed item is left this far from the wall's new face


# ---------------------------------------------------------------- the backend's part
#
# What the three steps below DECIDE - which walls move and how far while every room keeps
# its minimum size, which partitions follow, which walls are cut, which columns are in the
# way - is decided by the backend (backend/froglet_backend/core/walls.py, the planners of
# this file copied verbatim). The plugin reads the model into a snapshot (records without
# the Revit element, member boxes, roof outlines), sends it, and carries out the plan it
# gets back in the same transactions as before.

_RECORD_KEYS = (
    "id", "owner", "p0", "p1", "z", "length", "dir", "width", "inserts", "insert_count", "dims",
    "isFoam", "isFacade",
)


def _record_payload(record):
    """A wall record as the backend takes it: everything but the Revit element."""
    return dict(
        (key, [record[key][0], record[key][1]] if key in ("p0", "p1", "dir") else record[key])
        for key in _RECORD_KEYS
    )


def _rect_of(element):
    box = element.get_BoundingBox(None)
    if box is None:
        return None
    return [box.Min.X, box.Min.Y, box.Max.X, box.Max.Y]


def _members_payload(document, item, record_ids):
    """The members of an ungrouped apartment that can follow a moving wall (see
    _items_to_follow): its walls with a record, and the furniture / generic models /
    fixtures / casework / columns, in the order of the apartment's members."""
    wanted = (
        int(BuiltInCategory.OST_Furniture), int(BuiltInCategory.OST_GenericModel),
        int(BuiltInCategory.OST_PlumbingFixtures), int(BuiltInCategory.OST_Casework),
        int(BuiltInCategory.OST_StructuralColumns), int(BuiltInCategory.OST_Columns),
    )
    column_categories = (int(BuiltInCategory.OST_StructuralColumns), int(BuiltInCategory.OST_Columns))
    members = []
    for member_id in item["members"]:
        element = document.GetElement(member_id)
        if isinstance(element, Wall):
            wall_id = _id_int(element.Id)
            if wall_id in record_ids:
                members.append({"id": wall_id, "kind": "wall", "rect": _rect_of(element)})
            continue
        if not isinstance(element, FamilyInstance) or element.Category is None:
            continue
        category_id = _id_int(element.Category.Id)
        if category_id not in wanted:
            continue
        host_id = None
        try:
            if element.Host is not None:
                host_id = _id_int(element.Host.Id)
        except Exception:
            pass
        members.append({
            "id": _id_int(element.Id), "kind": "item", "rect": _rect_of(element),
            "isColumn": category_id in column_categories, "hostId": host_id, "name": agp._element_name(element),
        })
    return members


def _alignment_request(document, info, entries):
    """(request, all wall records in order, records by id) for /walls/align-*: the
    apartments in the order of `info`, their walls, members, saved rooms and footprint
    outline, and the roofs of their levels (for the footprint's sides)."""
    import apartment_groups
    import stair_core

    picker = apartment_groups._picker()
    picker.doc = document
    roofs_by_level = {}
    apartments, all_records, records_by_id = [], [], {}
    for group_id, item in info.items():
        records = []
        for member_id in item["members"]:
            element = document.GetElement(member_id)
            if isinstance(element, Wall):
                record = _wall_record(element, group_id)
                if record is not None:
                    records.append(record)
        all_records.extend(records)
        for record in records:
            records_by_id[record["id"]] = record

        entry = entries[group_id]
        roof_id = entry.get("roofId")
        roof_level_id, roof_polygon = None, None
        if roof_id is not None:
            roof = document.GetElement(ElementId(int(roof_id)))
            if roof is not None:
                level_id = picker._roof_level_id(roof)
                polygon, _z = picker._bottom_footprint(roof)
                if level_id is not None and polygon:
                    roof_level_id, roof_polygon = level_id, [[p[0], p[1]] for p in polygon]
                    if level_id not in roofs_by_level:
                        roofs_by_level[level_id] = stair_core._collect_roofs(document, picker, level_id)

        apartments.append({
            "groupId": group_id, "name": item["name"], "doorWidth": item.get("door_width"),
            "roofId": roof_id, "roofLevelId": roof_level_id, "roofPolygon": roof_polygon,
            "rvtPath": entry.get("rvtPath"), "entryName": entry.get("name"),
            "rooms": [
                {"p": [[p[0], p[1]] for p in room["p"]], "t": room.get("t"), "e": room.get("e"), "n": room.get("n")}
                for room in (entry.get("rooms") or [])
            ],
            "records": [_record_payload(record) for record in records],
            "members": _members_payload(document, item, set(record["id"] for record in records)),
        })

    request = {
        "apartments": apartments,
        "entryOrder": [group_id for group_id in entries if group_id in info],
        "roofsByLevel": [
            {"levelId": level_id, "roofs": [
                {"id": rid, "type": rtype, "polygon": [[p[0], p[1]] for p in polygon]}
                for rid, rtype, polygon, _rz in roofs
            ]}
            for level_id, roofs in roofs_by_level.items()
        ],
    }
    return request, all_records, records_by_id


def _execute_alignment(document, info, entries, answer, all_records, records_by_id, transaction_name, reason):
    """Carry out the plan of /walls/align-*: cut or delete the outer walls of double walls,
    move the walls, stretch the walls that ended on them, take the furniture and columns
    along, update the saved rooms and clear items out of walls - the second half of
    _apply_wall_plans. Returns (how many walls were moved, how many deleted)."""
    for note in answer["notes"]:
        _log(note)

    deleted_ids = set(answer["deletedIds"])
    if deleted_ids:
        all_records = [record for record in all_records if record["id"] not in deleted_ids]
        for item in info.values():
            item["members"] = [member_id for member_id in item["members"] if _id_int(member_id) not in deleted_ids]
    records_by_owner = {}
    for record in all_records:
        records_by_owner.setdefault(record["owner"], []).append(record)
    moved = 0

    # Which items stand in a wall already (shafts, built-in fixtures): those are
    # left alone by the final "nothing may end up in a wall" check.
    loose_items = _loose_items(document, info)
    items_before = _overlap_pairs(loose_items, records_by_owner)

    transaction = _transaction(document, transaction_name)
    removed = 0
    try:
        for cut in answer["doomed"]:
            wall_id = cut["wallId"]
            record = records_by_id[wall_id]
            pieces = [tuple(piece) for piece in cut["pieces"]]
            try:
                if pieces:
                    _keep_only(document, record, pieces, info)
                    _log(u"  стіну Id {}: видалено частину поруч з внутрішньою; залишено {} шт. ({}).".format(
                        wall_id, len(pieces),
                        u", ".join(u"{:.2f}–{:.2f} м".format(a * agp.FT_TO_M, b * agp.FT_TO_M) for a, b in pieces)))
                else:
                    document.Delete(record["el"].Id)
                    _log(u"  видалено зовнішню стіну Id {} (стоїть поруч з внутрішньою).".format(wall_id))
                removed += 1
            except Exception:
                _log(u"  стіну Id {} видалити не вдалося: {}".format(wall_id, traceback.format_exc()))

        for move in answer["moves"]:
            record = records_by_id[move["wallId"]]
            dx, dy = move["shift"]
            try:
                curve = record["el"].Location.Curve
                record["el"].Location.Curve = curve.CreateTransformed(Transform.CreateTranslation(XYZ(dx, dy, 0.0)))
            except Exception:
                _log(u"  стіну Id {} перемістити не вдалося: {}".format(record["id"], traceback.format_exc()))
                continue
            moved += 1
            _log(u"  стіна Id {} -> {}: зсув ({:+.3f}, {:+.3f}) фт.".format(record["id"], reason, dx, dy))

            # Walls running across it that ended on it follow (the backend says which
            # end): that end goes with it, so the wall gets longer or shorter.
            for stretch in move["stretches"]:
                other = records_by_id[stretch["wallId"]]
                try:
                    curve = other["el"].Location.Curve
                    start, end = curve.GetEndPoint(0), curve.GetEndPoint(1)
                    before = start.DistanceTo(end)
                    if stretch["end"] == 0:
                        start = XYZ(start.X + dx, start.Y + dy, start.Z)
                    else:
                        end = XYZ(end.X + dx, end.Y + dy, end.Z)
                    other["el"].Location.Curve = Line.CreateBound(start, end)
                    _log(u"  стіна Id {} {} на {:.2f} м разом зі стіною Id {}{}.".format(
                        other["id"], u"подовжена" if start.DistanceTo(end) > before else u"скорочена",
                        abs(start.DistanceTo(end) - before) * agp.FT_TO_M, record["id"],
                        u" (стіна з вікнами)" if other["inserts"] else u""))
                except Exception:
                    _log(u"  стіну Id {} подовжити не вдалося: {}".format(other["id"], traceback.format_exc()))

        for shift in answer["itemShifts"]:
            dx, dy = shift["shift"]
            try:
                ElementTransformUtils.MoveElement(document, ElementId(int(shift["id"])), XYZ(dx, dy, 0.0))
            except Exception:
                _log(u"  елемент Id {} перемістити не вдалося: {}".format(shift["id"], traceback.format_exc()))
        if answer["itemShifts"]:
            _log(u"  разом зі стінами переміщено меблів/загальних моделей/сантехніки: {}.".format(len(answer["itemShifts"])))
        if moved or removed:
            # The rooms saved with every apartment (used for the room minimums on the
            # next run) follow the walls that moved.
            for saved in answer["rooms"]:
                local_rooms = (entries.get(saved["groupId"]) or {}).get("rooms") or []
                for local_room, new_room in zip(local_rooms, saved["rooms"]):
                    for point, new_point in zip(local_room["p"], new_room["p"]):
                        point[0], point[1] = new_point[0], new_point[1]
            for note in answer["afterMoveNotes"]:
                _log(note)
            try:
                _clear_items_from_walls(document, loose_items, records_by_owner, items_before)
            except Exception:
                _log(u"  перевірка меблів у стінах не вдалася: {}".format(traceback.format_exc()))
        _commit(transaction)
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    return moved, removed


def _align_internal_walls_remote(document, info, entries):
    """_align_internal_walls with the backend's plan: (moved, removed double walls)."""
    request, all_records, records_by_id = _alignment_request(document, info, entries)
    answer = backend_api.walls_align_internal(request)
    return _execute_alignment(
        document, info, entries, answer, all_records, records_by_id,
        u"Align internal walls", u"внутрішня сторона")


def _align_external_walls_remote(document, info, entries):
    """_align_external_walls with the backend's plan: how many walls were moved."""
    request, all_records, records_by_id = _alignment_request(document, info, entries)
    answer = backend_api.walls_align_external(request)
    if answer.get("noPlans"):
        for note in answer["notes"]:
            _log(note)
        return 0
    moved, _removed = _execute_alignment(
        document, info, entries, answer, all_records, records_by_id,
        u"Align external walls", u"зовнішня сторона")
    return moved


def _dedupe_remote(document, info, walls, core_walls):
    """The backend's answer for step 2 of run(), in the shapes the code after it uses:
    {"cuts": {wall id: {"record", "intervals"}}, "columns": [column records], "blockers": {column id: why}}."""
    columns = _columns_of(document, info)
    answer = backend_api.walls_dedupe({
        "walls": [_record_payload(wall) for wall in walls],
        "coreWalls": [_record_payload(wall) for wall in core_walls],
        "columns": [{"id": c["id"], "owner": c["owner"], "rect": list(c["rect"])} for c in columns],
    })
    for note in answer["notes"]:
        _log(note)
    by_id = dict((wall["id"], wall) for wall in walls)
    return {
        "cuts": dict(
            (cut["wallId"], {"record": by_id[cut["wallId"]], "intervals": [tuple(i) for i in cut["intervals"]]})
            for cut in answer["cuts"]
        ),
        "columns": columns,
        "blockers": dict((item["id"], item["blocker"]) for item in answer["columns"]),
    }


def run(uiapp, document, group_ids, align=False, dedupe=True, external=False):
    """Ungroup the picked apartments, optionally `align` their internal walls
    to the footprint's inside sides and/or `dedupe` overlapping walls, then
    group every apartment again under its old name."""
    parts = []
    if align:
        parts.append(u"Вирівнювання внутрішніх стін")
    if external:
        parts.append(u"Вирівнювання зовнішніх стін")
    if dedupe:
        parts.append(u"Видалення дублюючих міжквартирних стін")
    label = u" + ".join(parts) or u"Редагування квартир"

    def _result(done, message):
        return {"label": label, "done": done, "message": u"«{}»: {}".format(label, message)}

    entries = dict((e["groupId"], e) for e in placed_apartments.live_entries(document))
    picked = [gid for gid in group_ids if gid in entries]
    if not picked:
        return _result(False, u"не обрано жодної квартири.")

    # Stair/lift cores are only READ (never ungrouped): their walls win over
    # the walls of the apartments.
    core_walls = _core_walls(document, entries) if dedupe else []
    if dedupe and not align and not external and len(picked) < 2 and not core_walls:
        return _result(False, u"потрібно обрати щонайменше дві квартири.")

    tg = TransactionGroup(document, u"Remove duplicate walls")
    tg.Start()
    try:
        # 1. Remember, ungroup, delete the old group types.
        info = {}   # old group id -> dict(name, type, member ids)
        transaction = _transaction(document, u"Ungroup apartments")
        try:
            for group_id in picked:
                group = document.GetElement(ElementId(int(group_id)))
                if group is None or not isinstance(group, Group):
                    continue
                group_type = group.GroupType

                # Thickness of the entrance (door) wall - read while the
                # group is still a group.
                door_width = None
                if align:
                    try:
                        door_wall = agp.find_entrance_wall(group) or agp._thickest_wall_door_ft(group)
                        door_width = door_wall.Width if door_wall is not None else None
                    except Exception:
                        door_width = None

                # The dimensions and tags are attached to the group and Revit drops
                # them when it is ungrouped. So the attached detail groups are
                # ungrouped FIRST: their dimensions and tags become ordinary
                # elements that stay (still tied to the walls and rooms). Only if
                # that is not possible, they are made again afterwards from the
                # data saved at placement.
                rebuild = agp.take_rebuild_data(group.UniqueId)
                if _free_attached_detail_groups(document, group):
                    rebuild = None

                info[group_id] = {
                    "name": agp._element_name(group_type),
                    "type": group_type,
                    "door_width": door_width,
                    "rebuild": rebuild,
                    "members": list(group.UngroupMembers()),
                }
            _commit(transaction)
        except Exception:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
            raise

        deleted_types = 0
        transaction = _transaction(document, u"Delete apartment group types")
        try:
            done_types = set()
            for group_id, item in info.items():
                type_id = _id_int(item["type"].Id)
                if type_id in done_types:
                    continue
                done_types.add(type_id)
                try:
                    if item["type"].Groups.Size == 0:
                        document.Delete(item["type"].Id)
                        deleted_types += 1
                except Exception:
                    _log(u"  не вдалося видалити тип групи «{}»: {}".format(item["name"], traceback.format_exc()))
            _commit(transaction)
        except Exception:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
            raise

        # Which wall face every dimension measures to, before any wall is touched.
        dimension_snapshots = []
        try:
            dimension_snapshots = _snapshot_dimensions(document, info)
        except Exception:
            _log(u"  розміри не вдалося запам'ятати: {}".format(traceback.format_exc()))

        # 1b. Internal walls onto the footprints' inside sides.
        moved_walls = 0
        removed_double_walls = 0
        if align:
            moved_walls, removed_double_walls = _align_internal_walls(document, info, entries)

        # 1c. Exterior walls - after the inside walls and the stair/lift core (those
        # steps ran before this one), BEFORE the duplicate walls are removed, which
        # always comes last: the line between the two walls of each exterior wall
        # goes onto the footprint's outside side.
        moved_external = 0
        if external:
            moved_external = _align_external_walls(document, info, entries)

        # 2. Walls of different apartments that overlap.
        walls = []
        if dedupe:
            for group_id, item in info.items():
                for member_id in item["members"]:
                    element = document.GetElement(member_id)
                    if isinstance(element, Wall):
                        record = _wall_record(element, group_id)
                        if record is not None:
                            walls.append(record)

        # What has to go off each wall: {wall id: {"record", "intervals"}}, the
        # intervals being stretches (feet from the wall's start) that lie on
        # another wall. A wall that is only partly covered loses just that part.
        cuts = {}

        def _remaining(cut):
            return _remaining_intervals(cut["record"]["length"], cut["intervals"])


        # The backend decides what goes off each wall (and which columns are in the way).
        remote_dedupe = _dedupe_remote(document, info, walls, core_walls) if dedupe else None
        if remote_dedupe is not None:
            cuts.update(remote_dedupe["cuts"])

        deleted_walls = 0
        trimmed_walls = 0
        deleted_columns = 0
        transaction = _transaction(document, u"Delete duplicate walls")
        try:
            for wall_id, cut in cuts.items():
                record = cut["record"]
                remaining = _remaining(cut)
                if not remaining:
                    try:
                        document.Delete(record["el"].Id)
                        deleted_walls += 1
                        _log(u"  видалено стіну Id {} (лежить на стіні іншої квартири).".format(wall_id))
                    except Exception:
                        _log(u"  стіну Id {} видалити не вдалося: {}".format(wall_id, traceback.format_exc()))
                    continue

                try:
                    _keep_only(document, record, remaining, info)
                    trimmed_walls += 1
                    _log(u"  стіну Id {}: видалено частину, що лежить на іншій стіні; залишено {} шт. ({}).".format(
                        wall_id, len(remaining),
                        u", ".join(u"{:.2f}–{:.2f} м".format(a * agp.FT_TO_M, b * agp.FT_TO_M) for a, b in remaining)))
                except Exception:
                    _log(u"  стіну Id {} обрізати не вдалося: {}".format(wall_id, traceback.format_exc()))

            # Structural columns in the way: any column of an apartment that
            # stands in a wall of another apartment (or of a stair core), or on
            # another apartment's column.
            columns = remote_dedupe["columns"] if remote_dedupe is not None else []
            deleted_column_ids = set()
            for column in columns:
                blocker = remote_dedupe["blockers"].get(column["id"])
                if blocker is None:
                    continue
                try:
                    document.Delete(column["el"].Id)
                    deleted_column_ids.add(column["id"])
                    deleted_columns += 1
                    _log(u"  видалено колону Id {} ({} заважає).".format(column["id"], blocker))
                except Exception:
                    _log(u"  колону Id {} видалити не вдалося: {}".format(column["id"], traceback.format_exc()))
            _commit(transaction)
        except Exception:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
            raise
        # Dimensions that Revit re-pointed to the other face of a wall (or lost
        # with a deleted wall) are put back on the face they measured to.
        repaired_dims = 0
        if dimension_snapshots:
            transaction = _transaction(document, u"Repair dimensions")
            try:
                repaired_dims = _repair_dimensions(document, dimension_snapshots)
                _commit(transaction)
            except Exception:
                if transaction.HasStarted() and not transaction.HasEnded():
                    transaction.RollBack()
                _log(u"  розміри не виправлено: {}".format(traceback.format_exc()))
            if repaired_dims:
                _log(u"  виправлено розмірів, що змінили бік стіни: {}".format(repaired_dims))

        # 3. Group every apartment again under its old name.
        regrouped = 0
        transaction = _transaction(document, u"Regroup apartments")
        try:
            for group_id, item in info.items():
                ids = List[ElementId]()
                for member_id in item["members"]:
                    element = document.GetElement(member_id)
                    if element is None or not element.IsValidObject:
                        continue
                    try:
                        if element.ViewSpecific:
                            continue
                    except Exception:
                        pass
                    ids.Add(member_id)
                if ids.Count == 0:
                    continue

                new_group = document.Create.NewGroup(ids)
                _unique_type_name(document, new_group, item["name"])

                entry = entries[group_id]
                placed_apartments.record(
                    document, new_group, entry.get("roofId"), entry.get("name"), entry.get("rvtPath"),
                    rooms=entry.get("rooms"), z=entry.get("z"), pending_tags=entry.get("pendingTags"),
                    origin=entry.get("origin"))
                item["new_group_id"] = new_group.Id
                if item.get("rebuild"):
                    agp.keep_rebuild_data(new_group.UniqueId, item["rebuild"])
                regrouped += 1
            _commit(transaction)
        except Exception:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
            raise

        # The dimensions and tags that were attached to the apartments are made
        # again, tied to the walls and rooms as they are now.
        dims_made, tags_rebuilt = 0, 0
        for group_id, item in info.items():
            if not item.get("rebuild") or item.get("new_group_id") is None:
                continue
            try:
                new_group = document.GetElement(item["new_group_id"])
                made_dims, made_tags, waiting = agp.rebuild_annotations(document, new_group, item["rebuild"])
                dims_made += made_dims
                tags_rebuilt += made_tags
                if waiting:
                    # Rooms not enclosed yet: kept with the apartment for later.
                    pending = {
                        "metas": waiting, "rotation": item["rebuild"]["tag_rotation"],
                        "mirror": list(item["rebuild"]["tag_mirror"]) if item["rebuild"]["tag_mirror"] else None,
                        "levelId": item["rebuild"]["level_id"],
                    }
                    placed_apartments.set_pending_tags(document, new_group.UniqueId, pending)
            except Exception:
                _log(u"  розмірні лінії/позначки не відновлено: {}".format(traceback.format_exc()))

        # With the overlapping walls gone the rooms are enclosed: the room tags
        # (and the apartment tag) that had to wait can be made now.
        tags_made = 0
        try:
            tags_made = agp.flush_pending_room_tags(document)
        except Exception:
            _log(u"  позначки приміщень не створено: {}".format(traceback.format_exc()))

        # The room tags turned with the apartments: back to horizontal.
        tags_upright = 0
        try:
            tags_upright = _upright_room_tags(document, info)
        except Exception:
            _log(u"  позначки приміщень не вирівняно: {}".format(traceback.format_exc()))

        tg.Assimilate()
        done = [u"розгруповано {}".format(len(info))]
        if tags_upright:
            done.append(u"позначок приміщень повернуто горизонтально {}".format(tags_upright))
        if dims_made or tags_rebuilt:
            done.append(u"відновлено розмірів {} і позначок {}".format(dims_made, tags_rebuilt))
        if tags_made:
            done.append(u"створено позначок приміщень {}".format(tags_made))
        if align:
            done.append(u"переміщено стін {}".format(moved_walls))
        if external:
            done.append(u"переміщено зовнішніх стін {}".format(moved_external))
            if removed_double_walls:
                done.append(u"видалено зовнішніх стін подвійних стін {}".format(removed_double_walls))
        if dedupe:
            done.append(u"видалено дублюючих стін {}, частково обрізано {}, видалено колон {}".format(deleted_walls, trimmed_walls, deleted_columns))
        done.append(u"згруповано знову {}".format(regrouped))
        return _result(True, u", ".join(done) + u".")
    except Exception as error:
        try:
            tg.RollBack()
        except Exception:
            pass
        _log(u"[ERROR] {}".format(traceback.format_exc()))
        return _result(False, u"не вдалося ({}); зміни скасовано.".format(error))
