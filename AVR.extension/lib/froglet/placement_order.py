# -*- coding: utf-8 -*-
"""Order in which a whole floor is placed: the apartments next to a
"сходово-ліфтовий вузол" roof first, then their neighbours, then the
neighbours' neighbours, and so on - so every apartment is placed when the
apartments around it are already there (room types are matched to them)."""

import math


def _is_stair_core_type_name(name):
    name = (name or u"").lower()
    return u"сходов" in name or u"ліфт" in name or u"лифт" in name


def _is_mzk_type_name(name):
    return u"мзк" in (name or u"").lower()


def stair_core_polygons(document, view):
    """Footprints (feet) of the "сходово-ліфтовий вузол" roofs visible in `view`."""
    return _roof_polygons(document, view, _is_stair_core_type_name)


def mzk_polygons(document, view):
    """Footprints (feet) of the МЗК roofs (corridors, lobbies - the "мзк" types) visible in `view`."""
    return _roof_polygons(document, view, _is_mzk_type_name)


def _roof_polygons(document, view, wanted):
    import apartment_groups
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    picker = apartment_groups._picker()
    picker.doc = document
    found = []
    for roof in FilteredElementCollector(document, view.Id).OfCategory(BuiltInCategory.OST_Roofs).WhereElementIsNotElementType():
        try:
            type_name = picker._element_name(document.GetElement(roof.GetTypeId()))
            if not wanted(type_name):
                continue
            polygon, _z = picker._bottom_footprint(roof)
        except Exception:
            continue
        if polygon and len(polygon) >= 3:
            found.append(polygon)
    return found


def _segment_distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length_sq))
    return math.hypot(point[0] - (a[0] + t * dx), point[1] - (a[1] + t * dy))


def order_footprints(items, open_ids, core_polygons):
    """Roof ids of `open_ids` in placement order (breadth-first from the
    footprints next to a stair/lift node). Without any node the plain id order.
    Decided by the backend (core/ordering.py)."""
    import backend_api

    open_ids = list(open_ids)
    return (backend_api.order_footprints(items, open_ids, core_polygons))

