# -*- coding: utf-8 -*-
"""Group the apartment footprints (boundary roofs) visible in a view.

A group holds footprints with the same window placement and added part,
whose areas span at most 7 m2 and whose nominal dimensions (width, length,
from the footprint's own outline) each span at most 1000 mm.

The classification itself reuses the footprint picker's geometry code
(Вибір плями квартири._impl/script.py); only the batch driver and the
grouping live here.
"""

import imp
import math
import os

from Autodesk.Revit.DB import BuiltInCategory, ElementId, FilteredElementCollector

import backend_api

_FT_TO_MM = 304.8
# Footprints chained by gaps up to this size form one building section
# (corridors are ~1-2 m; sections are many metres apart).
SECTION_GAP_FT = 4000.0 / _FT_TO_MM

_EXTENSION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tab_dir(extension_dir):
    """The extension's one "*.tab" folder (its name differs between the two plugins: "Froglet розробка.tab" and
    "Froglet.tab", so that pyRevit shows them as two tabs)."""
    for name in sorted(os.listdir(extension_dir)):
        if name.endswith(u".tab") and os.path.isdir(os.path.join(extension_dir, name)):
            return os.path.join(extension_dir, name)
    return os.path.join(extension_dir, u"Froglet.tab")


_PICKER_PATH = os.path.join(
    _tab_dir(_EXTENSION_DIR),
    u"Вибір планування.panel",
    u"Вибір плями квартири._impl",
    u"script.py",
)

_picker_module = {"module": None}


def _picker():
    if _picker_module["module"] is None:
        _picker_module["module"] = imp.load_source(
            "froglet_footprint_picker", _PICKER_PATH
        )
    return _picker_module["module"]


def _nominal_dimensions_mm(points, angle):
    """(shorter, longer) side in mm of the footprint's bounding rectangle,
    measured along its own dominant (longest-edge) direction."""
    cos_a, sin_a = math.cos(-angle), math.sin(-angle)
    xs, ys = [], []
    for x, y in points:
        xs.append(x * cos_a - y * sin_a)
        ys.append(x * sin_a + y * cos_a)

    side_a = (max(xs) - min(xs)) * _FT_TO_MM
    side_b = (max(ys) - min(ys)) * _FT_TO_MM
    return (min(side_a, side_b), max(side_a, side_b))


def _read_roofs(picker, document, view_roof_ids, placement_ids):
    """Every roof of the document as the backend needs it (core/floor_scan.py), in collector order:
    id, type name, lowest face (outline + height), level, the middle of the bounding box (only for
    the roofs whose level is unknown and the МЗК ones - the only places it is compared), the area
    (the roofs of the view) and the level a placement is made on (the roofs asked about)."""
    in_view = set(view_roof_ids)
    asked = set(placement_ids)
    roofs = []
    collector = (
        FilteredElementCollector(document)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
    )
    for roof in collector:
        roof_id = picker._element_id_int(roof.Id)
        try:
            type_name = picker._element_name(document.GetElement(roof.GetTypeId())) or u""
        except Exception:
            type_name = u""
        footprint, z = picker._bottom_footprint(roof)
        level_id = picker._roof_level_id(roof)
        needs_center = level_id is None or picker._MZK_TYPE_NAME_SUBSTRING in str(type_name).strip().lower()
        placement_level_id = None
        if roof_id in asked:
            level = picker._roof_level(document, roof)
            placement_level_id = picker._element_id_int(level.Id) if level is not None else None
        roofs.append({
            "id": roof_id,
            "typeName": type_name,
            "footprint": [[point[0], point[1]] for point in footprint] if footprint else None,
            "z": z,
            "levelId": level_id,
            "centerZ": picker._roof_bbox_center_z(roof) if needs_center else None,
            "area": picker._area_in_square_meters(roof) if roof_id in in_view else None,
            "placementLevelId": placement_level_id,
        })
    return roofs


def _collect_remote(picker, document, view):
    """collect_footprints from the backend: the roofs of the document and the ids of the ones in
    the view go there; the items come back with their outlines as tuples again."""
    view_roof_ids = [
        picker._element_id_int(roof.Id)
        for roof in (
            FilteredElementCollector(document, view.Id)
            .OfCategory(BuiltInCategory.OST_Roofs)
            .WhereElementIsNotElementType()
        )
    ]
    items = backend_api.collect_footprints(_read_roofs(picker, document, view_roof_ids, []), view_roof_ids)
    for item in items:
        item["footprint"] = [tuple(point) for point in item["footprint"]]
        item["dims"] = tuple(item["dims"])
    return items


def collect_footprints(uiapp, view):
    """One dict per boundary-type roof visible in `view`:
    id, type, area (m2), window ("R"/"C"/"L"/"N"/None), added ("AP"/"NP"/
    None), dims (short_mm, long_mm)."""
    picker = _picker()
    document = view.Document

    # The picker module captured revit.doc/uidoc at import time.
    picker.doc = document
    picker.uidoc = uiapp.ActiveUIDocument

    # The backend does the rest (core/floor_scan.py) from the roofs read here.
    return (_collect_remote(picker, document, view))


def group_footprints(items, section_items=None):
    """Greedy grouping by (window placement, added part), then area and
    dimension spans. Items are visited in area order so groups come out
    sorted by area. Building sections are numbered over `section_items`
    (default: `items`) so a subset keeps the full list's section numbers.

    Decided by the backend (core/grouping.py); the answer refers to items by
    id and is turned back into groups of THESE item dicts (and every item
    of `section_items` gets its "section")."""
    return (_group_footprints_remote(items, section_items))


def _group_footprints_remote(items, section_items):
    answer = backend_api.group_footprints(items, section_items)
    section_of = answer["sectionOf"]
    for item in (section_items if section_items is not None else items):
        item["section"] = section_of[item["id"]]

    by_id = dict((item["id"], item) for item in items)
    groups = []
    for raw in answer["groups"]:
        members = [by_id[member_id] for member_id in raw["memberIds"]]
        groups.append({
            "window": raw["window"],
            "added": raw["added"],
            "members": members,
            "section_count": answer["sectionCount"],
            "sections": [
                (part["number"], [by_id[member_id] for member_id in part["memberIds"]])
                for part in raw["sections"]
            ],
        })
    return groups


def _placements_remote(picker, document, element_ids):
    """compute_placements from the backend; what it wrote to its log goes to the debug log the
    picker writes to."""
    ids = [int(element_id) for element_id in element_ids]
    answer = backend_api.compute_placements(_read_roofs(picker, document, [], ids), ids)
    bank = getattr(picker, "apartment_bank_window", None)
    if bank is not None:
        for entry in answer["logs"]:
            bank._debug_log(entry)
    return answer["placements"]


def compute_placements(uiapp, view, element_ids):
    """Placement payload (the same dict "Вибір плями квартири" stores for a
    single pick) for each roof id, for placing an apartment on every
    footprint of a group. Footprints whose placement cannot be computed are
    skipped. Debug lines and dialogs of the picker are suppressed."""
    picker = _picker()
    document = view.Document
    picker.doc = document
    picker.uidoc = uiapp.ActiveUIDocument

    # The backend composes the payloads (core/floor_scan.py) from the roofs read here.
    return (_placements_remote(picker, document, element_ids))
