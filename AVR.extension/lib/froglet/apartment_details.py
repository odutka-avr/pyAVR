# -*- coding: utf-8 -*-
"""What the apartment's JSON says about it, for the apartment's own tab in the "Банк квартир":
the parameters of the plan, and every room with its size, its name, what it is connected to and
which room it is entered FROM (the way in from the front door, through the doors between rooms).

The JSON of an apartment (next to its .rvt, exported from Revit) already holds the rooms with their
outlines, the entrance room ("derived2.entry_room") and, for every room, the rooms its doors lead to
("derived2.per_room[..].doors_to"). Nothing here touches Revit or a window.

What the window shows is the plugin's own business, so this lives in the plugin (the backend
has no copy of it). Plain Python that runs under IronPython 2.7: no f-strings, no annotations, and
a float() before every "{:.2f}" (IronPython refuses a precision for an int).
"""

import io
import json
import math
import os

_PROBE_OFFSETS_M = (0.25, 0.5, 0.8)      # how far past a wall's face a door's two sides are looked at
_WAY_RANK = {"door": 3, "opening": 2, "window": 1}


def json_path(rvt_path):
    return os.path.splitext(rvt_path)[0] + ".json"


def load(rvt_path):
    """The parsed JSON next to the .rvt, or None."""
    try:
        with io.open(json_path(rvt_path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _number(value):
    try:
        return float(value)
    except Exception:
        return None


def _room_key(room):
    return u"{} {}".format(room.get("number") or u"", room.get("name") or u"").strip()


def _outline(room):
    """The room's outer outline as [(x, y)] in metres, or []."""
    try:
        loops = room.get("boundary") or []
        points = []
        for segment in loops[0]:
            first = segment["pts"][0]
            points.append((float(first[0]), float(first[1])))
        return points
    except Exception:
        return []


def _dims_mm(room):
    """(short, long) side in mm of the room's bounding rectangle, measured along its own longest wall
    (the same way the apartment's nominal size is); [] when there is no outline."""
    points = _outline(room)
    if len(points) >= 3:
        longest, angle = -1.0, 0.0
        for index in range(len(points)):
            x1, y1 = points[index]
            x2, y2 = points[(index + 1) % len(points)]
            length = (x2 - x1) ** 2 + (y2 - y1) ** 2
            if length > longest:
                longest, angle = length, math.atan2(y2 - y1, x2 - x1)
        cos_a, sin_a = math.cos(-angle), math.sin(-angle)
        xs = [x * cos_a - y * sin_a for x, y in points]
        ys = [x * sin_a + y * cos_a for x, y in points]
        sides = ((max(xs) - min(xs)) * 1000.0, (max(ys) - min(ys)) * 1000.0)
        return [round(min(sides), 0), round(max(sides), 0)]
    box = room.get("bbox")
    try:
        sides = (abs(box[1][0] - box[0][0]) * 1000.0, abs(box[1][1] - box[0][1]) * 1000.0)
        return [round(min(sides), 0), round(max(sides), 0)]
    except Exception:
        return []


def _inside(point, polygon):
    x, y = point
    result, j = False, len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            result = not result
        j = i
    return result


def _room_at(point, outlines):
    for key, polygon in outlines:
        if len(polygon) >= 3 and _inside(point, polygon):
            return key
    return None


def _across(line, point, half_width, outlines):
    """The rooms on the two sides of a wall (or room separator) at `point`: (key or None, key or None)."""
    (x0, y0), (x1, y1) = (line[0][0], line[0][1]), (line[1][0], line[1][1])
    length = math.hypot(x1 - x0, y1 - y0) or 1.0
    nx, ny = -(y1 - y0) / length, (x1 - x0) / length
    sides = []
    for sign in (1, -1):
        found = None
        for offset in _PROBE_OFFSETS_M:
            found = _room_at((point[0] + sign * nx * (half_width + offset), point[1] + sign * ny * (half_width + offset)), outlines)
            if found:
                break
        sides.append(found)
    return sides


def _counts(names):
    result = {}
    for name in names or []:
        result[name] = result.get(name, 0) + 1
    return result


def _distances(keys, links, start):
    """Steps from `start` to every room that can be reached from it, and the path to each. The ways
    through doors and open passages come first; a room that can only be reached through a window (a
    loggia behind a balcony door) is attached afterwards."""
    distance, path = {start: 0}, {start: [start]}
    for kinds in (("door", "opening"), ("door", "opening", "window")):
        queue = list(distance)
        while queue:
            current = queue.pop(0)
            for other in keys:
                if other in distance or links.get(current, {}).get(other) not in kinds:
                    continue
                distance[other] = distance[current] + 1
                path[other] = path[current] + [other]
                queue.append(other)
    return distance, path


def describe(data):
    """{"summary": {...}, "rooms": [{...}], "notes": [str]} for an apartment's parsed JSON."""
    notes = []
    parsed = data.get("name_parsed") or {}
    derived = data.get("derived") or {}
    derived2 = data.get("derived2") or {}
    per_room = derived2.get("per_room") or {}
    counts = derived.get("counts") or {}
    rooms_data = data.get("rooms") or []

    names = dict((_room_key(room), room.get("name") or u"") for room in rooms_data)
    keys = [_room_key(room) for room in rooms_data]
    entry = derived2.get("entry_room")
    if entry not in names:
        entry = None
    if not derived2.get("entry_room"):
        notes.append(u"У JSON не вказано кімнату, з якої починається квартира (вхідні двері).")
    if not per_room:
        notes.append(u"У JSON немає опису кімнат (derived2.per_room): двері між кімнатами невідомі.")

    # How the rooms are connected: the doors the JSON lists, the doors found by looking at both sides
    # of the wall they stand in, the open passages (room separators), and - last - the windows.
    links = dict((key, {}) for key in keys)

    def connect(first, second, way):
        if first and second and first != second and first in links and second in links:
            for one, two in ((first, second), (second, first)):
                if _WAY_RANK[way] > _WAY_RANK.get(links[one].get(two), 0):     # a door beats an opening beats a window
                    links[one][two] = way

    for key in keys:
        for other in (per_room.get(key) or {}).get("doors_to") or []:
            connect(key, other, "door")
    outlines = [(_room_key(room), _outline(room)) for room in rooms_data]
    walls = dict((wall.get("id"), wall) for wall in data.get("walls") or [])
    for door in data.get("doors") or []:
        wall, point = walls.get(door.get("host_id")), door.get("point")
        if wall and point and wall.get("line"):
            first, second = _across(wall["line"], point, (wall.get("width_mm") or 200) / 2000.0, outlines)
            connect(first, second, "door")
    for separator in data.get("room_separators") or []:
        line = separator.get("line")
        if line:
            middle = ((line[0][0] + line[1][0]) / 2.0, (line[0][1] + line[1][1]) / 2.0)
            first, second = _across(line, middle, 0.0, outlines)
            connect(first, second, "opening")
    for key in keys:
        for other in (per_room.get(key) or {}).get("win_to") or []:
            connect(key, other, "window")
    distance, path = _distances(keys, links, entry) if entry else ({}, {})

    rooms = []
    for room in rooms_data:
        key = _room_key(room)
        info = per_room.get(key) or {}
        params = room.get("params") or {}
        is_entry = key == entry
        if is_entry:
            entered_from, entered_via = [], []
        else:
            before = [other for other in keys
                      if other in links[key] and key in distance and distance.get(other, 1 << 30) < distance[key]]
            entered_from = [names[other] for other in before]
            entered_via = [links[key][other] for other in before]
        rooms.append({
            "key": key,
            "number": room.get("number"),
            "name": room.get("name"),
            "areaM2": room.get("area_m2"),
            "areaWithCoefficientM2": _number(params.get("AVR_Площа з Коефіціентом")),
            "perimeterM": room.get("perimeter_m"),
            "dimsMm": _dims_mm(room),
            "category": params.get("AVR_Категорія Приміщення"),
            "typeCode": params.get("AVR_Тип Приміщення"),
            "kind": info.get("class"),
            "windows": info.get("windows"),
            "balconyDoors": info.get("balcony_doors"),
            "windowsTo": [names.get(other, other) for other in info.get("win_to") or []],
            "fixtures": _counts(info.get("fixtures")),
            "furniture": _counts(info.get("furniture")),
            "isEntry": is_entry,
            "connectedTo": [names[other] for other in keys if other in links[key]],
            "connectedVia": [links[key][other] for other in keys if other in links[key]],
            "enteredFrom": entered_from,
            "enteredVia": entered_via,
            "pathFromEntrance": [names[other] for other in path[key]] if key in path else None,
        })

    summary = {
        "code": derived2.get("apt_code") or (data.get("apartment") or {}).get("AVR_Номер Квартири"),
        "area": derived2.get("area"),
        "areaTotal": derived2.get("area_total"),
        "areaLiving": derived2.get("area_living"),
        "rooms": parsed.get("rooms"),
        "roomsCount": derived2.get("rooms_count") if derived2.get("rooms_count") is not None else len(rooms_data),
        "floor": parsed.get("floor"),
        "part": parsed.get("part"),
        "externalWalls": parsed.get("ext_walls"),
        "externalWallsCount": derived.get("ext_walls"),
        "variant": parsed.get("variant"),
        "widthMm": parsed.get("width"),
        "depthMm": parsed.get("depth"),
        "roomsTotalM2": derived.get("rooms_total_m2"),
        "extentMm": derived.get("rooms_extent_mm"),
        "doors": counts.get("doors"),
        "windows": counts.get("windows"),
        "entryRoom": names.get(entry) if entry else None,
        "kitchenAreaM2": derived2.get("kitchen_area"),
        "livingAreaM2": derived2.get("living_area"),
        "hallAreaM2": derived2.get("hall_area"),
    }
    return {"summary": summary, "rooms": rooms, "notes": notes}


def _meters(value):
    # float(): IronPython 2.7 refuses a precision ("{:.2f}") for an int, which CPython accepts - a JSON
    # that holds 10 where another holds 10.5 must not bring the window down
    return u"{:.2f}".format(float(value)).replace(u".", u",") if value is not None else None


def format_room(room, show_door=False):
    """The room as the lines of its row: (name, [lines]) - the area, the size, and - only when
    `show_door` is set (the window asks for it for the living room of a 1k / 2k / 3k apartment) - the
    room it is entered from, on the other side of the door."""
    title = room.get("name") or room.get("key") or u""
    lines = []
    if room.get("areaM2") is not None:
        lines.append(_meters(room["areaM2"]) + u" м²")
    dims = room.get("dimsMm")
    if dims and len(dims) == 2:
        lines.append(u"{:.0f} × {:.0f} мм".format(float(dims[0]), float(dims[1])))
    if show_door:
        if room.get("isEntry"):
            lines.append(u"Вхід зі сходової клітки")
        else:
            others = room.get("enteredFrom") or room.get("connectedTo") or []
            if others:
                lines.append(u"Вхід з «" + u"», «".join(others) + u"»")
            else:
                lines.append(u"Вхід не визначено")
    return title, lines