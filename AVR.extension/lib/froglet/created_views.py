# -*- coding: utf-8 -*-
"""Remembers which views Froglet created (per Revit document) so the
"Створення виду" column can list them. Only the views' UniqueIds are stored
(in %APPDATA%\\DKUI\\created_views.json); names are always read live from
Revit, so a renamed view shows its new name and a deleted one disappears."""

import io
import json
import os

from Autodesk.Revit.DB import Element, View

_PATH = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "DKUI", "created_views.json"
)


def _key(document):
    return document.PathName or document.Title


def _load():
    try:
        with io.open(_PATH, "r", encoding="utf-8") as handle:
            return json.loads(handle.read())
    except Exception:
        return {}


def _save(data):
    folder = os.path.dirname(_PATH)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with io.open(_PATH, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, indent=1))


def record(document, view):
    data = _load()
    unique_ids = data.setdefault(_key(document), [])
    if view.UniqueId not in unique_ids:
        unique_ids.append(view.UniqueId)
    _save(data)


def list_views(document):
    """[(element id, current name)] for the still-existing views Froglet
    created in this document, in creation order. Entries whose view was
    deleted are pruned."""
    data = _load()
    stored = data.get(_key(document), [])

    entries, alive = [], []
    for unique_id in stored:
        try:
            element = document.GetElement(unique_id)
        except Exception:
            element = None
        if element is None or not isinstance(element, View):
            continue
        alive.append(unique_id)
        try:
            name = Element.Name.__get__(element)
        except Exception:
            name = element.Name
        try:
            element_id = int(element.Id.Value)
        except Exception:
            element_id = int(element.Id.IntegerValue)
        entries.append((element_id, name))

    if alive != stored:
        data[_key(document)] = alive
        try:
            _save(data)
        except Exception:
            pass

    return entries


_SECTION_PREFIX = u"sections::"


def record_sections(document, sections, views):
    """Remember the views created for a choice of building sections: `sections` =
    the section numbers (text), `views` = [(view, "3d" | "plan")]."""
    data = _load()
    sets = data.setdefault(_SECTION_PREFIX + _key(document), [])
    sets.append({
        "sections": list(sections),
        "views": [{"uid": view.UniqueId, "kind": kind} for view, kind in views],
    })
    _save(data)


def add_views_to_set(document, sections, views):
    """Add `views` ([(view, "3d" | "plan")]) to the newest remembered choice of exactly these `sections`
    (so the plans made later for other floors sit under the same "Секції: ..." heading); a new
    choice is made when there is none."""
    data = _load()
    key = _SECTION_PREFIX + _key(document)
    for entry in reversed(data.get(key, [])):
        if list(entry.get("sections", [])) == list(sections):
            entry.setdefault("views", []).extend(
                {"uid": view.UniqueId, "kind": kind} for view, kind in views)
            _save(data)
            return
    record_sections(document, sections, views)


def list_section_views(document):
    """[(section numbers, [(element id, current name, kind)], set key)] for the section
    choices whose views still exist (deleted views are pruned)."""
    data = _load()
    key = _SECTION_PREFIX + _key(document)
    stored = data.get(key, [])

    result, alive_sets = [], []
    for entry in stored:
        views, alive = [], []
        for item in entry.get("views", []):
            try:
                element = document.GetElement(item["uid"])
            except Exception:
                element = None
            if element is None or not isinstance(element, View):
                continue
            alive.append(item)
            try:
                name = Element.Name.__get__(element)
            except Exception:
                name = element.Name
            try:
                element_id = int(element.Id.Value)
            except Exception:
                element_id = int(element.Id.IntegerValue)
            views.append((element_id, name, item.get("kind")))
        if views:
            alive_sets.append({"sections": entry.get("sections", []), "views": alive})
            # (the set is told apart by the unique id of its first view)
            result.append((entry.get("sections", []), views, alive[0]["uid"]))

    if alive_sets != stored:
        data[key] = alive_sets
        try:
            _save(data)
        except Exception:
            pass
    return result


def is_created(document, view):
    """True if `view` is one of the views Froglet created in `document` (also
    the views created for chosen sections)."""
    try:
        data = _load()
        if view.UniqueId in data.get(_key(document), []):
            return True
        for entry in data.get(_SECTION_PREFIX + _key(document), []):
            if any(item.get("uid") == view.UniqueId and item.get("kind") == "plan" for item in entry.get("views", [])):
                return True
        return False
    except Exception:
        return False


def delete_section_set(document, set_key):
    """Delete every view created for the section choice `set_key` (the unique id of its
    first view) and forget the choice. Needs an open API context (it opens a transaction).
    Returns how many views were deleted."""
    from Autodesk.Revit.DB import ElementId, Transaction, ViewPlan

    data = _load()
    key = _SECTION_PREFIX + _key(document)
    stored = data.get(key, [])
    target = None
    for entry in stored:
        uids = [item.get("uid") for item in entry.get("views", [])]
        if uids and uids[0] == set_key:
            target = entry
            break
    if target is None:
        return 0

    views = []
    for item in target.get("views", []):
        try:
            element = document.GetElement(item["uid"])
        except Exception:
            element = None
        if element is not None and isinstance(element, View):
            views.append(element)

    # The view that is open cannot be deleted: open another one first.
    uidoc = None
    try:
        from pyrevit import HOST_APP
        uidoc = HOST_APP.uiapp.ActiveUIDocument
    except Exception:
        uidoc = None
    doomed_ids = set(int(view.Id.IntegerValue) if not hasattr(view.Id, "Value") else int(view.Id.Value) for view in views)
    if uidoc is not None:
        try:
            active = uidoc.ActiveView
            active_id = int(active.Id.Value) if hasattr(active.Id, "Value") else int(active.Id.IntegerValue)
            if active_id in doomed_ids:
                from Autodesk.Revit.DB import FilteredElementCollector
                for candidate in FilteredElementCollector(document).OfClass(ViewPlan):
                    candidate_id = int(candidate.Id.Value) if hasattr(candidate.Id, "Value") else int(candidate.Id.IntegerValue)
                    if not candidate.IsTemplate and candidate_id not in doomed_ids:
                        uidoc.ActiveView = candidate
                        break
        except Exception:
            pass

    deleted = 0
    transaction = Transaction(document, u"Froglet: видалити види секцій")
    transaction.Start()
    try:
        for view in views:
            try:
                document.Delete(view.Id)
                deleted += 1
            except Exception:
                pass
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise

    stored.remove(target)
    data[key] = stored
    _save(data)
    return deleted