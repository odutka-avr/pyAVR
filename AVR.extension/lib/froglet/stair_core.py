# -*- coding: utf-8 -*-
"""«Розташування сходово-ліфтового вузла»: copies the stair/lift core from the
file in the Stairs folder next to the picked apartments.

Rules (first version):
  * The core's geometry is the model group of the Stairs file (or, when the
    file has no group, all its model elements).
  * In the file, the wall that carries a door is the core's entrance wall.
    The wall parallel to it that is FURTHEST from it is the core's outer wall.
  * For every "сходово-ліфтовий вузол" roof near a picked apartment, the core is turned (and
    moved) so that the OUTSIDE face of that outer wall lies on an OUTSIDE side
    of the МЗК footprint (a side no other roof touches), centred on it. Of the
    МЗК footprint's outside sides, the one where the core covers the footprint
    best is used - that is the "rotate if needed to cover the footprint".
  * One core per МЗК roof; a roof that already got one (and the core still
    exists) is skipped.
"""

import glob
import math
import os
import re
import traceback

import apartment_group_placement as agp
import apartment_groups
import backend_api
import froglet_settings
import placed_apartments
import stair_stack

from Autodesk.Revit.DB import (
    BuiltInCategory, CategoryType, CopyPasteOptions, ElementId, ElementTransformUtils,
    FamilyInstance, FilteredElementCollector, Group, Line, Transaction, TransactionStatus,
    Transform, Wall, XYZ,
)
from System.Collections.Generic import List

# Roof types (by name) of the stair/lift node: "сходово-ліфтовий вузол".
_CORE_TYPE_WORDS = (u"сходов", u"ліфт", u"лифт")
_PARALLEL_SIN = math.sin(math.radians(10.0))
_Z_TOLERANCE_FT = 1.0


def _log(message):
    agp._log(u"[STAIR CORE] " + message)


def _id_int(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def _is_core_type(type_name):
    name = (type_name or u"").lower()
    return any(word in name for word in _CORE_TYPE_WORDS)


def stairs_files():
    """[(number, path)] of the files in the Stairs folder (`stairs_folder` in settings.txt). The number is the
    LAST number in the name that is not inside brackets: "Stairs CK1_5200(1400)"
    -> 5200 (the footprint's width between its inside sides, in mm)."""
    result = []
    for path in sorted(glob.glob(os.path.join(froglet_settings.get("stairs_folder"), u"*.rvt"))):
        name = os.path.splitext(os.path.basename(path))[0]
        name = re.sub(r"\([^)]*\)", u"", name)
        numbers = re.findall(r"\d+", name)
        if numbers:
            result.append((int(numbers[-1]), path))
    return result

# ---------------------------------------------------------------- geometry

def _point_in_polygon(point, polygon):
    return agp._point_in_polygon_ft(point, polygon)


def _dist_point_segment(point, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return math.hypot(point[0] - ax, point[1] - ay)
    t = max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / length_sq))
    return math.hypot(point[0] - (ax + t * dx), point[1] - (ay + t * dy))


def _polygon_gap(first, second):
    """Smallest vertex-to-edge distance between two polygons (0 if they touch)."""
    best = 1e18
    for polygon_a, polygon_b in ((first, second), (second, first)):
        count = len(polygon_b)
        for point in polygon_a:
            for index in range(count):
                best = min(best, _dist_point_segment(point, polygon_b[index], polygon_b[(index + 1) % count]))
    return best


def _polygon_area(polygon):
    total = 0.0
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


# ------------------------------------------------------------ source file

def _line_of(wall):
    try:
        curve = wall.Location.Curve
    except Exception:
        return None
    if not isinstance(curve, Line):
        return None
    p0, p1 = curve.GetEndPoint(0), curve.GetEndPoint(1)
    return (p0.X, p0.Y), (p1.X, p1.Y)


def analyse_source(src_doc):
    """Reads the Stairs file: what to copy and where its outer wall is.
    Returns a dict, or raises ValueError with a message for the user."""
    groups = []
    for group in FilteredElementCollector(src_doc).OfClass(Group):
        try:
            if group.ViewSpecific:
                continue
        except Exception:
            pass
        groups.append(group)

    copy_ids = []
    member_ids = None
    if groups:
        group = max(groups, key=lambda g: len(list(g.GetMemberIds())))
        copy_ids = [group.Id]
        member_ids = set(_id_int(i) for i in group.GetMemberIds())
        _log(u"Файл вузла: група «{}», елементів {}.".format(agp._element_name(group.GroupType), len(member_ids)))
    else:
        for element in FilteredElementCollector(src_doc).WhereElementIsNotElementType():
            try:
                category = element.Category
                if category is None or category.CategoryType != CategoryType.Model:
                    continue
                if element.ViewSpecific:
                    continue
            except Exception:
                continue
            copy_ids.append(element.Id)
        _log(u"Файл вузла: груп немає, копіюємо всі модельні елементи ({}).".format(len(copy_ids)))

    def _in_scope(element):
        return member_ids is None or _id_int(element.Id) in member_ids

    walls = []
    for wall in FilteredElementCollector(src_doc).OfClass(Wall):
        if not _in_scope(wall):
            continue
        line = _line_of(wall)
        if line is None:
            continue
        try:
            bbox = wall.get_BoundingBox(None)
            bottom = bbox.Min.Z if bbox is not None else 0.0
        except Exception:
            bottom = 0.0
        walls.append({"id": _id_int(wall.Id), "p0": line[0], "p1": line[1],
                      "width": wall.Width, "bottom": bottom})

    if not walls:
        raise ValueError(u"у файлі вузла немає прямих стін")

    wall_by_id = dict((w["id"], w) for w in walls)
    door_wall_ids = set()
    for door in FilteredElementCollector(src_doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsNotElementType():
        if not isinstance(door, FamilyInstance) or not _in_scope(door):
            continue
        host = door.Host
        if isinstance(host, Wall) and _id_int(host.Id) in wall_by_id:
            door_wall_ids.add(_id_int(host.Id))

    if not door_wall_ids:
        raise ValueError(u"у файлі вузла жодна стіна не має дверей")

    # From here on it is geometry over what was just read: the backend's (core/stairs.analyse_walls).
    remote = (_analyse_remote(walls, door_wall_ids))
    remote["copyIds"] = copy_ids
    return remote


# --------------------------------------------------------------- host side

def _collect_roofs(document, picker, level_id):
    """[(roof id, type name, polygon, z)] of every roof on `level_id`."""
    roofs = []
    for roof in FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_Roofs).WhereElementIsNotElementType():
        try:
            if picker._roof_level_id(roof) != level_id:
                continue
            polygon, z = picker._bottom_footprint(roof)
            if not polygon or len(polygon) < 3:
                continue
            type_name = picker._element_name(document.GetElement(roof.GetTypeId()))
        except Exception:
            continue
        roofs.append((_id_int(roof.Id), str(type_name or u"").strip().lower(), polygon, z))
    return roofs


def _analyse_remote(walls, door_wall_ids):
    """The backend's analysis of the Stairs file's walls (the dict analyse_source returns, without
    copyIds). Raises ValueError with the message for the user."""
    answer = backend_api.stairs_analyse({
        "walls": [{"id": w["id"], "p0": list(w["p0"]), "p1": list(w["p1"]), "width": w["width"], "bottom": w["bottom"]}
                  for w in walls],
        "doorWallIds": list(door_wall_ids),
    })
    for note in answer["notes"]:
        _log(note)
    if answer.get("error"):
        raise ValueError(answer["error"])
    return answer["source"]


def _best_pose(source, polygon, other_polygons, apartment_polygon):
    """(coverage, angle, target_point, chosen side, all sides) for the core on the МЗК footprint's
    OUTSIDE side - decided by the backend (core/stairs_planners._best_pose)."""
    return (_best_pose_remote(source, polygon, other_polygons, apartment_polygon))


def _best_pose_remote(source, polygon, other_polygons, apartment_polygon):
    answer = backend_api.stairs_pose({
        "source": dict((key, source[key]) for key in ("faceX", "faceY", "outNormal", "samples")),
        "polygon": [list(p) for p in polygon],
        "others": [[list(p) for p in other] for other in other_polygons],
        "apartmentPolygon": [list(p) for p in apartment_polygon],
    })
    for note in answer["notes"]:
        _log(note)
    pose = answer["pose"]
    if pose is None:
        return None
    sides = pose["allSides"]
    return pose["coverage"], pose["angle"], pose["mid"], sides[pose["chosenIndex"]], sides


# ------------------------------------------------------------------- entry

def run(uiapp, document, group_ids):
    """Place the stair/lift core on every "сходово-ліфтовий вузол" roof visible
    in the active view (the picked apartments only trigger the action)."""
    label = u"Розташування сходово-ліфтового вузла"

    def _result(done, message):
        return {"label": label, "done": done, "message": u"«{}»: {}".format(label, message)}

    files = stairs_files()
    if not files:
        return _result(False, u"у папці Stairs немає .rvt файлів з числом у назві.")

    picker = apartment_groups._picker()
    picker.doc = document

    # Every "сходово-ліфтовий вузол" roof visible in the active view.
    view = uiapp.ActiveUIDocument.ActiveView
    jobs = {}   # roof id -> {"polygon", "others", "z", "apartmentPolygon"}
    roofs_by_level = {}
    seen_types = set()

    for roof in FilteredElementCollector(document, view.Id).OfCategory(BuiltInCategory.OST_Roofs).WhereElementIsNotElementType():
        try:
            type_name = str(picker._element_name(document.GetElement(roof.GetTypeId())) or u"").strip().lower()
            seen_types.add(type_name)
            if not _is_core_type(type_name):
                continue
            level_id = picker._roof_level_id(roof)
            polygon, z = picker._bottom_footprint(roof)
        except Exception:
            continue
        if level_id is None or not polygon or len(polygon) < 3:
            continue

        if level_id not in roofs_by_level:
            roofs_by_level[level_id] = _collect_roofs(document, picker, level_id)

        roof_id = _id_int(roof.Id)
        # Only apartment/МЗК/node roofs count as neighbours (the level also
        # holds big roofs of other types that cover everything).
        others = [
            (rid, rtype, p) for rid, rtype, p, _z in roofs_by_level[level_id]
            if rid != roof_id and (
                rtype in picker._NORMALISED_BOUNDARY_TYPE_NAMES
                or picker._MZK_TYPE_NAME_SUBSTRING in rtype
                or _is_core_type(rtype)
            )
        ]

        # Which way is "away" from the flats: from the nearest apartment roof.
        apartment_polygons = [p for _rid, rtype, p in others if rtype in picker._NORMALISED_BOUNDARY_TYPE_NAMES]
        reference = min(apartment_polygons, key=lambda p: _polygon_gap(polygon, p)) if apartment_polygons else polygon

        jobs[roof_id] = {
            "polygon": polygon,
            "others": [p for _rid, _rtype, p in others],
            "z": z,
            "apartmentPolygon": reference,
            "levelId": level_id,
        }

    _log(u"Вид «{}»: плям типу «сходово-ліфтовий вузол»: {}.".format(view.Name, len(jobs)))
    if not jobs:
        _log(u"  типи плям на виді: {}.".format(u", ".join(sorted(seen_types))))
        return _result(False, u"на активному виді немає плям типу «сходово-ліфтовий вузол».")

    # Cores that were already placed (and still exist) are not duplicated.
    existing = placed_apartments.live_cores(document)
    todo = dict((roof_id, job) for roof_id, job in jobs.items() if roof_id not in existing)

    # A footprint that already has stairs on it (placed by us earlier or by
    # hand, in a group or not) gets nothing on top.
    stair_centres = []
    for stairs in FilteredElementCollector(document, view.Id).OfCategory(BuiltInCategory.OST_Stairs).WhereElementIsNotElementType():
        try:
            if stair_stack.is_multistory(stairs):
                continue                  # the stairs of a core that runs through the stack are not a core of this floor
            box = stairs.get_BoundingBox(None)
            if box is not None:
                stair_centres.append(((box.Min.X + box.Max.X) / 2.0, (box.Min.Y + box.Max.Y) / 2.0,
                                      (box.Min.Z + box.Max.Z) / 2.0))
        except Exception:
            continue
    for roof_id in list(todo):
        job = todo[roof_id]
        if any(abs(z - job["z"]) <= 12.0 and _point_in_polygon((x, y), job["polygon"])
               for x, y, z in stair_centres):
            _log(u"Вузол {}: на плямі вже є сходи - пропущено.".format(roof_id))
            del todo[roof_id]

    already = len(jobs) - len(todo)
    if not todo:
        return _result(False, u"на всіх плямах вузлів ({}) уже є сходи.".format(already))

    application = uiapp.Application
    opened = {}   # path -> (document, analysed source), opened when first needed
    placed, stacked, notes = 0, 0, []
    placed_roofs = []     # the core roofs that got a core now

    def _open(path):
        if path not in opened:
            document_ = agp._open_source_document(application, path)
            opened[path] = (document_, analyse_source(document_))
        return opened[path]

    try:

        for roof_id, job in sorted(todo.items()):
            # The same stack already has its core on another floor (a МЗК roof of this outline one above the
            # other): that core is copied here as it stands - same size, same mirror - no file, no side search.
            try:
                copied_from = _copy_stack_core(document, picker, roof_id, job)
            except Exception:
                _log(u"[ERROR] {}".format(traceback.format_exc()))
                notes.append(u"МЗК {}: вузол з іншого поверху не скопійовано (див. журнал), ставлю з файла".format(roof_id))
                copied_from = None
            if copied_from is not None:
                placed += 1
                stacked += 1
                placed_roofs.append(roof_id)
                _log(u"МЗК {}: скопійовано вузол з іншого поверху (група Id {}).".format(roof_id, _id_int(copied_from.Id)))
                continue

            _log(u"МЗК {}: перевірка сторін.".format(roof_id))
            try:
                # First pass (with the first file) only to find the outside side.
                first_path = files[0][1]
                src_doc, source = _open(first_path)
            except ValueError as error:
                return _result(False, u"{}.".format(error))
            pose = _best_pose(source, job["polygon"], job["others"], job["apartmentPolygon"])
            chosen_side, all_sides = None, []
            if pose is not None:
                pose, chosen_side, all_sides = pose[:3], pose[3], pose[4]

            # The file whose number is closest to the distance between the
            # footprint's two inside sides.
            if chosen_side is not None:
                inside_distance = _inside_distance_ft(chosen_side, all_sides) * 304.8
                number, path = min(files, key=lambda item: abs(item[0] - inside_distance))
                _log(u"МЗК {}: відстань між внутрішніми сторонами {:.0f} мм -> файл з числом {} ({}).".format(
                    roof_id, inside_distance, number, os.path.basename(path)))
                if path != first_path:
                    src_doc, source = _open(path)
                    pose = _best_pose(source, job["polygon"], job["others"], job["apartmentPolygon"])
                    if pose is not None:
                        pose, chosen_side, all_sides = pose[:3], pose[3], pose[4]
            if pose is None:
                # No usable side: the group still goes in as is, with its
                # outer wall's outside face at the footprint's centre.
                polygon = job["polygon"]
                centre = (sum(p[0] for p in polygon) / len(polygon),
                          sum(p[1] for p in polygon) / len(polygon))
                pose = (0.0, 0.0, centre)
                notes.append(u"МЗК {}: немає зовнішньої сторони, вузол поставлено в центр".format(roof_id))
            coverage, angle, mid = pose

            area_ratio = _polygon_area(job["polygon"]) / max(source["coreArea"], 1e-6)
            _log(u"МЗК {}: покриття {:.0%}, площа плями / площа вузла {:.2f}, кут {:.1f}°.".format(
                roof_id, coverage, area_ratio, math.degrees(angle)))

            base_z = job["z"]
            try:
                core_group_id = _copy_core(document, src_doc, source, mid, angle, base_z, roof_id)
                placed += 1
                placed_roofs.append(roof_id)

                # The wall close to the door goes centre-on to the footprint's
                # inside side (the sides running across the outside side).
                if chosen_side is not None:
                    inside_sides = _inside_sides_of(chosen_side, all_sides)
                    # The stairs go next to the "Житлова кімната" that touches the
                    # node (looking at the apartments on both sides): the core is
                    # mirrored when the stairs came out on the other side.
                    try:
                        # The cores of one stack (the same МЗК outline on every floor) stand with their stairs on
                        # the same side over the whole height: a core above or below one that is already placed
                        # is mirrored like it, whatever the rooms around it say.
                        wanted_side = _stack_wanted(document, picker, roof_id, job["polygon"], chosen_side)
                        if wanted_side is not None:
                            _log(u"Вузол {}: сходи з того ж боку, що й у вузла на іншому поверсі.".format(roof_id))
                        else:
                            wanted_side = _living_room_side(document, job, chosen_side)
                        if wanted_side is not None:
                            _log(u"Вузол {}: {}.".format(
                                roof_id, _face_stairs_to_side(document, core_group_id, chosen_side, wanted_side)))
                    except Exception as error:
                        _log(u"[ERROR] {}".format(traceback.format_exc()))
                        notes.append(u"{}: сходи не вдалося повернути до житлової кімнати ({})".format(roof_id, error))
                    try:
                        slide_message = _slide_wall_to_inside_side(document, core_group_id, inside_sides)
                    except Exception as error:
                        _log(u"[ERROR] {}".format(traceback.format_exc()))
                        slide_message = u"стіну не вдалося поставити ({})".format(error)
                    _log(u"Вузол {}: {}.".format(roof_id, slide_message))
                    notes.append(u"{}: {}".format(roof_id, slide_message))
            except Exception:
                _log(u"[ERROR] {}".format(traceback.format_exc()))
                notes.append(u"МЗК {}: помилка копіювання (див. журнал)".format(roof_id))
    finally:
        for opened_doc, _opened_source in opened.values():
            try:
                opened_doc.Close(False)
            except Exception:
                pass

    notes.extend(_run_through_the_stack(document, view, placed_roofs))

    message = u"розташовано {}".format(placed)
    if stacked:
        message += u" (з них {} скопійовано з вузла на іншому поверсі)".format(stacked)
    if already:
        message += u", уже були {}".format(already)
    if notes:
        message += u"; " + u"; ".join(notes)
    return _result(placed > 0, message + u".")


def _run_through_the_stack(document, view, roof_ids):
    """When a whole section is laid out (the view is one of the Froglet section views), the stairs and the shaft of
    each placed core go through the whole stack of floors (stair_stack): Multistory Stairs, a shaft from the lowest
    floor to under the slab over the top one, the rest of the core copied onto the other floors. A single floor
    (any other view) is left as it is. Returns the notes for the final message; a failure is logged, never raised."""
    if not roof_ids or not stair_stack.MAKE_MULTISTORY:
        return []
    import floor_copy

    notes = []
    try:
        sections = floor_copy.active_sections(document, view)
    except Exception:
        sections = None
    if not sections:
        return notes
    for roof_id in roof_ids:                    # (one core roof per stack on the active floor)
        try:
            text = stair_stack.convert(document, sections, roof_id, log=_log)
        except Exception:
            _log(u"[ERROR] {}".format(traceback.format_exc()))
            notes.append(u"МЗК {}: сходи на всі поверхи не зроблено (див. журнал), вузол лишено як є".format(roof_id))
            continue
        if text:
            notes.append(text.rstrip(u"."))
    return notes


def _index_of(side, sides):
    """The position of `side` in `sides` (by identity) or None."""
    for index, candidate in enumerate(sides):
        if candidate is side:
            return index
    return None


def _inside_distance_ft(chosen_side, all_sides):
    """Distance between the footprint's two inside sides - decided by the backend (core/stairs.
    inside_sides)."""
    index = _index_of(chosen_side, all_sides)
    if index is None:
        raise ValueError(u"сторону плями не знайдено серед її сторін")
    return (backend_api.stairs_inside({"allSides": all_sides, "chosenIndex": index})["distanceFt"])


def _inside_sides_of(chosen_side, all_sides):
    """The footprint's sides that touch the chosen side's ends: its inside sides."""
    index = _index_of(chosen_side, all_sides)
    if index is None:
        raise ValueError(u"сторону плями не знайдено серед її сторін")
    return ([all_sides[i] for i in backend_api.stairs_inside({"allSides": all_sides, "chosenIndex": index})["indices"]])


def _slide_wall_to_inside_side(document, group_id, inside_sides):
    """Slide the placed (still grouped) core along its outer wall so the wall
    that is close to the door - perpendicular to the outer wall, same
    thickness, NOT next to the stairs - has its centre line on the footprint's
    inside side. Nothing else is changed. Returns a message.

    Which wall slides and how far is the backend's decision (core/stairs.plan_slide); the
    move is made here."""
    group = document.GetElement(group_id)
    members = [m for m in (document.GetElement(i) for i in group.GetMemberIds()) if m is not None]

    wall_id, slide, o = (_slide_plan_remote(members, inside_sides))

    transaction = Transaction(document, u"Slide core to inside side")
    transaction.Start()
    agp.apply_silent_failure_handling(transaction)
    try:
        ElementTransformUtils.MoveElement(document, group_id, XYZ(o[0] * slide, o[1] * slide, 0.0))
        status = transaction.Commit()
        if status != TransactionStatus.Committed:
            raise Exception(u"транзакція не завершилась ({})".format(status))
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    return u"стіну біля дверей поставлено центром на внутрішню сторону (зсув {:+.2f} м)".format(slide * agp.FT_TO_M)


def _slide_plan_remote(members, inside_sides):
    """(wall id, slide, direction along the outer wall) from the backend; a ValueError carries the message for the user."""
    import Autodesk.Revit.DB.Architecture as architecture

    walls, door_host_ids, stairs_boxes = [], [], []
    stairs_class = getattr(architecture, "Stairs")
    for member in members:
        if isinstance(member, Wall):
            line = _line_of(member)
            if line is None:
                continue
            walls.append({"id": _id_int(member.Id), "p0": list(line[0]), "p1": list(line[1]), "width": member.Width})
        elif (isinstance(member, FamilyInstance) and member.Category is not None
                and _id_int(member.Category.Id) == int(BuiltInCategory.OST_Doors)):
            host = member.Host
            if isinstance(host, Wall):
                door_host_ids.append(_id_int(host.Id))
        if isinstance(member, stairs_class):
            box = member.get_BoundingBox(None)
            if box is not None:
                stairs_boxes.append([box.Min.X, box.Min.Y, box.Max.X, box.Max.Y])
    answer = backend_api.stairs_slide({
        "members": walls, "doorWallIds": door_host_ids, "stairsBoxes": stairs_boxes,
        "insideSides": [dict((key, side[key]) for key in ("a", "b")) for side in inside_sides],
    })
    for note in answer["notes"]:
        _log(note)
    if answer.get("error"):
        raise ValueError(answer["error"])
    return answer["wallId"], answer["slide"], tuple(answer["along"])


def _living_room_side_remote(document, job, chosen_side):
    """(side,) from the backend - a tuple, because "no side" (None) is an answer too."""
    entries = []
    for entry in placed_apartments.live_entries(document):
        entries.append({
            "name": entry.get("name"), "z": entry.get("z"), "rvtPath": entry.get("rvtPath"),
            "rooms": [{"p": [list(point) for point in room["p"]]} for room in (entry.get("rooms") or [])],
        })
    answer = backend_api.stairs_living_room_side({
        "polygon": [list(p) for p in job["polygon"]], "z": job["z"],
        "chosenSide": dict((key, chosen_side[key]) for key in ("a", "b")), "entries": entries,
    })
    for note in answer["notes"]:
        _log(note)
    return (answer["side"],)


def _living_room_side(document, job, chosen_side):
    """+1 / -1: on which side of the node (along the outside side, a -> b) the
    "Житлова кімната" rooms that touch the node's roof are; None when there are
    none, or as many on one side as on the other. Looks at the apartments on
    BOTH sides. Room names come from each apartment's JSON (same order as the
    rooms saved with the placed apartment).

    The backend decides (core/stairs.living_room_side: it reads the JSONs itself)."""
    remote = (_living_room_side_remote(document, job, chosen_side))
    return remote[0]


_STACK_POINT_STEP_FT = 0.05      # outlines are the same stack when their points agree to this
_STAIRS_MIDDLE_FT = 0.5          # stairs this close to the core's middle are on neither side


def polygon_key(polygon):
    """An outline as a comparable key: its points rounded to _STACK_POINT_STEP_FT, in any order."""
    return tuple(sorted((int(round(x / _STACK_POINT_STEP_FT)), int(round(y / _STACK_POINT_STEP_FT))) for x, y in polygon))


def stairs_side(stairs_boxes, wall_boxes, dx, dy):
    """On which side of the core the stairs are, along the unit direction (dx, dy): +1 (towards its end), -1, or None
    (in the middle, or no stairs / walls). The boxes are [min x, min y, max x, max y] in feet."""
    def values(boxes):
        return [x * dx + y * dy for box in boxes for x in (box[0], box[2]) for y in (box[1], box[3])]

    stairs_values, core_values = values(stairs_boxes), values(wall_boxes)
    if not stairs_values or not core_values:
        return None
    difference = (min(stairs_values) + max(stairs_values)) / 2.0 - (min(core_values) + max(core_values)) / 2.0
    if abs(difference) < _STAIRS_MIDDLE_FT:
        return None
    return 1 if difference > 0 else -1


def _stack_wanted(document, picker, roof_id, polygon, chosen_side):
    """+1 / -1: the side (along chosen_side's a -> b) the stairs must be on so that this core stands like the core of
    the same stack (a МЗК roof with the same outline) that is already placed on another floor; None when there is
    none, or it cannot be read."""
    import Autodesk.Revit.DB.Architecture as architecture

    try:
        dx = chosen_side["b"][0] - chosen_side["a"][0]
        dy = chosen_side["b"][1] - chosen_side["a"][1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return None
        dx, dy = dx / length, dy / length
        key = polygon_key(polygon)
        stairs_class = getattr(architecture, "Stairs")
        for other_id, uid in sorted(placed_apartments.live_cores(document).items()):
            if other_id == roof_id:
                continue
            roof = document.GetElement(ElementId(int(other_id)))
            group = document.GetElement(uid)
            if roof is None or group is None:
                continue
            other_polygon, _z = picker._bottom_footprint(roof)
            if not other_polygon or polygon_key(other_polygon) != key:
                continue
            stairs_boxes, wall_boxes = [], []
            for member_id in group.GetMemberIds():
                member = document.GetElement(member_id)
                box = member.get_BoundingBox(None) if member is not None else None
                if box is None:
                    continue
                rect = [box.Min.X, box.Min.Y, box.Max.X, box.Max.Y]
                if isinstance(member, stairs_class):
                    stairs_boxes.append(rect)
                elif isinstance(member, Wall):
                    wall_boxes.append(rect)
            side = stairs_side(stairs_boxes, wall_boxes, dx, dy)
            if side is not None:
                return side
    except Exception:
        _log(u"[ERROR] сторона сходів сусіднього поверху: {}".format(traceback.format_exc()))
    return None


def _stack_core_to_copy(document, picker, roof_id, polygon, z):
    """(group, dz): the core already placed on a МЗК roof with this very outline (same place in the plan) on
    another floor - the one closest in height - and how far up (feet) it has to be moved to stand on this
    roof; None when there is none."""
    key = polygon_key(polygon)
    best = None
    for other_id, uid in sorted(placed_apartments.live_cores(document).items()):
        if other_id == roof_id:
            continue
        try:
            roof = document.GetElement(ElementId(int(other_id)))
            group = document.GetElement(uid)
            if roof is None or group is None:
                continue
            other_polygon, other_z = picker._bottom_footprint(roof)
        except Exception:
            continue
        if not other_polygon or other_z is None or polygon_key(other_polygon) != key:
            continue
        dz = z - other_z
        if abs(dz) < _Z_TOLERANCE_FT:
            continue                                  # the same floor
        if best is None or abs(dz) < abs(best[1]):
            best = (group, dz)
    return best


def _copy_stack_core(document, picker, roof_id, job):
    """The core of the same stack that is already placed on another floor is copied straight up/down onto
    this floor's roof (so it stands exactly like it, mirror included) instead of being placed from the Stairs
    file again. The source group, or None when there is no such core. Raises when Revit refuses the copy (the
    caller then places the core from the file)."""
    found = _stack_core_to_copy(document, picker, roof_id, job["polygon"], job["z"])
    if found is None:
        return None
    group, dz = found

    transaction = Transaction(document, u"Copy stair core")
    transaction.Start()
    agp.apply_silent_failure_handling(transaction)
    try:
        ids = List[ElementId]()
        ids.Add(group.Id)
        new_ids = ElementTransformUtils.CopyElements(document, ids, XYZ(0.0, 0.0, dz))
        if new_ids is None or new_ids.Count == 0:
            raise Exception(u"CopyElements нічого не скопіював")
        copy = None
        for new_id in new_ids:
            element = document.GetElement(new_id)
            if copy is None:
                copy = element
            if isinstance(element, Group) and not element.ViewSpecific:
                copy = element
                break
        status = transaction.Commit()
        if status != TransactionStatus.Committed:
            raise Exception(u"транзакція не завершилась ({})".format(status))
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise

    placed_apartments.record_core(document, roof_id, copy.UniqueId)
    return group


def _face_stairs_to_side(document, group_id, chosen_side, wanted):
    """Mirror the placed core about the plane through its centre, across the
    outside side, if its stairs are not on the `wanted` side (+1: towards the
    side's b end). Returns a message.

    Whether to mirror, and about which plane, is the backend's decision (core/stairs.plan_face);
    the mirror is made here."""
    from Autodesk.Revit.DB import Plane

    group = document.GetElement(group_id)
    members = [m for m in (document.GetElement(i) for i in group.GetMemberIds()) if m is not None]

    plan = (_face_plan_remote(group, members, chosen_side, wanted))
    if not plan["mirror"]:
        return plan["message"]
    plane = Plane.CreateByNormalAndOrigin(
        XYZ(plan["normal"][0], plan["normal"][1], 0.0), XYZ(plan["origin"][0], plan["origin"][1], plan["origin"][2]))

    transaction = Transaction(document, u"Mirror core")
    transaction.Start()
    agp.apply_silent_failure_handling(transaction)
    try:
        ids = List[ElementId]()
        ids.Add(group_id)
        ElementTransformUtils.MirrorElements(document, ids, plane, False)
        status = transaction.Commit()
        if status != TransactionStatus.Committed:
            raise Exception(u"транзакція не завершилась ({})".format(status))
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    return plan["message"]


def _face_plan_remote(group, members, chosen_side, wanted):
    import Autodesk.Revit.DB.Architecture as architecture

    stairs_class = getattr(architecture, "Stairs")
    stairs_boxes, wall_boxes = [], []
    for member in members:
        box = member.get_BoundingBox(None)
        if box is None:
            continue
        rect = [box.Min.X, box.Min.Y, box.Max.X, box.Max.Y]
        if isinstance(member, stairs_class):
            stairs_boxes.append(rect)
        if isinstance(member, Wall):
            wall_boxes.append(rect)
    group_box = group.get_BoundingBox(None)
    answer = backend_api.stairs_face({
        "chosenSide": dict((key, chosen_side[key]) for key in ("a", "b")), "wanted": wanted,
        "stairsBoxes": stairs_boxes, "wallBoxes": wall_boxes,
        "groupBox": [group_box.Min.X, group_box.Min.Y, group_box.Min.Z, group_box.Max.X, group_box.Max.Y, group_box.Max.Z],
    })
    if answer.get("error"):
        raise ValueError(answer["error"])
    return answer


def _copy_core(document, src_doc, source, mid, angle, base_z, mzk_roof_id):
    transaction = Transaction(document, u"Place stair core")
    transaction.Start()
    agp.apply_silent_failure_handling(transaction)
    try:
        ids = List[ElementId]()
        for element_id in source["copyIds"]:
            ids.Add(element_id)

        options = CopyPasteOptions()
        options.SetDuplicateTypeNamesHandler(agp.UseDestinationTypes())

        move = XYZ(mid[0] - source["faceX"], mid[1] - source["faceY"], base_z - source["zBottom"])
        new_ids = ElementTransformUtils.CopyElements(
            src_doc, ids, document, Transform.CreateTranslation(move), options
        )
        if new_ids is None or new_ids.Count == 0:
            raise Exception(u"CopyElements нічого не скопіював")

        if abs(angle) > 1e-9:
            axis = Line.CreateBound(XYZ(mid[0], mid[1], base_z), XYZ(mid[0], mid[1], base_z + 1.0))
            ElementTransformUtils.RotateElements(document, new_ids, axis, angle)

        uid = None
        first = document.GetElement(list(new_ids)[0])
        group_element_id = first.Id
        if isinstance(first, Group) and new_ids.Count == 1:
            uid = first.UniqueId
        else:
            try:
                group = document.Create.NewGroup(new_ids)
                uid = group.UniqueId
                group_element_id = group.Id
            except Exception:
                uid = first.UniqueId

        status = transaction.Commit()
        if status != TransactionStatus.Committed:
            raise Exception(u"транзакція не завершилась ({})".format(status))
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise

    placed_apartments.record_core(document, mzk_roof_id, uid)
    return group_element_id
