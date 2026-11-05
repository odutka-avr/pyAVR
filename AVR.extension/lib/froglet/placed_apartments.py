# -*- coding: utf-8 -*-
"""Remembers which footprint got which apartment.

Every apartment Froglet places is recorded as (footprint roof id, placed model
group, apartment name) per Revit document, in %APPDATA%\\DKUI. The records are
used to keep footprints that already have an apartment transparent (even when
the geometry check cannot see it) and are re-validated on every read: a record
whose group has been deleted is dropped, so deleting an apartment frees its
footprint again.
"""

import io
import json
import os

_PATH = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "DKUI", "placed_apartments.json"
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


def record(document, group, roof_id, name, rvt_path, rooms=None, z=None, pending_tags=None, flip=None, origin=None):
    """Remember that `group` (just placed) sits on footprint `roof_id`. A
    footprint holds one apartment, so an older record for the same roof is
    replaced. `origin` "copy" marks an apartment that came with a copied floor (floor_copy) rather
    than being placed by the user: a floor made of such apartments is not an original laid-out floor."""
    data = _load()
    entries = data.setdefault(_key(document), [])

    entries[:] = [e for e in entries if e.get("groupUid") != group.UniqueId]
    if roof_id is not None:
        entries[:] = [e for e in entries if e.get("roofId") != roof_id]

    entry = {
        "roofId": roof_id,
        "groupUid": group.UniqueId,
        "name": name or u"",
        "rvtPath": rvt_path or u"",
    }
    if origin:
        entry["origin"] = origin
    if pending_tags:
        # Room tags that could not be made yet (the rooms were not enclosed):
        # {"metas": [...], "rotation": radians, "mirror": [x, y] or None, "levelId": int}
        entry["pendingTags"] = pending_tags
    if rooms:
        # [{"t": room type, "p": [[x, y], ...] feet}] as placed, and the floor
        # height (feet): lets the next apartment tell what lies next to it.
        entry["rooms"] = rooms
        entry["z"] = z
        if flip:
            # [nx, ny, ox, oy]: the plane (through the apartment's centre,
            # perpendicular to its outside side) it may still be mirrored about
            # - see apartment_group_placement.rebalance_neighbours.
            entry["flip"] = flip
    entries.append(entry)
    _save(data)


def live_entries(document):
    """The records whose group still exists in `document`, each with the
    group's current element id. Dead records are pruned from the file."""
    data = _load()
    stored = data.get(_key(document), [])

    alive, result = [], []
    for entry in stored:
        try:
            group = document.GetElement(entry.get("groupUid"))
        except Exception:
            group = None
        if group is None:
            continue

        alive.append(entry)
        try:
            group_id = int(group.Id.Value)
        except Exception:
            group_id = int(group.Id.IntegerValue)
        result.append(dict(entry, groupId=group_id))

    if len(alive) != len(stored):
        data[_key(document)] = alive
        try:
            _save(data)
        except Exception:
            pass

    return result


def occupied_roof_ids(document, visible_group_ids=None):
    """Footprint (roof) ids that have a remembered, still existing apartment;
    with `visible_group_ids`, only apartments the active view can see."""
    roofs = set()
    for entry in live_entries(document):
        if entry.get("roofId") is None:
            continue
        if visible_group_ids is not None and entry["groupId"] not in visible_group_ids:
            continue
        roofs.add(entry["roofId"])
    return roofs


def live_rooms(document, z, tolerance_ft=1.5):
    """[(room type, polygon in feet)] of every remembered, still existing
    apartment on the floor at height `z`."""
    rooms = []
    for entry in live_entries(document):
        entry_z = entry.get("z")
        if not entry.get("rooms") or entry_z is None or abs(entry_z - z) > tolerance_ft:
            continue
        for room in entry["rooms"]:
            rooms.append((room["t"], [tuple(point) for point in room["p"]]))
    return rooms


# ---- stair/lift cores placed next to МЗК roofs ("cores" in the same file) ----

def record_core(document, mzk_roof_id, group_uid):
    """Remember that a stair core was placed on МЗК roof `mzk_roof_id`."""
    data = _load()
    cores = data.setdefault(_key(document) + u"::cores", [])
    cores[:] = [c for c in cores if c.get("roofId") != mzk_roof_id]
    cores.append({"roofId": mzk_roof_id, "groupUid": group_uid})
    _save(data)


def live_cores(document):
    """{МЗК roof id: group uid} of the cores that still exist."""
    data = _load()
    key = _key(document) + u"::cores"
    stored = data.get(key, [])
    alive = []
    for core in stored:
        try:
            exists = document.GetElement(core.get("groupUid")) is not None
        except Exception:
            exists = False
        if exists:
            alive.append(core)
    if len(alive) != len(stored):
        data[key] = alive
        try:
            _save(data)
        except Exception:
            pass
    return dict((core["roofId"], core["groupUid"]) for core in alive)


# ---- floor slabs ("перекриття", floor_slab) made per level ("slabs" in the same file) ----

def record_slabs(document, level_id, slab_uids):
    """Remember the slabs (unique ids) made on level `level_id`, replacing the earlier ones."""
    data = _load()
    slabs = data.setdefault(_key(document) + u"::slabs", [])
    slabs[:] = [s for s in slabs if s.get("levelId") != level_id]
    if slab_uids:
        slabs.append({"levelId": level_id, "uids": list(slab_uids)})
    _save(data)


def live_slabs(document):
    """{level id: [slab unique ids]} of the slabs that still exist."""
    data = _load()
    key = _key(document) + u"::slabs"
    result = {}
    for item in data.get(key, []):
        for uid in item.get("uids", []):
            try:
                exists = document.GetElement(uid) is not None
            except Exception:
                exists = False
            if exists:
                result.setdefault(item["levelId"], []).append(uid)
    return result


def set_pending_tags(document, group_uid, pending_tags):
    """Replace (or, with None, clear) the pending room tags of a placed apartment."""
    data = _load()
    for entry in data.get(_key(document), []):
        if entry.get("groupUid") == group_uid:
            if pending_tags:
                entry["pendingTags"] = pending_tags
            else:
                entry.pop("pendingTags", None)
    _save(data)

def update_rooms(document, group_uid, rooms):
    """Replace the remembered rooms of a placed apartment (after it was mirrored)."""
    data = _load()
    for entry in data.get(_key(document), []):
        if entry.get("groupUid") == group_uid:
            entry["rooms"] = rooms
    _save(data)