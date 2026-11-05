# -*- coding: utf-8 -*-
"""
Shared in-process bridge between the "Банк квартир" apartment-bank
window and other pyRevit commands in this extension (currently "Вибір
плями квартири") that want to push data into its filters while it is
open, or launch it if it's not - and also carries the placement
(level/position/rotation) that "Вибір плями квартири" computed from the
picked footprint, for "Запустити" to consume later when it copies the
selected apartment's group into the model (see apartment_group_placement.py).

State is stored in System.AppDomain.CurrentDomain's data slots, not in
this module's own Python globals. Different pyRevit pushbuttons/bundles
can each get their own separate IronPython engine (and therefore their
own separate sys.modules - two "copies" of this very module, each with
its own independent globals), so a plain module-level variable set by
one button's engine would simply be invisible to another's. The
AppDomain, on the other hand, is a single CLR-level object shared by
every engine running in this Revit process, regardless of which script
or interpreter is executing - so SetData/GetData here really is shared,
where plain Python globals were not.

Meaningless across Revit sessions (a fresh Revit process has a fresh
AppDomain), and does nothing if the apartment-bank window isn't
currently open.

Trimmed for the WPF rewrite (dropped the WebView2 CoreWebView2
registration/message-posting/handler-rewiring slots that only existed to
work around WebView2-specific event delivery quirks - see
apartment_bank_window.py's own module docstring for the full context).
"""

import json

import clr

clr.AddReference("System")

from System import AppDomain  # noqa: E402

_PENDING_PAYLOAD_SLOT = "DKUI_PendingFootprintPayloadJson_v1"
_LAST_PLACEMENT_SLOT = "DKUI_LastApartmentPlacementJson_v1"
_PERSISTENT_WINDOW_SLOT = "DKUI_PersistentApartmentBankWindow_v1"
_PERSISTENT_PLACEMENT_EVENT_SLOT = "DKUI_PersistentPlacementExternalEvent_v1"
_PENDING_RUN_REQUEST_SLOT = "DKUI_PendingRunRequestJson_v1"
_DEBUG_SIDE_LINE_IDS_SLOT = "DKUI_DebugSideLineElementIdsJson_v1"


_slot_suffix = [""]


def set_slot_suffix(suffix):
    """Names this engine's data slots apart from the other button's, so "Froglet (моноліт)"
    gets its own window and placement event instead of reusing (and running the code of)
    the window the backend-using button created."""
    _slot_suffix[0] = suffix or ""


class _Slots(object):
    def GetData(self, name):
        return AppDomain.CurrentDomain.GetData(name + _slot_suffix[0])

    def SetData(self, name, value):
        AppDomain.CurrentDomain.SetData(name + _slot_suffix[0], value)


def _domain():
    return _Slots()


_FLOOR_REGION_MAP_SLOT = "DKUI_FloorRegionToRoofJson_v1"


def set_floor_region_map(region_to_roof):
    """{filled region id: footprint roof id} while "Розпланування поверху" is
    active (None/empty otherwise). Lets the single-footprint pick accept a
    click on a grey/green region as a click on the footprint under it."""
    try:
        text = json.dumps(dict((str(k), v) for k, v in region_to_roof.items())) if region_to_roof else None
    except Exception:
        text = None

    _domain().SetData(_FLOOR_REGION_MAP_SLOT, text)


def get_floor_region_map():
    text = _domain().GetData(_FLOOR_REGION_MAP_SLOT)
    if not text:
        return {}

    try:
        return dict((int(k), v) for k, v in json.loads(text).items())
    except Exception:
        return {}


def get_persistent_window():
    """The one apartment-bank window (ApartmentSelectorWindow) created
    this Revit session, if any - reused (shown/hidden) rather than
    recreated on every "Select Apartment"/"Вибір плями квартири" click,
    so whatever the user currently has open/selected survives a footprint
    pick pushing new data into it. A genuine CLR object reference, not
    JSON - safe to share this way across the extension's separate
    per-bundle IronPython engines (see this module's own docstring)."""
    return _domain().GetData(_PERSISTENT_WINDOW_SLOT)


def set_persistent_window(window):
    _domain().SetData(_PERSISTENT_WINDOW_SLOT, window)


def get_persistent_placement_event():
    """The current ExternalEvent for placing an apartment group, if any.

    ExternalEvent.Create() - like Transaction.Start() - can only be
    called from a valid Revit API context (a script's own Execute(), or
    an ExternalEvent's own Execute() callback); calling it from inside a
    button Click handler throws. show_apartment_selector() creates this
    fresh every time it runs (a guaranteed-valid context, since it's
    called directly from the pyRevit script) and stores it here, so the
    "Запустити" button only ever needs to call the plain .NET Raise() on
    an already-created object - safe from any context, the same as
    Show()/Activate() on the persisted window. Re-created on every
    show_apartment_selector() call: its handler's Execute() is Python
    code bound to whichever engine created it, which goes stale once
    that invocation's engine is gone.

    A genuine CLR object reference, not JSON - see get_persistent_window's
    docstring for why that's safe to share this way across engines."""
    return _domain().GetData(_PERSISTENT_PLACEMENT_EVENT_SLOT)


def set_persistent_placement_event(event):
    _domain().SetData(_PERSISTENT_PLACEMENT_EVENT_SLOT, event)


def set_pending_run_request(request):
    """Stash the placement request built when "Запустити" is clicked,
    for the persistent placement ExternalEvent's Execute() callback
    (module-level, reading this fresh rather than a per-click closure -
    see get_persistent_placement_event's docstring) to pick up. Stored
    as a JSON string, not a raw Python dict - see
    set_pending_footprint_payload's docstring for why."""
    try:
        text = json.dumps(request, ensure_ascii=False) if request else None
    except Exception:
        text = None

    _domain().SetData(_PENDING_RUN_REQUEST_SLOT, text)


def pop_pending_run_request():
    domain = _domain()
    text = domain.GetData(_PENDING_RUN_REQUEST_SLOT)
    domain.SetData(_PENDING_RUN_REQUEST_SLOT, None)

    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        return None


def set_pending_footprint_payload(payload):
    """Stash a footprint-parameters payload for the apartment-bank
    window to apply to its filters - either right away (if it's already
    open, applied directly by show_apartment_selector()) or once it's
    finished being built (if this is opening it fresh).

    Stored as a JSON string (a plain .NET String), not a raw Python
    dict/list - those are objects native to whichever IronPython engine
    created them, and reading them back from a different engine isn't
    guaranteed to work the way reading a plain CLR string is.
    """
    try:
        text = json.dumps(payload or {}, ensure_ascii=False)
    except Exception:
        text = None

    _domain().SetData(_PENDING_PAYLOAD_SLOT, text)


def pop_pending_footprint_payload():
    domain = _domain()
    text = domain.GetData(_PENDING_PAYLOAD_SLOT)
    domain.SetData(_PENDING_PAYLOAD_SLOT, None)

    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        return None


def set_last_apartment_placement(payload):
    """Remember where/how to place an apartment group, computed from the
    footprint most recently picked via "Вибір плями квартири" - consumed
    later by "Запустити" in the apartment-bank window (a different
    pushbutton, possibly a different IronPython engine - see this
    module's docstring for why AppDomain data slots are used instead of a
    plain Python global).

    Unlike the pending-footprint-payload slot above, this one is NOT
    cleared on read: the user may pick several different apartments from
    the same footprint-filtered results (or press "Запустити" more than
    once) before picking a new footprint, and each of those should still
    use this same placement. It's only replaced by a fresh call here
    (including with None, to explicitly clear it) when a new footprint is
    picked.

    Stored as a JSON string, not a raw Python dict - see
    set_pending_footprint_payload's docstring for why.
    """
    try:
        text = json.dumps(payload, ensure_ascii=False) if payload else None
    except Exception:
        text = None

    _domain().SetData(_LAST_PLACEMENT_SLOT, text)


def get_last_apartment_placement():
    text = _domain().GetData(_LAST_PLACEMENT_SLOT)

    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        return None


def set_debug_side_line_ids(ids):
    """Remember the ElementIds (plain ints, i.e. ElementId.IntegerValue)
    of the МЗК/exterior-side debug lines "Вибір плями квартири" drew in
    the active view for the most recently picked footprint, so the NEXT
    pick can delete them first instead of letting them pile up one pair
    per pick - see that pushbutton's own _draw_side_debug_lines.

    Stored as a JSON list of ints, not raw ElementId objects - see
    set_pending_footprint_payload's docstring for why plain JSON is used
    for cross-engine-safe values here instead of CLR objects."""
    try:
        text = json.dumps(list(ids or []))
    except Exception:
        text = None

    _domain().SetData(_DEBUG_SIDE_LINE_IDS_SLOT, text)


def pop_debug_side_line_ids():
    domain = _domain()
    text = domain.GetData(_DEBUG_SIDE_LINE_IDS_SLOT)
    domain.SetData(_DEBUG_SIDE_LINE_IDS_SLOT, None)

    if not text:
        return []

    try:
        return json.loads(text)
    except Exception:
        return []


def append_debug_side_line_ids(ids):
    """Add more ids to the same tracked set set_debug_side_line_ids
    uses, without clearing whatever's already there - for
    apartment_group_placement.py's own R+AP depth-measurement debug
    lines (drawn later, after placement, in the same view "Вибір плями
    квартири" already drew its own footprint/МЗК/exterior lines into),
    so the NEXT footprint pick's own cleanup catches those too instead
    of leaving them behind."""
    if not ids:
        return

    domain = _domain()
    text = domain.GetData(_DEBUG_SIDE_LINE_IDS_SLOT)

    try:
        existing = json.loads(text) if text else []
    except Exception:
        existing = []

    try:
        combined = json.dumps(list(existing) + list(ids))
    except Exception:
        return

    domain.SetData(_DEBUG_SIDE_LINE_IDS_SLOT, combined)
