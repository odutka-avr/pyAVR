# -*- coding: utf-8 -*-
"""Room tags of the placed apartments that ended up away from their rooms: no leader, on the middle of the room.

The tags come with the apartment as the file's attached "T" detail group (copied, not rebuilt) and are
left as they are. Some end up away from their rooms, with the leader line on. At the end of "Запустити"
every tag of the picked apartments' rooms whose head is NOT inside its room gets its leader turned off
and its head put on the middle of the room; a tag that stands in its room is not touched. The framed
apartment tag ("П_Без площ з коефіцієнтами") is left alone: it stands outside the apartment with a
leader on purpose.
Runs on IronPython 2.7 (pyRevit) and CPython 3 (the tests); only tidy() touches Revit.
"""


def polygon_area(points):
    total = 0.0
    for index in range(len(points)):
        x1, y1 = points[index][0], points[index][1]
        x2, y2 = points[(index + 1) % len(points)][0], points[(index + 1) % len(points)][1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def point_in_polygon(point, points):
    x, y = point
    inside = False
    for index in range(len(points)):
        x1, y1 = points[index][0], points[index][1]
        x2, y2 = points[(index + 1) % len(points)][0], points[(index + 1) % len(points)][1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def room_centre(points, fallback=None):
    """The middle of a room outline: its centroid when that lies inside it, else the point of the outline's
    widest horizontal chord through the middle of its height, else `fallback` (the room's own point)."""
    if len(points) < 3 or abs(polygon_area(points)) < 1e-9:
        return fallback
    area = polygon_area(points)
    cx = cy = 0.0
    for index in range(len(points)):
        x1, y1 = points[index][0], points[index][1]
        x2, y2 = points[(index + 1) % len(points)][0], points[(index + 1) % len(points)][1]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    centroid = (cx / (6.0 * area), cy / (6.0 * area))
    if point_in_polygon(centroid, points):
        return centroid

    # (an L or U shape: the centroid falls outside) - the middle of the longest chord at mid height
    ys = [p[1] for p in points]
    y = (min(ys) + max(ys)) / 2.0
    crossings = []
    for index in range(len(points)):
        x1, y1 = points[index][0], points[index][1]
        x2, y2 = points[(index + 1) % len(points)][0], points[(index + 1) % len(points)][1]
        if (y1 > y) != (y2 > y):
            crossings.append(x1 + (x2 - x1) * (y - y1) / (y2 - y1))
    crossings.sort()
    best = None
    for start, end in zip(crossings[0::2], crossings[1::2]):
        if best is None or end - start > best[1] - best[0]:
            best = (start, end)
    if best is not None:
        return ((best[0] + best[1]) / 2.0, y)
    return fallback


# The families the room tags and the apartment tag of a placed apartment must come from. The apartment files bring their own
# tag families, and some of them were drawn with the project's older ones (...ОднеЗначення2, ...Квартира нова): a type of
# the same name, but another text colour (blue) and no frame on the apartment tag. Such tags are switched to these.
CANONICAL_TAG_FAMILIES = (u"AVR_Марка_Приміщення_ОднеЗначення1", u"AVR_Марка_Приміщення_Квартира2")


def pick_canonical_types(types, families=CANONICAL_TAG_FAMILIES):
    """{type name: type id} of the tag types that belong to the canonical `families`;
    `types` = [(type id, family name, type name)]. The first one of a name wins."""
    chosen = {}
    for type_id, family_name, type_name in types:
        if family_name in families and type_name not in chosen:
            chosen[type_name] = type_id
    return chosen


# ------------------------------------------------------------------ Revit side

def unify_families(document, room_ids, families=CANONICAL_TAG_FAMILIES):
    """Switch every room tag of the rooms `room_ids` whose type has a twin of the same name in a canonical family to
    that twin (nothing when the canonical families are not in the project). Returns how many tags were switched."""
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, Transaction
    from Autodesk.Revit.DB.Architecture import RoomTag
    import apartment_group_placement as agp

    collector = FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_RoomTags)
    types = [(t.Id, getattr(t, "FamilyName", u""), agp._element_name(t)) for t in collector.WhereElementIsElementType()]
    canonical = pick_canonical_types(types, families)
    if not canonical:
        return 0

    wanted = set(room_ids)
    todo = []                                         # (the transaction is only opened when there is something to switch)
    for tag in list(FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_RoomTags).WhereElementIsNotElementType()):
        try:
            if not isinstance(tag, RoomTag):
                continue
            room = tag.Room
            if room is None or room.Id.IntegerValue not in wanted:
                continue
            target = canonical.get(agp._element_name(document.GetElement(tag.GetTypeId())))
            if target is not None and tag.GetTypeId() != target:
                todo.append((tag, target))
        except Exception:
            continue
    if not todo:
        return 0

    own = not document.IsModifiable
    transaction = Transaction(document, u"Room tags: one family") if own else None
    if own:
        transaction.Start()
    switched = 0
    try:
        for tag, target in todo:
            try:
                tag.ChangeTypeId(target)
                switched += 1
            except Exception:
                continue
        if own:
            transaction.Commit()
    except Exception:
        if own and transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    return switched


def unify_placed(document):
    """unify_families for the rooms of every apartment Froglet has placed in the project (run after each placement, so the tags are
    right at once, whatever the apartment's file brought). Returns how many tags were switched."""
    import placed_apartments
    from Autodesk.Revit.DB import ElementId
    from Autodesk.Revit.DB.Architecture import Room

    room_ids = set()
    for entry in placed_apartments.live_entries(document):
        group = document.GetElement(ElementId(int(entry["groupId"])))
        if group is None:
            continue
        for member_id in group.GetMemberIds():
            if isinstance(document.GetElement(member_id), Room):
                room_ids.add(member_id.IntegerValue)
    return unify_families(document, room_ids) if room_ids else 0


def _outline(room):
    """The room's outer boundary as [(x, y)] feet, or None (an unenclosed room has none)."""
    from Autodesk.Revit.DB import SpatialElementBoundaryOptions
    loops = room.GetBoundarySegments(SpatialElementBoundaryOptions())
    best = None
    for loop in loops or []:
        points = []
        for segment in loop:
            point = segment.GetCurve().GetEndPoint(0)
            points.append((point.X, point.Y))
        if len(points) >= 3 and (best is None or abs(polygon_area(points)) > abs(polygon_area(best))):
            best = points
    return best


def tidy(document, room_ids, keep_type_names=()):
    """Turn the leader off and put the head on the middle of the room for every room tag of the rooms
    `room_ids` (element ids), except tags of the types in `keep_type_names`. Returns (tags changed, tags
    skipped because their room has no outline or the tag could not be changed)."""
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, Transaction, XYZ
    from Autodesk.Revit.DB.Architecture import RoomTag
    import apartment_group_placement as agp

    wanted = set(room_ids)
    changed = skipped = 0
    own = not document.IsModifiable
    transaction = Transaction(document, u"Room tags: no leader, in the middle") if own else None
    if own:
        transaction.Start()
    try:
        for tag in FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_RoomTags).WhereElementIsNotElementType():
            try:
                if not isinstance(tag, RoomTag):
                    continue
                room = tag.Room
                if room is None or room.Id.IntegerValue not in wanted:
                    continue
                if agp._element_name(document.GetElement(tag.GetTypeId())) in keep_type_names:
                    continue
                point = room.Location.Point if room.Location is not None else None
                outline = _outline(room) or []
                head = tag.TagHeadPosition
                if len(outline) >= 3 and point_in_polygon((head.X, head.Y), outline):
                    continue        # it stands in its room, as the copied "T" group put it: left as it is
                centre = room_centre(outline, (point.X, point.Y) if point is not None else None)
                if centre is None:
                    skipped += 1
                    continue
                tag.HasLeader = False
                tag.TagHeadPosition = XYZ(centre[0], centre[1], tag.TagHeadPosition.Z)
                changed += 1
            except Exception:
                skipped += 1
        if own:
            transaction.Commit()
    except Exception:
        if own and transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    return changed, skipped
