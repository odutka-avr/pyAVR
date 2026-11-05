# -*- coding: utf-8 -*-
"""
DKUI Apartment Selector (pyRevit port) - shared window-launching module.

Native WPF rewrite. This used to host a WebView2 (embedded Chromium)
control running an HTML/CSS/JS app, talking to Python over postMessage.
That whole layer was the source of a long chain of hard-to-diagnose bugs
(re-navigating a reused control hanging almost every time; the window
going permanently deaf to messages after any placement ran, even with a
COM message filter installed to work around it) - all rooted in the same
thing: WebView2 is a separate out-of-process browser talking to this
window over COM/IPC, and that whole failure category doesn't exist for a
plain native WPF UI. See ui.xaml (native controls, no WebView2) and this
module below.

This lives in the extension-level lib folder so more than one entry point can launch it: the button
("Froglet.pushbutton", where ui.xaml and icon.png live) and the footprint picker ("Вибір плями квартири._impl\\script.py",
not a ribbon button), which opens this window (if not already open) after picking an apartment footprint and pushes
that footprint's computed parameters into its filters.
"""

import datetime
import io
import json
import math
import os
import sys
import traceback

from Autodesk.Revit.UI import ExternalEvent, IExternalEventHandler

_LIB_DIR = os.path.dirname(__file__)
_EXTENSION_DIR = os.path.dirname(_LIB_DIR)

# ui.xaml and icon.png live under this pushbutton's own folder - that's
# still the canonical location for those assets, this module just knows
# how to find them.
def _tab_dir(extension_dir):
    """The extension's one "*.tab" folder (its name differs between the two plugins - "Froglet розробка.tab" and
    "Froglet.tab" - so that pyRevit shows them as two tabs)."""
    for name in sorted(os.listdir(extension_dir)):
        if name.endswith(".tab") and os.path.isdir(os.path.join(extension_dir, name)):
            return os.path.join(extension_dir, name)
    return os.path.join(extension_dir, "Froglet.tab")


BUNDLE_DIR = os.path.join(
    _tab_dir(_EXTENSION_DIR),
    "Вибір планування.panel",
    "Froglet.pushbutton",
)

LIB_DIR = os.path.join(BUNDLE_DIR, "lib")

# The footprint picker (Revit PickObject flow) lives in its own folder,
# renamed off ".pushbutton" so pyRevit does not show it as a ribbon item.
_FOOTPRINT_PICKER_PATH = os.path.join(
    os.path.dirname(BUNDLE_DIR), "Вибір плями квартири._impl", "script.py"
)

CONFIG_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "DKUI"
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
_DEBUG_LOG_PATH = os.path.join(CONFIG_DIR, "webview_debug.log")

# Far enough off any real monitor that Windows' "keep some part of a
# window on-screen" repositioning heuristics (which only kick in for
# windows just barely off-screen) never apply. Used to "close" this
# window without actually closing it - see _on_window_closing's own
# comment for why: a genuinely persistent window is much simpler and
# faster to bring back than rebuilding one from scratch, and there's no
# WebView2 control here anymore whose lifecycle that could ever
# jeopardize.
_OFFSCREEN_COORDINATE = -32000

if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)
if _LIB_DIR not in sys.path:
    sys.path.append(_LIB_DIR)

import apartment_details  # noqa: E402
import apartment_scan  # noqa: E402
import apartment_shapes  # noqa: E402
import backend_api  # noqa: E402
import froglet_settings  # noqa: E402
import facade_fit  # noqa: E402
import layout_orientation  # noqa: E402
import ui_throttle  # noqa: E402

try:
    import dkui_bridge  # noqa: E402
except Exception:
    dkui_bridge = None

try:
    import apartment_group_placement  # noqa: E402
except Exception:
    apartment_group_placement = None

from pyrevit import forms, script  # noqa: E402

output = script.get_output()
logger = script.get_logger()

try:
    _text_type = unicode  # noqa: F821 -- Python 2 / IronPython 2.7
except NameError:
    _text_type = str


def _debug_log(message, visible=False):
    """Diagnostic logging that can't silently vanish, unlike
    output.print_md().

    pyrevit.script.get_output() returns a PyRevitOutputWindow whose
    .window property is `return EXEC_PARAMS.window_handle` - re-resolved
    LIVE on every single call, not cached once at import time. pyRevit
    opens a brand new output console window for every fresh invocation
    of a command, and this module can log from a callback (ExternalEvent
    Execute, a button click on a window shown long after the script that
    created it returned) that fires well after that. This file is
    unaffected by any of that - always written here for anything worth
    being able to trust, regardless of `visible`.

    `visible=True` ALSO echoes to pyRevit's own output console
    (output.print_md) - opt-in, not the default, because that console
    popping up (or an existing one stealing focus) on essentially every
    click - opening the window, reusing it, a footprint pick, a priority
    reorder - turned out to be pure noise for routine UI interactions;
    the one thing worth a visible pop-up is the actual placement result,
    which passes this explicitly."""
    try:
        apartment_scan.ensure_dir(CONFIG_DIR)
        with io.open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(
                u"{} {}\n".format(
                    datetime.datetime.now().strftime("%H:%M:%S.%f"),
                    message,
                )
            )
    except Exception:
        pass

    if visible:
        try:
            output.print_md(message)
        except Exception:
            pass


def _dump_json_text(data, **kwargs):
    """json.dumps(...) that always returns a text (unicode) string."""
    text = json.dumps(data, **kwargs)
    if not isinstance(text, _text_type):
        text = text.decode("utf-8")
    return text


def _write_text_file(path, text):
    with io.open(path, "w", encoding="utf-8") as text_file:
        text_file.write(text)


# ============================================================
# CONFIG (image folder + UI state - filters, sort/card/page size,
# priority order - one JSON file, replaces the old WebView2-era
# localStorage keys 1:1)
# ============================================================

def load_config():
    try:
        with io.open(CONFIG_PATH, "r", encoding="utf-8") as config_file:
            return json.load(config_file)
    except Exception:
        return {}


def save_config(config):
    try:
        apartment_scan.ensure_dir(CONFIG_DIR)
        _write_text_file(
            CONFIG_PATH,
            _dump_json_text(config, ensure_ascii=False, indent=2),
        )
    except Exception as error:
        logger.warning("Не вдалося зберегти конфігурацію DKUI: %s", error)


def resolve_image_folder():
    """The apartment pool - a folder with many category subfolders, each with its own apartment PNGs (and .rvt, .json):
    `apartments_pool` in settings.txt."""
    return apartment_scan.clean_folder_path(froglet_settings.get("apartments_pool"))


def _scan_apartments(folder_path):
    """Scan the folder (and every subfolder inside it, at any depth -
    see apartment_scan.build_apartment_records's own recursive=True)
    and return (records, folder_state, errors) - the native-UI
    equivalent of the old build_boot_payload, minus the WebView2 boot-
    payload/thumbnail-server plumbing. The apartment catalog is the window's own business
    (it is what the window shows), so it is read here, not by the backend."""
    if not folder_path:
        return [], "not-selected", []

    if not os.path.isdir(folder_path):
        return [], "invalid-folder", ["Папку не знайдено: {}".format(folder_path)]

    try:
        records, errors = apartment_scan.build_apartment_records(folder_path, recursive=True)
    except Exception as error:
        return [], "read-error", ["Не вдалося прочитати папку: {}".format(error)]

    return records, "valid-folder", errors


# ============================================================
# THUMBNAILS - direct BitmapImage decode, no HTTP server
#
# The old thumbnail_server.py existed only to give WebView2's <img> tags
# something to fetch over HTTP (JPEG re-encode + disk cache to keep page
# payload small). A WPF Image control can decode a downscaled bitmap
# straight from the original PNG via BitmapImage.DecodePixelWidth - same
# "don't decode full-res for a small thumbnail" win, no HTTP/JPEG/disk-
# cache machinery needed. Cached in memory only (per Revit session), the
# same (mtime, size) signature apartment_scan.py already computes as the
# staleness key.
# ============================================================

_thumbnail_cache = {}


def _load_bitmap(path, decode_pixel_width=None):
    cache_key = None

    try:
        signature = apartment_scan.source_signature(path)
        cache_key = (path, signature, decode_pixel_width)
    except Exception:
        pass

    if cache_key is not None and cache_key in _thumbnail_cache:
        return _thumbnail_cache[cache_key]

    try:
        from System import Uri
        from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption

        bitmap = BitmapImage()
        bitmap.BeginInit()
        bitmap.UriSource = Uri(path)
        if decode_pixel_width:
            bitmap.DecodePixelWidth = int(decode_pixel_width)
        bitmap.CacheOption = BitmapCacheOption.OnLoad
        bitmap.EndInit()
        bitmap.Freeze()
    except Exception as error:
        logger.warning("DKUI: не вдалося завантажити зображення %s: %s", path, error)
        return None

    if cache_key is not None:
        _thumbnail_cache[cache_key] = bitmap

    return bitmap


def _decode_bitmap(path, decode_pixel_width):
    """The frozen, downscaled bitmap of a picture, or None. No caching here (see _thumbnails)."""
    try:
        from System import Uri
        from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption

        bitmap = BitmapImage()
        bitmap.BeginInit()
        bitmap.UriSource = Uri(path)
        if decode_pixel_width:
            bitmap.DecodePixelWidth = int(decode_pixel_width)
        bitmap.CacheOption = BitmapCacheOption.OnLoad
        bitmap.EndInit()
        bitmap.Freeze()
        return bitmap
    except Exception as error:
        logger.warning("DKUI: не вдалося завантажити зображення %s: %s", path, error)
        return None


def _run_in_thread_pool(work):
    from System.Threading import ThreadPool, WaitCallback

    def _worker(state):
        try:
            work()
        except Exception:
            pass

    ThreadPool.QueueUserWorkItem(WaitCallback(_worker))


# The pictures of the cards (and of an open apartment's tab): remembered per session, asked for the
# file share only in the background, never loaded twice at once and not loaded at all once the card
# that wanted them has been rebuilt (see ui_throttle.ThumbnailLoader).
_thumbnails = ui_throttle.ThumbnailLoader(apartment_scan.source_signature, _decode_bitmap, _run_in_thread_pool)


def _load_bitmap_async(path, decode_pixel_width, image, sticky=False):
    """Set `image.Source` without blocking the UI thread: a picture already loaded is applied
    immediately (without even looking at the file), otherwise the (often network-share) decode runs
    on a thread-pool thread and the frozen bitmap is handed back to the UI thread. Keeps page
    switches instant - images fill in as they load."""
    from System import Action
    from System.Windows.Threading import DispatcherPriority

    dispatcher = image.Dispatcher

    def _apply(bitmap):
        try:
            image.Source = bitmap
        except Exception:
            pass        # (runs on the dispatcher: an exception here would be unhandled)

    def _post(callback):
        dispatcher.BeginInvoke(DispatcherPriority.Background, Action(callback))

    _thumbnails.request(path, decode_pixel_width, _apply, _post, sticky=sticky)


# ============================================================
# EXTERNAL EVENT (document-modifying calls from a modeless window)
# ============================================================

class _CallbackExternalEventHandler(IExternalEventHandler):
    """Generic IExternalEventHandler that just calls a Python callable.

    Revit only allows starting a Transaction from inside a proper API
    context - either IExternalCommand.Execute itself, or an
    IExternalEventHandler.Execute callback. A modeless WPFWindow's own
    event handlers (button clicks) run on the UI thread but OUTSIDE that
    context: TaskDialog.Show/forms.alert work fine from them (UI-only, no
    document access), but any call that touches the document - like
    apartment_group_placement's Transactions - raises "Starting a
    transaction from an external application running outside of API
    context is not allowed." ExternalEvent.Raise() marshals the callback
    onto Revit's own API thread/context instead.
    """

    def __init__(self, callback, name):
        self._callback = callback
        self._name = name

    def Execute(self, uiapp):
        # Revit swallows whatever this returns/raises - it never surfaces
        # in the UI on its own, so every stage is logged VISIBLY
        # (print_md, not just logger) rather than assuming a silent
        # success.
        _debug_log("DKUI: ExternalEvent '{}' Execute() invoked.".format(self._name))
        try:
            # Revit's own messages do not disturb the run (revit_quiet: failures settled, dialogs answered with OK).
            try:
                import revit_quiet
                quiet = revit_quiet.quiet(uiapp)
            except Exception:
                quiet = None
            if quiet is None:
                self._callback(uiapp)
            else:
                with quiet:
                    self._callback(uiapp)
        except Exception as error:
            _debug_log(
                "DKUI: зовнішня подія '{}' завершилася помилкою:\n```\n{}\n```".format(
                    self._name, traceback.format_exc()
                )
            )
            logger.error(
                "DKUI: зовнішня подія '{}' завершилася помилкою: {}".format(
                    self._name, error
                )
            )

    def GetName(self):
        return self._name


def _run_footprint_pick(uiapp):
    """ExternalEvent callback for the "Вибір плями квартири" tab button:
    PickObject needs a valid Revit API context, which a modeless window
    click does not have. The picker module is (re)loaded by path each time
    so it binds to the CURRENT document/uidoc."""
    import imp

    picker = imp.load_source("froglet_footprint_picker", _FOOTPRINT_PICKER_PATH)
    _selected_group["placements"] = []
    picker.pick_apartment_footprint()


def _run_floor_plan_creation(uiapp):
    """ExternalEvent callback for "Вибір поверху та плям квартир"."""
    import floor_plan_creation

    floor_plan_creation.create_typical_floor_plan(uiapp)


def _refresh_created_views(window, uiapp):
    uidoc = uiapp.ActiveUIDocument
    _fill_created_views(window, uidoc.Document if uidoc is not None else None)
    try:
        _refresh_placed_apartments(window, uiapp)
    except Exception:
        logger.warning("DKUI: не вдалося оновити розміщені квартири: %s", traceback.format_exc())


def _set_groups_message(window, text):
    from System.Windows import Thickness, Visibility
    from System.Windows.Controls import TextBlock

    window.AvailableGroupsPanel.Visibility = Visibility.Visible
    window.AvailableGroupsList.Items.Clear()
    window._schema = {}
    window._footprint_group = {}
    _align_scheme_buttons(window, 0.0)
    try:
        from System.Windows import Visibility as _Visibility

        window.SelectTypeButton.Visibility = _Visibility.Collapsed
        window.AutoPickChosenButton.Visibility = _Visibility.Collapsed
        window.ClearChoiceButton.Visibility = _Visibility.Collapsed
        window.SchemeTypesList.Items.Clear()
        window._type_lines = {}
        _show_scheme_columns(window, False)
    except Exception:
        pass
    label = TextBlock()
    label.Text = text
    label.Style = window.FindResource("BodyTextStyle")
    window.AvailableGroupsList.Items.Add(label)


_OCCUPIED_Z_TOLERANCE_FT = 3.0


def _group_probe(group):
    """(x, y, z) of a model group that has walls - the length-weighted
    average of its wall midpoints (reliably inside the apartment even for
    L-shapes) at its lowest wall - or None for a group without walls."""
    from Autodesk.Revit.DB import Wall

    document = group.Document
    sum_x = sum_y = total = 0.0
    lowest = None

    for member_id in group.GetMemberIds():
        element = document.GetElement(member_id)
        if not isinstance(element, Wall):
            continue

        try:
            curve = element.Location.Curve
            length = curve.Length
            middle = curve.Evaluate(0.5, True)
            bbox = element.get_BoundingBox(None)
        except Exception:
            continue

        if length < 1e-6:
            continue

        sum_x += middle.X * length
        sum_y += middle.Y * length
        total += length
        if bbox is not None and (lowest is None or bbox.Min.Z < lowest):
            lowest = bbox.Min.Z

    if total < 1e-6 or lowest is None:
        return None
    return (sum_x / total, sum_y / total, lowest)


def _compute_occupied(document, items, view=None):
    """Roof ids (of `items`, the view's footprints) that already have an
    apartment placed on them: a model group with walls whose wall centre lies
    inside the footprint polygon at about the same height. With `view`, only
    groups VISIBLE in that view count - so apartments that live in a design
    option the view is not showing don't mark footprints as occupied."""
    import apartment_groups
    from Autodesk.Revit.DB import FilteredElementCollector, Group

    point_in_polygon = apartment_groups._picker()._point_in_polygon

    probes = []
    visible_group_ids = set()
    collector = (
        FilteredElementCollector(document, view.Id) if view is not None
        else FilteredElementCollector(document)
    )
    for group in collector.OfClass(Group):
        try:
            if group.ViewSpecific:
                continue
            visible_group_ids.add(
                int(group.Id.Value) if hasattr(group.Id, "Value") else int(group.Id.IntegerValue)
            )
            probe = _group_probe(group)
        except Exception:
            continue
        if probe is not None:
            probes.append(probe)

    # Footprints Froglet remembers an (existing, visible) apartment for.
    occupied = set()
    try:
        import placed_apartments
        item_ids = set(item["id"] for item in items)
        for roof_id in placed_apartments.occupied_roof_ids(document, visible_group_ids):
            if roof_id in item_ids:
                occupied.add(roof_id)
    except Exception:
        pass

    for item in items:
        footprint = item["footprint"]
        z = item.get("z")
        for x, y, probe_z in probes:
            if z is not None and abs(probe_z - z) > _OCCUPIED_Z_TOLERANCE_FT:
                continue
            if point_in_polygon((x, y), footprint):
                occupied.add(item["id"])
                break

    return occupied


def _occupancy_changed(window, uiapp):
    """API-context handler: an apartment group was added/deleted/moved, so
    re-check which footprints have apartments on them and update the list and
    the 90 % transparency accordingly."""
    uidoc = uiapp.ActiveUIDocument
    view_items = getattr(window, "_view_items", None)
    if uidoc is None or not view_items:
        return

    view = uidoc.ActiveView
    view_id = int(view.Id.Value) if hasattr(view.Id, "Value") else int(view.Id.IntegerValue)
    if view_id != view_items[0]:
        return

    occupied = _compute_occupied(uidoc.Document, view_items[1], view)
    previous = getattr(window, "_occupied", None)
    if previous and previous[0] == view_id and previous[1] == occupied:
        return

    _refresh_available_groups(window, uiapp, items=view_items[1])


def _document_change_touches_groups(args):
    """True if a DocumentChanged event deleted anything or added/modified a
    model group (placing, deleting or moving an apartment)."""
    from Autodesk.Revit.DB import Group

    try:
        if args.GetDeletedElementIds().Count:
            return True
    except Exception:
        pass

    document = args.GetDocument()
    for id_list in (args.GetAddedElementIds(), args.GetModifiedElementIds()):
        for element_id in id_list:
            try:
                if isinstance(document.GetElement(element_id), Group):
                    return True
            except Exception:
                continue
    return False


def _sync_floor_footprints(window, view_id, items, occupied):
    """While the floor layout is going on an apartment may be placed or deleted: the footprints left to fill change
    with it (a footprint freed by a deleted apartment can be chosen, confirmed and picked by "Автоматичний вибір";
    one that got an apartment drops out). Only for the plan the layout is on."""
    floor = getattr(window, "_floor", None)
    if not floor or floor["viewId"] != view_id:
        return
    still_free = set(item["id"] for item in items if item["id"] not in occupied)
    if still_free != floor["footprints"]:
        floor["footprints"] = still_free
        _refresh_floor_buttons(window)


def _follow_floor_to_view(window, view_id):
    """The floor-layout state (the apartments chosen so far, the footprints still to fill) belongs to ONE plan. When another
    plan becomes the active view the state of the old one is put aside (it comes back with its plan) and the layout starts
    again for the new one - so the scheme, the automatic choice and "Запустити" always work on the plan that is open."""
    floor = getattr(window, "_floor", None)
    aside = getattr(window, "_floor_aside", None)
    if aside is None:
        aside = window._floor_aside = {}
    changed = False
    if floor and floor["viewId"] != view_id:
        aside[floor["viewId"]] = floor
        window._floor = None
        window._chosen_ids = None
        window._chosen_group_number = None
        _selected_group["placements"] = []
        floor, changed = None, True
    if floor is None and view_id in aside:
        window._floor = aside.pop(view_id)
        changed = True
    if changed:
        _refresh_floor_buttons(window)


def _read_scheme_lines(document, view):
    """([МЗК outlines], [stair/lift node outlines]) of the floor, in feet; empty ones when they cannot be read."""
    import placement_order

    lines = []
    for read in (placement_order.mzk_polygons, placement_order.stair_core_polygons):
        try:
            lines.append(read(document, view))
        except Exception:
            _debug_log(u"DKUI: лінії схеми не прочитано:\n```\n{}\n```".format(traceback.format_exc()))
            lines.append([])
    return lines[0], lines[1]


def _refresh_available_groups(window, uiapp, items=None):
    """Show "Доступні групи квартир:" - the footprints of the active view,
    grouped - when the active view is one Froglet created; hide it
    otherwise. Footprints that already have an apartment placed on them (or,
    in floor-layout mode, a confirmed one) are left out. `items` (all
    footprints of the active view, if already known) skips re-reading them
    from Revit. Runs in an API context."""
    from System.Windows import FontWeights, TextWrapping, Thickness, Visibility
    from System.Windows.Controls import TextBlock

    import apartment_groups
    import created_views

    uidoc = uiapp.ActiveUIDocument
    view = uidoc.ActiveView if uidoc is not None else None
    window._placement_cache = {}            # (the plan may have changed: the placements are read again when asked for)
    window._scheme_choice = None

    created = view is not None and created_views.is_created(uidoc.Document, view)

    if not created:
        _set_groups_message(window, u"Не відкрито жодного виду")
        window._groups_data = []
        window._chosen_ids = None
        _follow_stage(window, False, (), ())
        return

    _set_groups_message(window, u"Обчислення…")

    view_id = int(view.Id.Value) if hasattr(view.Id, "Value") else int(view.Id.IntegerValue)
    _follow_floor_to_view(window, view_id)
    floor = getattr(window, "_floor", None)
    if floor and floor["viewId"] == view_id:
        items = floor["items"]

    if items is None:
        items = apartment_groups.collect_footprints(uiapp, view)

    # Footprints with apartments already placed on them: out of the list and
    # 90 % transparent in the view.
    occupied = _compute_occupied(uidoc.Document, items, view)
    window._view_items = (view_id, items)
    window._occupied = (view_id, occupied)
    _sync_floor_footprints(window, view_id, items, occupied)
    _follow_stage(window, True, [item["id"] for item in items], occupied)
    _auto_start_floor_layout(window, view, items, occupied)

    visible_items = [item for item in items if item["id"] not in occupied]
    floor_filtered = bool(floor) and floor["viewId"] == view_id
    if floor_filtered:
        visible_items = [item for item in visible_items if item["id"] not in floor["assigned"]]

    # Sections are numbered over ALL footprints, so "Секція N" keeps its
    # number while some footprints drop out of the list.
    groups = apartment_groups.group_footprints(visible_items, section_items=items)

    # The list is rebuilt, so nothing in it is chosen any more (this also
    # applies the transparency of the occupied footprints).
    _sync_floor_colors(window, uiapp, [])

    window._groups_data = groups
    window._chosen_ids = None
    _selected_group["placements"] = []
    window._footprint_group = {}
    for number, group in enumerate(groups, 1):
        for member in group["members"]:
            window._footprint_group[member["id"]] = number

    if not items:
        _set_groups_message(window, u"На цьому виді немає плям квартир.")
        window._groups_data = []
        return
    assigned = dict(floor["assigned"]) if floor_filtered else {}
    mzk, cores = _read_scheme_lines(uidoc.Document, view)
    _draw_scheme(window, items, occupied, assigned, mzk, cores, groups)


# ============================================================
# The scheme of the active floor (floor_scheme.py) instead of the list of groups: every footprint is a polygon with its type,
# its area and its nominal sizes; a click chooses its footprint (Ctrl toggles it, Shift adds it, a double click chooses
# the whole group of apartments that suit each other), exactly as the lines of the list did.
# window._schema = {footprint id: (polygon, [texts], fill brush, text brush)}, window._footprint_group = {footprint id: group number}
# ============================================================

_SCHEME_INK = u"#171C21"
_SCHEME_FILLS = {                       # state: (fill, outline, text)
    "free": (u"#E6E9EC", u"#171C21", u"#171C21"),                     # (a free footprint takes the colour of its group)
    "occupied": (u"#C9CDD1", u"#9AA0A6", u"#4A5058"),                 # an apartment is placed on it: grey, "Зайнято"
    "assigned": (u"#8CCB9F", u"#4E9A68", u"#171C21"),                 # an apartment is chosen for it: green (not too bright)
}
# The muted colours of the groups of apartments (the same colour in the list of types and on the footprints of the group).
_SCHEME_GROUP_COLORS = [u"#D6DFE9", u"#E8DED2", u"#DFDAE7", u"#E7D9DE", u"#D6E3DE", u"#E4E1CE", u"#E0D9D0", u"#D9DDE4"]
_SCHEME_DIMMED = (u"#F5F6F7", u"#DDE0E3", u"#C9CDD1")       # a free footprint outside the group of the chosen one
_SCHEME_SIZE_COLOR = u"#6E757C"                              # the sizes and their dimension lines
_SCHEME_SIZE_FONT = 12.0
_SCHEME_MZK_COLOR = u"#8F949C"                               # the МЗК lines (dashed)
_SCHEME_CORE_COLOR = u"#171C21"                              # the stair/lift node lines


def _hex_brush(color):
    from System.Windows.Media import BrushConverter

    return BrushConverter().ConvertFromString(color)


def _scheme_text(window, canvas, text, x, y, size, brush, angle=0.0, max_width=None):
    """A text centred on (x, y) (rotated by `angle` degrees about its centre, cut with "…" when wider than `max_width`); it
    never takes a click (the caller turns it on for a name)."""
    from System import Double
    from System.Windows import Point, Size, TextAlignment, TextTrimming
    from System.Windows.Controls import Canvas, TextBlock
    from System.Windows.Media import RotateTransform

    block = TextBlock()
    block.Text = text
    block.FontFamily = window.FontFamily
    block.FontSize = size
    block.Foreground = brush
    block.TextAlignment = TextAlignment.Center
    block.IsHitTestVisible = False
    if max_width:
        block.MaxWidth = max_width
        block.TextTrimming = TextTrimming.CharacterEllipsis
    block.Measure(Size(Double.PositiveInfinity, Double.PositiveInfinity))
    Canvas.SetLeft(block, x - block.DesiredSize.Width / 2.0)
    Canvas.SetTop(block, y - block.DesiredSize.Height / 2.0)
    Canvas.SetZIndex(block, 20)
    if angle:
        block.RenderTransformOrigin = Point(0.5, 0.5)
        block.RenderTransform = RotateTransform(angle)
    canvas.Children.Add(block)
    return block


def _scheme_line(canvas, start, end, brush, thickness=1.0):
    from System.Windows.Controls import Canvas
    from System.Windows.Shapes import Line

    line = Line()
    line.X1, line.Y1, line.X2, line.Y2 = start[0], start[1], end[0], end[1]
    line.Stroke = brush
    line.StrokeThickness = thickness
    line.IsHitTestVisible = False
    Canvas.SetZIndex(line, 5)
    canvas.Children.Add(line)
    return line


def _scheme_outline(canvas, points, color, thickness, dashed, tooltip):
    """The line of a МЗК roof (dashed) or of a stair/lift node (solid): an outline with no fill, no click."""
    from System.Windows import Point
    from System.Windows.Controls import Canvas
    from System.Windows.Media import DoubleCollection, PointCollection
    from System.Windows.Shapes import Polygon

    outline = Polygon()
    outline.Points = PointCollection()
    for x, y in points:
        outline.Points.Add(Point(x, y))
    outline.Stroke = _hex_brush(color)
    outline.StrokeThickness = thickness
    if dashed:
        dashes = DoubleCollection()
        dashes.Add(5.0)
        dashes.Add(3.0)
        outline.StrokeDashArray = dashes
    outline.ToolTip = tooltip
    outline.IsHitTestVisible = False
    Canvas.SetZIndex(outline, 6)                       # above the footprints (0), under the chosen ones (10)
    canvas.Children.Add(outline)
    return outline


def _show_scheme_columns(window, shown):
    """The buttons and the types (with their headings and dividers) are there while the scheme is."""
    from System.Windows import Visibility

    window._scheme_shown = bool(shown)
    state = Visibility.Visible if shown else Visibility.Collapsed
    for name in ("SchemeSidePanel", "SchemeTypesPanel", "SchemeDividerLeft", "SchemeDividerRight"):
        getattr(window, name).Visibility = state
    _sync_side_message(window)


def _sync_side_message(window):
    """"Всі плями вже розплановані" under "Параметри вибору:" when none of its buttons is shown."""
    from System.Windows import Visibility

    try:
        buttons = (window.AutoPickPlansButton, window.AutoPickChosenButton, window.SelectTypeButton, window.ClearChoiceButton)
        nothing = all(button.Visibility != Visibility.Visible for button in buttons)
        window.SchemeSideEmpty.Visibility = Visibility.Visible if nothing and getattr(window, "_scheme_shown", False) else Visibility.Collapsed
    except Exception:
        pass


def _group_color(number):
    return _SCHEME_GROUP_COLORS[(number - 1) % len(_SCHEME_GROUP_COLORS)]


def _scheme_base_colors(state, group):
    """(fill, outline, text) of a footprint that is not chosen: by its state, a free one by the colour of its group."""
    fill, outline, ink = _SCHEME_FILLS[state]
    if state == "free" and group is not None:
        fill = _group_color(group)
    return fill, outline, ink


def _draw_scheme(window, items, occupied, assigned, mzk=(), cores=(), groups=()):
    """Replace the content of the groups panel by the scheme of `items` (the МЗК and the stair/lift node roofs are lines
    around them); the footprints are coloured by the group of apartments they belong to, and the list of those groups
    (the types) stands beside it."""
    import floor_scheme

    from System.Windows import HorizontalAlignment, Point
    from System.Windows.Controls import Canvas, StretchDirection, Viewbox
    from System.Windows.Input import Cursors
    from System.Windows.Media import PointCollection, Stretch
    from System.Windows.Shapes import Polygon

    group_of = {}
    for number, group in enumerate(groups, 1):
        for member in group["members"]:
            group_of[member["id"]] = number
    layout = floor_scheme.build(items, occupied, set(assigned), mzk_polygons=mzk, core_polygons=cores, group_of=group_of)
    if layout is None:
        _set_groups_message(window, u"На цьому виді немає плям квартир.")
        return

    canvas = Canvas()
    canvas.Width = layout["width"]
    canvas.Height = layout["height"]
    window._schema = {}

    size_brush = _hex_brush(_SCHEME_SIZE_COLOR)
    for regions, color, thickness, dashed, tooltip in (
        (layout["mzk"], _SCHEME_MZK_COLOR, 1.2, True, u"МЗК"),
        (layout["cores"], _SCHEME_CORE_COLOR, 2.0, False, u"Сходово-ліфтовий вузол"),
    ):
        for points in regions:                               # (lines only: they carry no sizes)
            _scheme_outline(canvas, points, color, thickness, dashed, tooltip)

    for shape in layout["shapes"]:
        polygon = Polygon()
        polygon.Points = PointCollection()
        for x, y in shape["points"]:
            polygon.Points.Add(Point(x, y))
        polygon.StrokeThickness = 1.2
        canvas.Children.Add(polygon)

        label_x, label_y = shape["label"]
        caption = u"Зайнято" if shape["state"] == "occupied" else u"{}\n{}".format(shape["title"], shape["area"])
        text = _scheme_text(window, canvas, caption, label_x, label_y, 13.0, size_brush)
        for size in shape["sizes"]:
            _scheme_text(window, canvas, size["text"], size["x"], size["y"], _SCHEME_SIZE_FONT, size_brush, size["angle"])
            _scheme_line(canvas, size["line"][0], size["line"][1], size_brush)
            for tick in size["ticks"]:
                _scheme_line(canvas, tick[0], tick[1], size_brush)
        window._schema[shape["id"]] = {"polygon": polygon, "text": text, "state": shape["state"], "extras": [],
                                       "base": _scheme_base_colors(shape["state"], shape.get("group"))}
        if shape["state"] == "assigned" and assigned.get(shape["id"]):
            # The apartment already chosen for this footprint: its name stands under the area (always), opens the apartment's
            # own tab, and a small cross beside it clears the choice.
            _add_scheme_name(window, canvas, shape, text, assigned[shape["id"]])
        if shape["state"] in ("free", "assigned"):
            polygon.Cursor = Cursors.Hand
            polygon.MouseLeftButtonDown += _make_scheme_handler(window, shape["id"])
    _paint_schema_choice(window, [])

    box = Viewbox()
    box.Stretch = Stretch.Uniform
    box.StretchDirection = StretchDirection.DownOnly
    box.MaxHeight = _SCHEME_MAX_HEIGHT
    box.MaxWidth = _SCHEME_MAX_WIDTH
    _align_scheme_buttons(window, layout["margin"] * min(1.0, _SCHEME_MAX_HEIGHT / layout["height"], _SCHEME_MAX_WIDTH / layout["width"]))
    box.HorizontalAlignment = HorizontalAlignment.Left
    box.Child = canvas
    window.AvailableGroupsList.Items.Clear()
    window.AvailableGroupsList.Items.Add(box)
    _fill_scheme_types(window, groups)
    _show_scheme_columns(window, True)


def _fill_scheme_types(window, groups):
    """"Квартири за типами:": one block per group of apartments - a small circle of the group's colour and what the group is,
    and under it every footprint of the group on its own line ("1к - 57 м²"); a click on a line chooses that footprint on the
    scheme, the next click on it takes the choice off (as a click on the footprint itself)."""
    import floor_scheme

    from System.Windows import GridLength, Thickness, VerticalAlignment, Visibility
    from System.Windows.Controls import Border, ColumnDefinition, Grid, StackPanel, TextBlock
    from System.Windows.Input import Cursors
    from System.Windows.Shapes import Ellipse

    window.SchemeTypesList.Items.Clear()
    window._type_lines = {}
    if not groups:
        message = TextBlock()
        message.Text = u"Всі плями вже розплановані"
        message.Style = window.FindResource("BodyTextStyle")
        window.SchemeTypesList.Items.Add(message)
    for number, group in enumerate(groups, 1):
        circle = Ellipse()
        circle.Width = circle.Height = 12
        circle.Fill = _hex_brush(_group_color(number))
        circle.Stroke = _hex_brush(_SCHEME_SIZE_COLOR)
        circle.StrokeThickness = 0.8
        circle.VerticalAlignment = VerticalAlignment.Center
        circle.Margin = Thickness(0, 0, 10, 0)

        types = sorted(set(floor_scheme.type_label(member["type"]) for member in group["members"]))
        label = TextBlock()
        label.Style = window.FindResource("BodyTextStyle")
        label.VerticalAlignment = VerticalAlignment.Center
        label.Text = u"{}, {}, {} - {} шт.".format(
            u"/".join(types) or u"—",
            _WINDOW_PLACEMENT_FULL_LABELS.get(group["window"], u"—"),
            _ADDED_PART_FULL_LABELS.get(group["added"], u"—"),
            len(group["members"]),
        )
        head = Grid()
        head.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength.Auto))
        head.ColumnDefinitions.Add(ColumnDefinition())
        Grid.SetColumn(label, 1)
        head.Children.Add(circle)
        head.Children.Add(label)

        lines = StackPanel()
        lines.Margin = Thickness(22, 4, 0, 0)
        for member in group["members"]:
            line = TextBlock()
            line.Style = window.FindResource("BodyTextStyle")
            line.Text = u"{} - {} м²".format(floor_scheme.type_label(member["type"]), floor_scheme.round_area(member["area"]))
            line.Foreground = window.FindResource("AvrTextMutedBrush")
            line.Padding = Thickness(6, 1, 6, 1)
            line.Margin = Thickness(0, 0, 0, 2)
            line.Cursor = Cursors.Hand
            line.ToolTip = u"Вибрати цю пляму на схемі"
            line.MouseEnter += _on_view_label_enter
            line.MouseLeave += _on_view_label_leave
            line.MouseLeftButtonDown += _swallow_mouse_event
            line.MouseLeftButtonUp += _make_type_line_handler(window, member["id"])
            lines.Children.Add(line)
            window._type_lines[member["id"]] = (line, line.Foreground)

        block = StackPanel()
        block.Margin = Thickness(0, 0, 0, 16)
        block.Children.Add(head)
        block.Children.Add(lines)
        window.SchemeTypesList.Items.Add(block)


def _make_type_line_handler(window, footprint_id):
    def _handler(sender, args):
        args.Handled = True
        _choose_from_scheme(window, footprint_id)

    return _handler


_SCHEME_MAX_HEIGHT = 520.0       # the scheme is shrunk to this height when it is taller
_SCHEME_MAX_WIDTH = 350.0        # ... and to this width (the buttons and the types stand beside it in a window of about 760)


def _align_scheme_buttons(window, top):
    """The buttons beside the scheme start at the top edge of the plan (below the blank margin that holds the size labels),
    not at the top of the picture."""
    from System.Windows import Thickness

    try:
        window.SchemeSidePanel.Margin = Thickness(12, top, 0, 0)
        window.SchemeTypesPanel.Margin = Thickness(16, top, 0, 0)           # (the types stand a little farther to the right)
        window.SchemeDividerLeft.Margin = Thickness(12, top, 0, 0)          # (the dividers start there too)
        window.SchemeDividerRight.Margin = Thickness(16, top, 0, 0)
    except Exception:
        pass


_SCHEME_NAME_GAP = 10.0          # between the area and the name of the apartment chosen for a footprint


def _add_scheme_name(window, canvas, shape, area_text, info):
    """Under the area of a footprint that has its apartment: the name (opens the apartment's tab) and a small cross beside it
    (clears the choice). The three lines - type, area, name - are centred together on the footprint."""
    from System.Windows.Controls import Canvas

    item = info.get("item") or {}
    name = info.get("displayName") or item.get("displayName") or os.path.basename(info.get("rvtPath") or u"") or u"—"
    xs = [x for x, _ in shape["points"]]
    label_x, label_y = shape["label"]
    area_height = area_text.DesiredSize.Height
    ink = _hex_brush(_SCHEME_INK)

    cross = _scheme_text(window, canvas, u"×", label_x, label_y, _SCHEME_SIZE_FONT + 5.0, _hex_brush(_SCHEME_SIZE_COLOR))
    block = _scheme_text(window, canvas, name, label_x, label_y, _SCHEME_SIZE_FONT, ink,
                         max_width=max(40.0, max(xs) - min(xs) - 34.0))
    name_width, cross_width = block.DesiredSize.Width, cross.DesiredSize.Width
    name_top = label_y + area_height / 2.0 + _SCHEME_NAME_GAP
    unit_left = label_x - (name_width + 4.0 + cross_width) / 2.0
    Canvas.SetLeft(block, unit_left)
    Canvas.SetLeft(cross, unit_left + name_width + 4.0)
    Canvas.SetTop(block, name_top)
    Canvas.SetTop(cross, name_top + block.DesiredSize.Height / 2.0 - cross.DesiredSize.Height / 2.0)
    # all three lines together are centred on the footprint
    lift = (_SCHEME_NAME_GAP + block.DesiredSize.Height) / 2.0
    for part in (area_text, block, cross):
        Canvas.SetTop(part, Canvas.GetTop(part) - lift)

    block.IsHitTestVisible = True
    block.Cursor = _hand_cursor()
    block.ToolTip = u"Відкрити вкладку цієї квартири"
    block.MouseEnter += _on_view_label_enter
    block.MouseLeave += _on_view_label_leave
    block.MouseLeftButtonDown += _swallow_mouse_event
    block.MouseLeftButtonUp += _make_open_apartment_handler(window, item or info)

    cross.IsHitTestVisible = True
    cross.Cursor = _hand_cursor()
    cross.ToolTip = u"Очистити вибір цього планування"
    cross.MouseLeftButtonDown += _swallow_mouse_event
    cross.MouseLeftButtonUp += _make_clear_layout_handler(window, shape["id"])
    window._schema[shape["id"]]["extras"] = [block, cross]


def _make_clear_layout_handler(window, footprint_id):
    def _handler(sender, args):
        args.Handled = True
        _request_clear_layouts(window, [footprint_id])

    return _handler


def _request_clear_layouts(window, footprint_ids):
    """The chosen apartment is forgotten for these footprints (the same as the "Очистити" of the old list)."""
    window._pending_clear_ids = list(footprint_ids)
    if window._floor_clear_event is not None:
        window._floor_clear_event.Raise()


def _clear_chosen_layouts(window):
    """"Очистити вибір": the layouts of the chosen footprints (those that have one)."""
    schema = getattr(window, "_schema", {})
    ids = [footprint_id for footprint_id in (getattr(window, "_chosen_ids", None) or ())
           if schema.get(footprint_id, {}).get("state") == "assigned"]
    if ids:
        _request_clear_layouts(window, ids)


def _make_scheme_handler(window, footprint_id):
    def _handler(sender, args):
        _click_on_scheme(window, footprint_id)
        args.Handled = True

    return _handler


def _click_on_scheme(window, footprint_id):
    """A click on a footprint of the scheme: a free one is chosen / taken off the choice; one that has its apartment is chosen
    the same way (the button "Очистити вибір" then clears the layouts of the chosen ones)."""
    shape = getattr(window, "_schema", {}).get(footprint_id)
    if shape is None:
        return
    if shape["state"] == "assigned":
        _choose_assigned_from_scheme(window, footprint_id)
    elif shape["state"] == "free":
        _choose_from_scheme(window, footprint_id)


def _scheme_choice_now(window):
    """(the footprints chosen on the scheme, the number of their group or None): what the user sees - the last click, even when
    Revit has not caught up with it yet (the choice is painted at once, the rest runs in the API context)."""
    pending = getattr(window, "_scheme_choice", None)
    if pending is not None:
        return set(pending), getattr(window, "_scheme_group", None)
    return set(getattr(window, "_chosen_ids", None) or ()), getattr(window, "_chosen_group_number", None)


def _choose_assigned_from_scheme(window, footprint_id):
    """Footprints that have their apartment are chosen like the free ones (a second click takes the footprint off) - they are
    selected in Revit and their parameters fill the bank filters (`_pick_floor_footprints`), so that another apartment can
    be confirmed for them."""
    schema = getattr(window, "_schema", {})
    chosen, group = _scheme_choice_now(window)
    only_assigned = bool(chosen) and group is None and all(
        schema.get(roof_id, {}).get("state") == "assigned" for roof_id in chosen)
    remaining = _choice_after_click(chosen if only_assigned else (), only_assigned, footprint_id)
    window._scheme_choice, window._scheme_group = set(remaining), None
    _paint_schema_choice(window, remaining)                   # at once; Revit follows
    window._pending_floor_pick_ids = remaining
    window._pending_floor_unpick = not remaining
    if window._floor_pick_event is not None:
        window._floor_pick_event.Raise()


def _choice_after_click(chosen, same_group, footprint_id):
    """The footprints chosen after a click on `footprint_id`: nothing chosen, or another group chosen - just it; the same
    group - it joins the choice, or leaves it when it is chosen already (a second click takes the choice off)."""
    if not same_group:
        return [footprint_id]
    if footprint_id in chosen:
        return [roof_id for roof_id in chosen if roof_id != footprint_id]
    return list(chosen) + [footprint_id]


def _choose_from_scheme(window, footprint_id):
    """A click on a free footprint of the scheme chooses it; the next click on it takes it off again. Footprints of one group
    can be chosen one after another; a footprint of another group starts a new choice."""
    number = getattr(window, "_footprint_group", {}).get(footprint_id)
    if number is None:
        return
    chosen, group = _scheme_choice_now(window)
    same_group = bool(chosen) and group == number
    remaining = _choice_after_click(chosen, same_group, footprint_id)
    window._scheme_choice, window._scheme_group = set(remaining), (number if remaining else None)
    _paint_schema_choice(window, remaining)                   # at once; Revit follows
    window._pending_group_number = number
    window._pending_unchoose = not remaining
    window._pending_member_ids = remaining
    if window._group_select_event is not None:
        window._group_select_event.Raise()


def _paint_schema_choice(window, chosen_ids):
    """The chosen footprints of the scheme are black, with a white outline and white text; while a free one is chosen the
    free footprints that are not of its group go light grey (the apartments of the group stay as they are). Beside the
    scheme: "Вибрати всі плями цього типу" while free footprints are chosen, "Очистити вибір" while footprints that have an
    apartment are."""
    from System.Windows import Visibility
    from System.Windows.Controls import Canvas
    from System.Windows.Media import Brushes

    schema = getattr(window, "_schema", {})
    chosen = set(chosen_ids or ())
    window._scheme_chosen_any = bool(chosen)
    chosen_free = [roof_id for roof_id in chosen if schema.get(roof_id, {}).get("state") == "free"]
    chosen_assigned = [roof_id for roof_id in chosen if schema.get(roof_id, {}).get("state") == "assigned"]
    number = None
    for footprint_id in chosen_free:
        number = getattr(window, "_footprint_group", {}).get(footprint_id)
        if number is not None:
            break
    for footprint_id, shape in schema.items():
        polygon, text, state = shape["polygon"], shape["text"], shape["state"]
        if footprint_id in chosen:
            fill, outline, ink = _hex_brush(_SCHEME_INK), Brushes.White, Brushes.White
        elif state == "free" and number is not None and getattr(window, "_footprint_group", {}).get(footprint_id) != number:
            fill, outline, ink = [_hex_brush(color) for color in _SCHEME_DIMMED]
        else:
            fill, outline, ink = [_hex_brush(color) for color in shape.get("base") or _SCHEME_FILLS[state]]
        polygon.Fill = fill
        polygon.Stroke = outline
        text.Foreground = ink
        for extra in shape.get("extras", []):                       # the name and the cross of an apartment
            extra.Foreground = Brushes.White if footprint_id in chosen else _hex_brush(_SCHEME_INK)
        Canvas.SetZIndex(polygon, 10 if footprint_id in chosen else 0)
    for footprint_id, (line, original) in getattr(window, "_type_lines", {}).items():     # the lines of the types
        line.Background = _hex_brush(_SCHEME_INK) if footprint_id in chosen else Brushes.Transparent
        line.Foreground = Brushes.White if footprint_id in chosen else original
    others_left = False
    groups = getattr(window, "_groups_data", None) or []
    if number is not None and groups and number <= len(groups):
        others_left = any(member["id"] not in chosen for member in groups[number - 1]["members"])
    try:
        window.SelectTypeButton.Visibility = Visibility.Visible if chosen_free and others_left else Visibility.Collapsed
        window.AutoPickChosenButton.Visibility = Visibility.Visible if chosen_free else Visibility.Collapsed
        window.ClearChoiceButton.Visibility = Visibility.Visible if chosen_assigned else Visibility.Collapsed
    except Exception:
        pass
    _sync_run_buttons(window)                  # ("... для решти плям" goes while something is chosen, comes back with none)


def _choose_whole_type(window):
    """"Вибрати всі плями цього типу": the whole group of the chosen footprint."""
    chosen, group = _scheme_choice_now(window)
    number = group
    groups = getattr(window, "_groups_data", None)
    if not number or not groups or number > len(groups) or window._group_select_event is None:
        return
    members = [member["id"] for member in groups[number - 1]["members"]]
    window._scheme_choice, window._scheme_group = set(members), number
    _paint_schema_choice(window, members)                      # at once; Revit follows
    window._pending_group_number = number
    window._pending_member_ids = None
    window._pending_unchoose = False
    window._group_select_event.Raise()


def _transparent_brush():
    from System.Windows.Media import Brushes
    return Brushes.Transparent


def _paint_group_choice(window, number, subset):
    """Show group `number` as chosen on the scheme (the whole group, or just the `subset` footprints); number=None paints
    everything unchosen."""
    chosen = []
    groups = getattr(window, "_groups_data", None)
    if number and groups and number <= len(groups):
        chosen = list(subset) if subset else [member["id"] for member in groups[number - 1]["members"]]
    if not chosen:
        window._scheme_choice = None
    _paint_schema_choice(window, chosen)


def _unchoose_group_if_selection_changed(window, uiapp):
    """Idling hook: once the Revit selection no longer is exactly what the
    chosen group/line selected (Esc, click on empty space, other pick), the
    group goes back to white and its placements are dropped."""
    chosen = getattr(window, "_chosen_ids", None)
    if not chosen:
        return

    uidoc = uiapp.ActiveUIDocument
    if uidoc is None:
        return

    current = set()
    for element_id in uidoc.Selection.GetElementIds():
        try:
            current.add(int(element_id.Value))
        except Exception:
            current.add(int(element_id.IntegerValue))

    if current == chosen:
        return

    window._chosen_ids = None
    _selected_group["placements"] = []
    _paint_group_choice(window, None, None)
    _sync_floor_colors(window, uiapp, [])
    window.StatusText.Text = u""


_DIM_TOLERANCE_START_MM = 500


def _payload_from_members(members, window_code, added_code, placements=None):
    """Bank-filter payload for chosen footprints (dicts with type, area,
    dims): type, window placement, added part, area ±2 m² and the nominal
    size (the two sides, averaged over the footprints) with ±500 mm.

    Рядове footprints whose outside side is known (`placements`: their placement payloads) give their
    FACADE and DEPTH instead of the short and the long side - when they all have one orientation - and
    the layouts are compared with those side for side (facade_fit)."""
    areas = [m["area"] for m in members if m["area"] is not None]
    payload = {
        "windowPlacement": window_code,
        "addedPart": added_code,
        # Outlines of (up to 4 of) the footprints, for "Найбільш підходяще".
        "footprintsFt": [
            [[point[0], point[1]] for point in member["footprint"]]
            for member in members[:4] if member.get("footprint")
        ],
        # Like a single picked footprint: at least 3 suggestions are left.
        "minMatches": 3,
    }
    type_codes = set(str(member["type"]).strip()[:1] for member in members)
    if len(type_codes) == 1 and list(type_codes)[0] in APARTMENT_TYPE_CODES:
        payload["apartmentType"] = list(type_codes)[0]
    if areas:
        payload["areaMin"] = int(round(min(areas) - 2))
        payload["areaMax"] = int(round(max(areas) + 2))
    if members:
        payload["dimWidth"] = int(round(sum(m["dims"][0] for m in members) / len(members)))
        payload["dimLength"] = int(round(sum(m["dims"][1] for m in members) / len(members)))
        payload["dimTolerance"] = _DIM_TOLERANCE_START_MM
        ordered = facade_fit.ordered_dims(members, placements) if window_code == "R" else None
        if ordered and facade_fit.consistent(ordered):
            facade, depth = facade_fit.average(ordered)
            payload["dimWidth"] = int(round(facade))
            payload["dimLength"] = int(round(depth))
            payload["dimOrdered"] = True
            payload["orderedDims"] = [list(dims) for dims in ordered]
    return payload


def _placements_cached(window, uiapp, view, element_ids):
    """`apartment_groups.compute_placements` for `element_ids`, but every footprint only once while the view stays as it is
    (a click on the scheme re-read the roofs and asked the backend each time). The cache goes with every
    `_refresh_available_groups` (a new view, a changed plan)."""
    import copy

    import apartment_groups

    cache = getattr(window, "_placement_cache", None)
    if cache is None:
        cache = window._placement_cache = {}
    missing = [roof_id for roof_id in element_ids if roof_id not in cache]
    if missing:
        computed = apartment_groups.compute_placements(uiapp, view, missing)
        by_roof = dict((placement.get("roofId"), placement) for placement in computed if placement.get("roofId") is not None)
        for roof_id in missing:
            cache[roof_id] = by_roof.get(roof_id)
    return [copy.deepcopy(cache[roof_id]) for roof_id in element_ids if cache.get(roof_id) is not None]


def _select_group(window, uiapp):
    """Select every footprint of the chosen group in Revit, compute their
    placements (so "Запустити" can place an apartment on each one) and push
    the group's parameters into the Банк квартир filters."""
    from System.Collections.Generic import List
    from System.Windows import Thickness
    from System.Windows.Media import Brushes
    from Autodesk.Revit.DB import ElementId

    import apartment_groups

    number = getattr(window, "_pending_group_number", None)
    groups = getattr(window, "_groups_data", None)
    if not number or not groups or number > len(groups):
        return

    group = groups[number - 1]
    uidoc = uiapp.ActiveUIDocument
    view = uidoc.ActiveView
    subset = getattr(window, "_pending_member_ids", None)
    window._pending_member_ids = None

    if getattr(window, "_pending_unchoose", False):
        # Ctrl took the last footprint off the choice: nothing is chosen any more.
        window._pending_unchoose = False
        window._chosen_ids = None
        window._chosen_group_number = None
        if getattr(window, "_scheme_choice", None) == set():
            window._scheme_choice = None
        _selected_group["placements"] = []
        try:
            uidoc.Selection.SetElementIds(List[ElementId]())
        except Exception:
            pass
        _paint_group_choice(window, None, None)
        _sync_floor_colors(window, uiapp, [])
        window.StatusText.Text = u""
        return
    members = (
        [member for member in group["members"] if member["id"] in subset]
        if subset else group["members"]
    )
    member_ids = [member["id"] for member in members]

    ids = List[ElementId]()
    for member_id in member_ids:
        ids.Add(ElementId(member_id))
    uidoc.Selection.SetElementIds(ids)

    _selected_group["placements"] = _placements_cached(window, uiapp, view, member_ids)

    window._chosen_ids = set(member_ids)
    window._chosen_group_number = number
    if getattr(window, "_scheme_choice", None) == set(member_ids):          # (no newer click: Revit has caught up)
        window._scheme_choice = None
    # (the whole group chosen is not a "subset" for the painting of the lines)
    _paint_group_choice(window, number, None if len(members) == len(group["members"]) else subset)
    _sync_floor_colors(window, uiapp, member_ids)

    payload = _payload_from_members(
        members, group["window"], group["added"], _selected_group["placements"]
    )
    _defer_footprint_payload(window, payload)

    window.StatusText.Text = u"Обрано групу {}: {} плям. Оберіть квартиру і натисніть «Запустити».".format(
        number, len(_selected_group["placements"])
    ) if not subset else u"Обрано плям з групи {}: {}. Оберіть квартиру і натисніть «Запустити».".format(
        number, len(_selected_group["placements"])
    )


# ============================================================
# "Розпланування поверху": every footprint of the active plan gets a grey
# crosshatch region; the user picks footprints (any way) + an apartment and
# presses "Підтвердити вибір" (region turns green). Once every footprint has
# its apartment the button becomes "Запустити" and places them all.
# window._floor = {"viewId", "regions": {roof id: region id},
#                  "assigned": {roof id: {rvtPath, displayName, placement}}}
# ============================================================

def _floor_all_assigned(window):
    floor = getattr(window, "_floor", None)
    return bool(floor) and bool(floor["footprints"]) and floor["footprints"] <= set(floor["assigned"])


_PICK_FLOOR = "floor"        # "Розпланування поверху" (chosen at first)


def _auto_start_floor_layout(window, view, items, occupied):
    """"Розпланування поверху" is chosen by default: on a plan Froglet made that still has free footprints the
    floor-layout mode starts by itself (as if its button was pressed) - unless the user chose the single pick.
    Never raises."""
    try:
        from Autodesk.Revit.DB import ViewPlan

        if getattr(window, "_floor", None):
            return
        if window._floor_start_event is None or not isinstance(view, ViewPlan):
            return
        if not any(item["id"] not in occupied for item in items):
            return
        window._floor_start_event.Raise()
    except Exception:
        _debug_log(u"DKUI: розпланування поверху не запущено само:\n```\n{}\n```".format(traceback.format_exc()))


def _sync_run_buttons(window):
    """The "Запустити" at the bottom right (and the "Автоматичний вибір планувань для решти плям" beside the scheme): with "2" open it is there as soon as ONE footprint has its apartment (nothing is
    placed until it is pressed); with "3" open it runs the ticked edits."""
    from System.Windows import Visibility

    try:
        floor = getattr(window, "_floor", None)
        window.FloorRunButton.Visibility = (
            Visibility.Visible if floor and floor["assigned"] and window.FootprintExpander.IsExpanded else Visibility.Collapsed
        )
        window.PlacedRunButton.Visibility = Visibility.Visible if window.GeometryExpander.IsExpanded else Visibility.Collapsed
        # "Автоматичний вибір планувань для решти плям" is there only while some footprint still has no layout chosen.
        if floor:
            pending = any(roof_id not in floor["assigned"] for roof_id in floor["footprints"])
        else:                            # (the layout has not started yet: any footprint with no apartment placed)
            view_items = getattr(window, "_view_items", None)
            occupied_pair = getattr(window, "_occupied", None)
            taken = occupied_pair[1] if occupied_pair and view_items and occupied_pair[0] == view_items[0] else set()
            pending = bool(view_items) and any(item["id"] not in taken for item in view_items[1])
        # (and not while a footprint is chosen: then the buttons for the chosen ones stand there; nor while "Запустити" is placing
        # the floor: the mode is already off, "occupied" is not read again yet, so the footprints being placed would look free)
        placing = getattr(window, "_floor_cleanup", None) is not None
        shown = pending and not placing and not getattr(window, "_scheme_chosen_any", False)
        window.AutoPickPlansButton.Visibility = Visibility.Visible if shown else Visibility.Collapsed
        _sync_side_message(window)
    except Exception:
        pass


def _refresh_floor_buttons(window):
    from System.Windows import Visibility

    floor = getattr(window, "_floor", None)
    # (the "Автоматичний вибір" button is always there: it works for the floor layout and for one picked footprint)
    # Same "Запустити" on the "Вибір квартири" tab once every footprint has
    # its apartment; gone again after placing / cancelling.
    _sync_run_buttons(window)

    # The bank tab's button and the "Запустити" of every open apartment tab.
    window.RunButton.Content = _run_button_text(window)
    for detail_button in getattr(window, "_detail_run_buttons", []):
        try:
            detail_button.Content = _run_button_text(window)
        except Exception:
            pass


def _run_button_text(window):
    """"Підтвердити вибір" while floor-layout mode still needs apartments
    confirmed, "Запустити" otherwise."""
    if getattr(window, "_floor", None) and not _floor_all_assigned(window):
        return u"Підтвердити вибір"
    return u"Запустити"


def _rank_apartments(apartments, type_code, window_code, added_code, area, dims, footprints=None, ordered_dims=None):
    """The apartments that suit a footprint (group), best first, decided by the backend (core/ranking.py): the layouts' JSON -
    the facade and depth of a Рядове layout, the outline of a Присутня one - are read from ITS pool; the JSON it does not
    find are not used (the sizes of the file names are), and the log says how many it found."""

    def _remote():
        order, json_sizes = backend_api.rank_apartments(
            apartments, type_code, window_code, added_code, area, dims, footprints or [], ordered_dims)
        if ordered_dims and json_sizes["total"] and not json_sizes["found"]:
            _debug_log(u"DKUI: сервер не знайшов JSON планувань на своєму пулі ({} Рядових): ширина й глибина - з назв файлів."
                       .format(json_sizes["total"]))
        return [apartments[index] for index in order]

    return (_remote())


def _closest_type_groups(apartments, type_code):
    """The room counts ("0" студіо, "1", "2", "3") other than `type_code` that the
    bank has apartments for, grouped by distance from it, closest group first:
    for "3" -> [["2"], ["1"], ["0"]]; for "2" -> [["1", "3"], ["0"]]."""
    try:
        wanted = int(type_code)
    except Exception:
        return []

    present = set()
    for item in apartments:
        code = item.get("apartmentType")
        if code in APARTMENT_TYPE_CODES and code != type_code:
            present.add(code)

    by_distance = {}
    for code in present:
        try:
            by_distance.setdefault(abs(int(code) - wanted), []).append(code)
        except Exception:
            continue
    return [sorted(by_distance[distance]) for distance in sorted(by_distance)]


def _auto_pick_floor_plans(window, uiapp):
    """"Автоматичний вибір": for each group of footprints that has
    no apartment yet - or only for the footprints chosen on the scheme, when any are - pick the most suitable plan
    (area + size) and confirm it for all of them - nothing is placed until "Запустити". Outside the
    floor layout (the single pick) it finds the plan for the picked footprint."""
    floor = getattr(window, "_floor", None)
    uidoc = uiapp.ActiveUIDocument
    silent = bool(getattr(window, "_auto_silent", False))
    window._auto_silent = False
    if uidoc is None:
        return
    if not floor:
        if not silent:
            _auto_pick_picked_footprints(window)
        return

    import apartment_groups
    import floor_layout
    from Autodesk.Revit.DB import ElementId

    # "Автоматичний вибір планувань" (beside the chosen footprints) works on the chosen ones only; "... для решти плям" on
    # every footprint that has no layout confirmed.
    scope = getattr(window, "_auto_scope", "all")
    chosen = set(getattr(window, "_chosen_ids", None) or ()) if scope == "chosen" else set()
    pending = [
        item for item in floor["items"]
        if item["id"] in floor["footprints"] and item["id"] not in floor["assigned"]
        and (scope != "chosen" or item["id"] in chosen)
    ]
    if not pending:
        window.StatusText.Text = u"Для всіх плям уже підтверджено квартири." if scope != "chosen" else u"Для обраних плям уже підтверджено квартири."
        return

    apartments = getattr(window, "_all_apartments", None) or []
    if not apartments:
        if silent:                       # (it started by itself with the plan: no dialog)
            window.StatusText.Text = u"Банк квартир порожній - планування не підібрано."
        else:
            forms.alert(u"Банк квартир порожній - нічого підбирати.", title="Froglet")
        return

    # Where each footprint's outside side runs is in its placement payload: a Рядове footprint is then
    # described by its facade and its depth, and a group whose footprints lie both ways is cut in two -
    # one layout cannot have its windows on the short side of one footprint and the long side of another.
    placements = _placements_cached(window, uiapp, uidoc.ActiveView, [item["id"] for item in pending])

    choices = {}
    for group in apartment_groups.group_footprints(pending):
        ordered = facade_fit.ordered_dims(group["members"], placements) if group["window"] == "R" else None
        for members, ordered_dims in facade_fit.split_by_orientation(group["members"], ordered):
            type_codes = [str(m["type"]).strip()[:1] for m in members]
            type_code = max(set(type_codes), key=type_codes.count)
            areas = [m["area"] for m in members if m["area"] is not None]
            dims = (
                sum(m["dims"][0] for m in members) / len(members),
                sum(m["dims"][1] for m in members) / len(members),
            )
            if ordered_dims:
                dims = facade_fit.average(ordered_dims)

            def _rank():
                return _rank_apartments(
                    apartments, type_code, group["window"], group["added"],
                    sum(areas) / len(areas) if areas else None, dims,
                    footprints=[m["footprint"] for m in members if m.get("footprint")],
                    ordered_dims=ordered_dims,
                )

            ranked = _rank()
            if ranked:
                for member in members:
                    choices[member["id"]] = ranked   # best first; the rest are the alternatives

    placement_by_roof = dict(
        (placement["roofId"], placement) for placement in placements
        if placement.get("roofId") is not None
    )

    confirmed = []
    for roof_id, ranked in choices.items():
        placement = placement_by_roof.get(roof_id)
        if placement is None:
            continue
        apartment = ranked[0]
        floor["assigned"][roof_id] = {
            "rvtPath": apartment.get("rvtPath"),
            "displayName": apartment.get("displayName"),
            "item": apartment,
            "placement": placement,
            # Chosen by the script: may be swapped for one of `pool` (the
            # suitable apartments, best first) if it causes a bedroom conflict.
            "source": "auto",
            "pool": ranked,
        }
        confirmed.append(roof_id)

    _selected_group["placements"] = []
    window._chosen_ids = None
    _sync_floor_colors(window, uiapp, [])

    _refresh_floor_buttons(window)
    _refresh_available_groups(window, uiapp)

    left = len(pending) - len(confirmed)
    window.StatusText.Text = (
        u"Автоматично підібрано квартири для {} плям.".format(len(confirmed))
        + (u" Без підходящої квартири: {} - оберіть їх вручну." .format(left) if left else
           u" Перевірте вибір і натисніть «Запустити».")
    )


def _auto_pick_picked_footprints(window):
    """"Автоматичний вибір" outside the floor layout (the single pick): the most suitable plan for the footprint(s)
    picked last - ranked like the floor layout ranks a group - becomes the chosen card of the bank, so "Запустити"
    places it. Needs a pick (its parameters are what `_apply_footprint_payload` remembered)."""
    payload = getattr(window, "_last_pick_payload", None)
    picked = _selected_group.get("placements") or (
        dkui_bridge.get_last_apartment_placement() if dkui_bridge is not None else None)
    if not payload or not picked:
        window.StatusText.Text = u"Спершу оберіть пляму квартири (або розпочніть розпланування поверху)."
        return

    apartments = getattr(window, "_all_apartments", None) or []
    if not apartments:
        forms.alert(u"Банк квартир порожній - нічого підбирати.", title="Froglet")
        return

    low, high = payload.get("areaMin"), payload.get("areaMax")
    width, length = payload.get("dimWidth"), payload.get("dimLength")
    ordered = payload.get("orderedDims") if payload.get("dimOrdered") else None
    ranked = _rank_apartments(
        apartments, payload.get("apartmentType"), payload.get("windowPlacement"), payload.get("addedPart"),
        (low + high) / 2.0 if low is not None and high is not None else None,
        (float(width), float(length)) if width is not None and length is not None else None,
        footprints=payload.get("footprintsFt") or [],
        ordered_dims=[tuple(dims) for dims in ordered] if ordered else None,
    )
    if not ranked:
        window.StatusText.Text = u"Для обраної плями не знайдено підходящої квартири - оберіть її вручну."
        return

    best = ranked[0]
    window._selected_apartment = best
    window._current_page = 1
    _render_grid_now(window, save=False)
    window.StatusText.Text = u"Автоматично підібрано «{}». Перейдіть на вкладку «Банк квартир» і натисніть «Запустити».".format(
        best.get("displayName") or best.get("name") or u""
    )


def _clear_floor_assignments(window, uiapp):
    """"Очистити" on a confirmed apartment: forget it for its footprints,
    turn their regions grey again and return them to "Доступні групи
    квартир на активному виді"."""
    import floor_layout
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId

    floor = getattr(window, "_floor", None)
    uidoc = uiapp.ActiveUIDocument
    ids = [roof_id for roof_id in (window._pending_clear_ids or []) if floor and roof_id in floor["assigned"]]
    window._pending_clear_ids = []
    if not ids or uidoc is None:
        return

    for roof_id in ids:
        del floor["assigned"][roof_id]

    # Anything chosen/selected a moment ago may include these footprints.
    window._chosen_ids = None
    _selected_group["placements"] = []
    _sync_floor_colors(window, uiapp, [])
    try:
        uidoc.Selection.SetElementIds(List[ElementId]())
    except Exception:
        pass

    _refresh_floor_buttons(window)
    _refresh_available_groups(window, uiapp)
    window.StatusText.Text = u"Підтверджено плям: {} з {}.".format(
        len(floor["assigned"]), len(floor["footprints"])
    )


def _pick_floor_footprints(window, uiapp):
    """Choose already-confirmed footprints again (an apartment's "Вибрати" or
    one of its lines): select them in Revit, compute their placements and
    push their parameters into the bank filters so another apartment can be
    confirmed for them."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId

    import apartment_groups

    floor = getattr(window, "_floor", None)
    uidoc = uiapp.ActiveUIDocument
    ids = [roof_id for roof_id in (window._pending_floor_pick_ids or []) if floor and roof_id in floor["footprints"]]
    window._pending_floor_pick_ids = []
    unpick = bool(getattr(window, "_pending_floor_unpick", False))
    window._pending_floor_unpick = False
    if uidoc is None:
        return
    if not ids and unpick:
        # The last chosen footprint was clicked again: nothing is chosen any more.
        window._chosen_ids = None
        window._chosen_group_number = None
        _selected_group["placements"] = []
        try:
            uidoc.Selection.SetElementIds(List[ElementId]())
        except Exception:
            pass
        _paint_schema_choice(window, [])
        _sync_floor_colors(window, uiapp, [])
        window.StatusText.Text = u""
        return
    if not ids:
        return

    element_ids = List[ElementId]()
    for roof_id in ids:
        element_ids.Add(ElementId(roof_id))
    uidoc.Selection.SetElementIds(element_ids)

    _selected_group["placements"] = _placements_cached(window, uiapp, uidoc.ActiveView, ids)

    window._chosen_ids = set(ids)
    window._chosen_group_number = None
    try:
        _paint_group_choice(window, None, None)
    except Exception:
        pass
    _paint_schema_choice(window, ids)
    _sync_floor_colors(window, uiapp, ids)

    infos = [floor["info"][roof_id] for roof_id in ids if roof_id in floor["info"]]
    if infos:
        payload = _payload_from_members(
            infos, infos[0]["window"], infos[0]["added"], _selected_group["placements"]
        )
        _defer_footprint_payload(window, payload)

    window.StatusText.Text = u"Обрано плям: {}. Оберіть іншу квартиру і натисніть «Підтвердити вибір».".format(
        len(_selected_group["placements"])
    )


def _hand_cursor():
    from System.Windows.Input import Cursors
    return Cursors.Hand


def _swallow_mouse_event(sender, args):
    # Keeps the expander's header toggle from reacting to a click on the name.
    args.Handled = True


def _make_open_apartment_handler(window, item):
    def _handler(sender, args):
        args.Handled = True
        _open_apartment_detail_tab(window, item, True)

    return _handler


def _start_floor_layout(window, uiapp):
    import apartment_groups
    import floor_layout
    from Autodesk.Revit.DB import ViewPlan

    if getattr(window, "_floor", None):
        return

    uidoc = uiapp.ActiveUIDocument
    view = uidoc.ActiveView if uidoc is not None else None
    if view is None or not isinstance(view, ViewPlan):
        forms.alert(
            u"Відкрийте план поверху (вид, створений у Froglet), щоб розпланувати поверх.",
            title="Froglet",
        )
        return

    items = apartment_groups.collect_footprints(uiapp, view)
    if not items:
        forms.alert(u"На цьому виді немає плям квартир.", title="Froglet")
        return

    # (The view is turned earlier - when the plan is created and the roofs to
    # show are picked: floor_plan_creation. Not here.)

    # Forget any earlier single pick / group choice - only picks made from
    # now on may be confirmed.
    _selected_group["placements"] = []
    if dkui_bridge is not None:
        try:
            dkui_bridge.set_last_apartment_placement(None)
        except Exception:
            pass

    view_id = int(view.Id.Value) if hasattr(view.Id, "Value") else int(view.Id.IntegerValue)

    # Footprints that already have an apartment placed are not part of the
    # layout: nothing to choose for them.
    occupied = _compute_occupied(uidoc.Document, items, view)
    window._view_items = (view_id, items)
    window._occupied = (view_id, occupied)

    open_ids = set(item["id"] for item in items if item["id"] not in occupied)
    if not open_ids:
        forms.alert(u"На всіх плямах цього виду вже є квартири.", title="Froglet")
        return

    window._floor = {
        "viewId": view_id,
        # Footprints still to be filled (all of the view's, minus the ones
        # that already have a placed apartment). "regions" is unused now
        # (kept empty): footprints are shown by overriding the roof itself.
        "footprints": open_ids,
        "regions": {},
        "assigned": {},
        "items": items,
        "info": dict(
            (item["id"], {
                "type": item["type"], "area": item["area"], "dims": item["dims"],
                "window": item["window"], "added": item["added"],
                "id": item["id"], "footprint": item.get("footprint"),
            })
            for item in items
        ),
    }
    # Placement order: the footprints next to a сходово-ліфтовий вузол first,
    # then outwards through their neighbours.
    try:
        import placement_order
        window._floor["order"] = placement_order.order_footprints(
            items, open_ids, placement_order.stair_core_polygons(uidoc.Document, view)
        )
    except Exception:
        logger.warning("DKUI: порядок розміщення не визначено: %s", traceback.format_exc())
        window._floor["order"] = []
    window._floor_expanded = set()

    # (a footprint of the single pick that was blue is not chosen any more: that pick was forgotten above)
    window._chosen_ids = None
    try:
        _sync_floor_colors(window, uiapp, [])
    except Exception:
        _debug_log(u"DKUI: підсвітку плям не скинуто:\n```\n{}\n```".format(traceback.format_exc()))

    _refresh_floor_buttons(window)
    window.StatusText.Text = (
        u"Розпланування поверху: оберіть плями ({}), потім квартиру і натисніть «Підтвердити вибір»."
        .format(len(open_ids))
    )
    # By default the open plan is filled by the script at once; what the user does not like he changes (the cross, "Очистити",
    # the bank tab). It starts by itself, so no dialog when the bank is empty.
    if getattr(window, "_floor_auto_event", None) is not None:
        window._auto_scope = "all"
        window._auto_silent = True
        window._floor_auto_event.Raise()


def _cancel_floor_layout(window, uiapp):
    """Leave the mode (button pressed again / after "Запустити") and remove
    what it showed: blue/green roof overrides (footprints with placed
    apartments keep their 90 % transparency)."""
    # After "Запустити" the mode is already switched off; its state waits in
    # _floor_cleanup for this handler.
    floor = getattr(window, "_floor", None) or getattr(window, "_floor_cleanup", None)
    window._floor_cleanup = None
    window._floor = None
    window._display_chosen = set()

    if floor:
        window._last_floor_items = (floor["viewId"], floor["items"])
        # Reset the floor view's roofs (it may no longer be the active view).
        if not getattr(window, "_view_items", None) or window._view_items[0] != floor["viewId"]:
            window._view_items = (floor["viewId"], floor["items"])

    if dkui_bridge is not None:
        try:
            dkui_bridge.set_floor_region_map(None)
        except Exception:
            pass

    uidoc = uiapp.ActiveUIDocument
    _sync_floor_colors(window, uiapp, [])

    _refresh_floor_buttons(window)
    window.StatusText.Text = u""

    # Every footprint is available again in "Доступні групи квартир" (reusing
    # the footprints read when the mode started if the view is the same).
    cached = getattr(window, "_last_floor_items", None)
    window._last_floor_items = None
    active_view = uidoc.ActiveView if uidoc is not None else None
    reuse = None
    if cached and active_view is not None:
        active_id = int(active_view.Id.Value) if hasattr(active_view.Id, "Value") else int(active_view.Id.IntegerValue)
        if cached[0] == active_id:
            reuse = cached[1]
    _refresh_available_groups(window, uiapp, items=reuse)


def _sync_floor_colors(window, uiapp, chosen):
    """Show each footprint roof of the view by its state, overriding the roof
    itself: chosen right now (`chosen`: roof ids - a group or line of the list, a footprint of the floor layout,
    or the one footprint of the single pick) = blue; apartment confirmed = green; apartment already placed on it = 90 %
    transparent; otherwise untouched. Only roofs whose state changed are
    touched. `chosen=None` keeps the previous choice."""
    import floor_layout
    from Autodesk.Revit.DB import ElementId

    uidoc = uiapp.ActiveUIDocument
    view_items = getattr(window, "_view_items", None)
    if uidoc is None or not view_items:
        return

    view_id, items = view_items
    floor = getattr(window, "_floor", None)
    floor_here = bool(floor) and floor["viewId"] == view_id

    if chosen is not None:
        window._display_chosen = set(chosen)
    chosen_ids = getattr(window, "_display_chosen", set())
    assigned = floor["assigned"] if floor_here else {}
    footprints = floor["footprints"] if floor_here else None      # (outside the floor layout any chosen footprint is blue)

    occupied_pair = getattr(window, "_occupied", None)
    occupied = occupied_pair[1] if occupied_pair and occupied_pair[0] == view_id else set()

    if not hasattr(window, "_roof_shown"):
        window._roof_shown = {}
    shown = window._roof_shown.setdefault(view_id, {})

    changes = {}
    for item in items:
        roof_id = item["id"]
        state = (
            roof_id in chosen_ids and (footprints is None or roof_id in footprints),
            roof_id in assigned,
            roof_id in occupied,
        )
        if shown.get(roof_id, (False, False, False)) != state:
            shown[roof_id] = state
            changes[roof_id] = state

    if not changes:
        return

    view = uidoc.Document.GetElement(ElementId(view_id))
    if view is not None:
        floor_layout.apply_states(uidoc.Document, view, {}, {}, changes)


def _mark_floor_confirmed(window, uiapp):
    """Turn the regions of the just-confirmed footprints green and clear the
    Revit selection."""
    import floor_layout
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId

    floor = getattr(window, "_floor", None)
    uidoc = uiapp.ActiveUIDocument
    if not floor or uidoc is None:
        return

    window._pending_green_ids = []
    window._chosen_ids = None
    # The confirmed footprints are in floor["assigned"] already: green.
    _sync_floor_colors(window, uiapp, [])

    try:
        uidoc.Selection.SetElementIds(List[ElementId]())
    except Exception:
        pass

    # Footprints that now have an apartment drop out of "Доступні групи квартир".
    _refresh_available_groups(window, uiapp)


def _confirm_floor_choice(window, item, quiet=False):
    """"Підтвердити вибір": remember `item` as the apartment for every
    footprint picked right now, without placing anything. `quiet`: with nothing picked, say so in the status
    line instead of an alert (every footprint has its apartment already)."""
    floor = window._floor

    placements = list(_selected_group["placements"])
    if not placements and dkui_bridge is not None:
        try:
            last = dkui_bridge.get_last_apartment_placement()
        except Exception:
            last = None
        if last:
            placements = [last]

    placements = [
        placement for placement in placements
        if placement.get("roofId") in floor["footprints"]
    ]
    if not placements and quiet:
        window.StatusText.Text = u"Усі плями вже мають планування. Натисніть «Запустити»."
        return
    if not placements:
        forms.alert(
            u"Спершу оберіть пляму/плями на плані (вкладка «Вибір квартири»), "
            u"потім квартиру в банку.",
            title="Froglet",
        )
        return

    for placement in placements:
        floor["assigned"][placement["roofId"]] = {
            "rvtPath": item.get("rvtPath"),
            "displayName": item.get("displayName"),
            "item": item,
            "placement": placement,
            "source": "user",   # picked by the user: never swapped by the script
        }

    window._pending_green_ids = [placement["roofId"] for placement in placements]
    _selected_group["placements"] = []
    if dkui_bridge is not None:
        try:
            dkui_bridge.set_last_apartment_placement(None)
        except Exception:
            pass

    window._chosen_ids = None
    try:
        _paint_group_choice(window, None, None)
    except Exception:
        pass

    if window._floor_mark_event is not None:
        window._floor_mark_event.Raise()

    _refresh_floor_buttons(window)
    window.StatusText.Text = u"Підтверджено плям: {} з {}.".format(
        len(floor["assigned"]), len(floor["footprints"])
    )


def _run_floor_layout(window):
    """"Запустити": place the apartments chosen so far (every footprint that has one), then leave the mode (it starts again
    for the footprints that are still free)."""
    if apartment_group_placement is None or dkui_bridge is None:
        forms.alert(u"Не вдалося розмістити квартири: модулі розміщення недоступні.", title="DKUI")
        return

    floor = window._floor
    event = dkui_bridge.get_persistent_placement_event()
    if event is None:
        forms.alert(
            u"Внутрішня помилка: немає готової події розміщення. "
            u"Закрийте і знову відкрийте «Банк квартир».",
            title="DKUI",
        )
        return

    # Next to a сходово-ліфтовий вузол first, then their neighbours, and so on.
    position = dict((roof_id, index) for index, roof_id in enumerate(floor.get("order") or []))
    requests = []
    for _roof_id, entry in sorted(
        floor["assigned"].items(), key=lambda pair: (position.get(pair[0], len(position)), pair[0])
    ):
        single = _build_placement_request(entry["rvtPath"], entry["displayName"], entry["placement"])
        # Who chose it: only the script's choices may be swapped (see
        # _resolve_bedroom_conflicts), for the next-best of its pool.
        single["source"] = entry.get("source", "user")
        single["alternatives"] = [
            {"rvtPath": alt.get("rvtPath"), "displayName": alt.get("displayName")}
            for alt in (entry.get("pool") or [])[1:9]
            if alt.get("rvtPath") != entry["rvtPath"]
        ]
        requests.append(single)
    dkui_bridge.set_pending_run_request({"batch": requests})

    window._floor_cleanup = floor  # the cancel handler below clears the view
    window._last_floor_items = (floor["viewId"], floor["items"])
    window._floor = None
    _refresh_floor_buttons(window)

    event.Raise()
    if window._floor_cancel_event is not None:
        window._floor_cancel_event.Raise()


def _subscribe_created_views_live_update(window):
    """Refresh the list as soon as a tracked view is deleted or renamed in
    Revit (DocumentChanged), instead of only when the tab is opened."""
    if getattr(window, "_doc_changed_subscribed", False):
        return

    from pyrevit import HOST_APP

    def _on_document_changed(sender, args):
        # An apartment was placed/deleted/moved: footprints with apartments
        # on them change (transparency + "Доступні групи квартир").
        try:
            if _document_change_touches_groups(args):
                if getattr(window, "_view_items", None) and window._occupancy_event is not None:
                    window._occupancy_event.Raise()
                if window._placed_event is not None:
                    window._placed_event.Raise()
                if getattr(window, "_floor_copy", None) and getattr(window, "_floors_event", None) is not None:
                    window._floors_event.Raise()       # section mode: the laid-out floors may have changed
        except Exception:
            pass

        try:
            tracked = getattr(window, "_created_view_ids", set())
            if not tracked:
                return

            changed = set()
            for id_list in (args.GetDeletedElementIds(), args.GetModifiedElementIds()):
                for element_id in id_list:
                    try:
                        changed.add(int(element_id.Value))
                    except Exception:
                        changed.add(int(element_id.IntegerValue))

            if tracked & changed:
                _fill_created_views(window, args.GetDocument())
        except Exception:
            pass

    HOST_APP.app.DocumentChanged += _on_document_changed

    def _on_view_activated(sender, args):
        try:        # a view opened from "Створені види" by a view change request: zoomed once it is the active one
            pending = getattr(window, "_zoom_on_activation", None)
            if pending is not None and args.CurrentActiveView.Id == pending:
                window._zoom_on_activation = None
                _zoom_to_view_geometry(HOST_APP.uiapp.ActiveUIDocument, args.CurrentActiveView)
        except Exception:
            pass

        try:
            import created_views

            created = created_views.is_created(args.Document, args.CurrentActiveView)

            if created:
                _set_groups_message(window, u"Обчислення…")
                if window._groups_event is not None:
                    window._groups_event.Raise()
            else:
                _set_groups_message(window, u"Не відкрито жодного виду")
                window._groups_data = []
        except Exception:
            pass

        try:        # enter / leave section mode ("Розміщені поверхи")
            if getattr(window, "_floors_event", None) is not None:
                window._floors_event.Raise()
        except Exception:
            pass

        try:        # "Розміщені квартири" show the floor of the new active view
            if getattr(window, "_placed_event", None) is not None:
                window._placed_event.Raise()
        except Exception:
            pass

    HOST_APP.uiapp.ViewActivated += _on_view_activated

    def _on_idling(sender, args):
        try:
            if getattr(window, "_chosen_ids", None):
                _unchoose_group_if_selection_changed(window, sender)
        except Exception:
            pass

    HOST_APP.uiapp.Idling += _on_idling
    window._doc_changed_subscribed = True


def _show_no_created_views(window):
    from System.Windows.Controls import TextBlock

    note = TextBlock()
    note.Text = u"Поки не створено жодного плану поверху"
    note.Style = window.FindResource("BodyTextStyle")
    window.CreatedViewsList.Items.Clear()
    window.CreatedViewsList.Items.Add(note)


def _fill_created_views(window, document):
    """Fill the "Створені види:" list from Revit (current names, deleted
    views dropped). Runs in an API context."""
    from System.Windows import TextWrapping, Thickness
    from System.Windows.Controls import TextBlock
    from System.Windows.Input import Cursors

    import created_views

    entries = created_views.list_views(document) if document is not None else []
    window._created_view_ids = set(view_id for view_id, _name in entries)
    names = [name for _id, name in entries]

    window.CreatedViewsList.Items.Clear()

    # The views of the chosen building sections, in their own column.
    try:
        _fill_section_views(window, document)
    except Exception:
        logger.warning("DKUI: не вдалося показати види секцій: %s", traceback.format_exc())

    # "Створені види" are always there: without a plan the list says so.
    from System.Windows import Visibility
    window.CreatedViewsPanel.Visibility = Visibility.Visible
    if not names:
        _show_no_created_views(window)
        return

    for view_id, name in entries:
        label = TextBlock()
        label.Text = name
        label.TextWrapping = TextWrapping.Wrap
        label.Margin = Thickness(0, 0, 0, 6)
        label.Cursor = Cursors.Hand
        label.ToolTip = u"Перейти на цей вид"
        label.MouseEnter += _on_view_label_enter
        label.MouseLeave += _on_view_label_leave
        label.MouseLeftButtonUp += _make_open_view_handler(window, view_id)
        window.CreatedViewsList.Items.Add(label)


def _fill_section_views(window, document):
    """"Створені види для секцій": for every choice of building sections - its
    section numbers, then its views (3D and floor plans), each opens on a click."""
    from System.Windows import FontWeights, TextWrapping, Thickness, Visibility
    from System.Windows.Controls import TextBlock
    from System.Windows.Input import Cursors

    import created_views

    window.SectionViewsList.Items.Clear()
    sets = created_views.list_section_views(document) if document is not None else []
    window.SectionViewsPanel.Visibility = Visibility.Visible if sets else Visibility.Collapsed
    # (renaming / deleting one of these views refreshes the list too)
    window._created_view_ids = set(getattr(window, "_created_view_ids", set())) | set(
        view_id for _sections, views, _key in sets for view_id, _name, _kind in views)

    from System.Windows import GridLength, HorizontalAlignment, VerticalAlignment
    from System.Windows.Controls import Button, ColumnDefinition, Grid

    for sections, views, set_key in sets:
        heading = TextBlock()
        heading.Text = u"Секції: {}".format(u", ".join(sections))
        heading.FontWeight = FontWeights.SemiBold
        heading.TextWrapping = TextWrapping.Wrap
        heading.VerticalAlignment = VerticalAlignment.Center

        # "Видалити": deletes every view of this choice of sections.
        remove = Button()
        remove.Content = u"Видалити"
        remove.Style = window.FindResource("ChooseTextButtonStyle")
        remove.Foreground = _selected_dark_brush()
        remove.Padding = Thickness(8, 2, 8, 2)
        remove.Margin = Thickness(8, 0, 12, 0)
        remove.HorizontalAlignment = HorizontalAlignment.Right
        remove.VerticalAlignment = VerticalAlignment.Center
        remove.ToolTip = u"Видалити всі види цих секцій"
        Grid.SetColumn(remove, 1)

        def _on_delete(sender, args, set_key=set_key):
            window._pending_section_delete = set_key
            if window._section_delete_event is not None:
                window._section_delete_event.Raise()

        remove.Click += _on_delete

        header = Grid()
        header.ColumnDefinitions.Add(ColumnDefinition())
        header.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength.Auto))
        header.Margin = Thickness(19, 4, 0, 4)
        header.Children.Add(heading)
        header.Children.Add(remove)
        window.SectionViewsList.Items.Add(header)
        for view_id, name, _kind in views:
            label = TextBlock()
            label.Text = name
            label.TextWrapping = TextWrapping.Wrap
            label.Margin = Thickness(31, 0, 0, 4)
            label.Cursor = Cursors.Hand
            label.ToolTip = u"Перейти на цей вид"
            label.MouseEnter += _on_view_label_enter
            label.MouseLeave += _on_view_label_leave
            label.MouseLeftButtonUp += _make_open_view_handler(window, view_id)
            window.SectionViewsList.Items.Add(label)


def _on_view_label_enter(sender, args):
    from System.Windows import TextDecorations
    sender.TextDecorations = TextDecorations.Underline


def _on_view_label_leave(sender, args):
    sender.TextDecorations = None


def _make_open_view_handler(window, view_id):
    def _handler(sender, args):
        window._pending_view_id = view_id
        if window._open_view_event is not None:
            window._open_view_event.Raise()

    return _handler


def _open_created_view(window, uiapp):
    """Make the clicked "Створені види" entry the active view. Runs in an API
    context; the ViewActivated subscription then refreshes the group list."""
    from Autodesk.Revit.DB import ElementId, View

    view_id = getattr(window, "_pending_view_id", None)
    uidoc = uiapp.ActiveUIDocument
    if view_id is None or uidoc is None:
        return

    view = uidoc.Document.GetElement(ElementId(view_id))
    if view is None or not isinstance(view, View):
        window.StatusText.Text = u"Вид більше не існує."
        return

    already_active = uidoc.ActiveView is not None and uidoc.ActiveView.Id == view.Id
    try:
        uidoc.ActiveView = view
    except Exception:
        window._zoom_on_activation = view.Id          # the change comes later: zoomed in ViewActivated
        uidoc.RequestViewChange(view)
        return
    _zoom_to_view_geometry(uidoc, view)
    if already_active:
        # No ViewActivated will come, so the stage is read here: the plan opened from the list opens its stage ("2" / "3")
        # even if the list of another stage was opened by hand.
        window._force_stage = True
        _refresh_available_groups(window, uiapp)


def _zoom_to_view_geometry(uidoc, view):
    """A view opened from "Створені види" is zoomed to everything shown in it (as when Froglet makes the plan). Never raises."""
    try:
        import floor_plan_creation

        floor_plan_creation._zoom_to_fit(uidoc, view)
    except Exception:
        _debug_log(u"DKUI: вид не зумлено до геометрії:\n```\n{}\n```".format(traceback.format_exc()))


# ============================================================
# "Розміщені квартири" (under "Редагування квартир"): every apartment Froglet
# placed (placed_apartments.py). Rows are picked with "Вибрати": a plain click
# adds the row, Shift+click picks the range from the last clicked row, Ctrl+click
# unpicks it. While anything is picked, "Запустити" and the four check boxes show.
# window._placed_rows = [(group id, border, label, button)], in display order.
# ============================================================


def _refresh_placed_apartments(window, uiapp):
    """API-context handler: rebuild the list from the remembered apartments
    that still exist in the document, keeping the picks that survived. The
    apartments are piled like "Доступні групи квартир", by the footprint each
    one is tied to."""
    import apartment_groups
    import placed_apartments

    uidoc = uiapp.ActiveUIDocument
    entries = placed_apartments.live_entries(uidoc.Document) if uidoc is not None else []
    # Only the apartments of the floor the active plan shows (all of them in a 3D view): the edits of
    # "Запустити" work on what is picked here, so they touch this floor only.
    window._placed_floor_name = None
    if uidoc is not None:
        try:
            import floor_copy
            if floor_copy.active_level_id(uidoc.ActiveView) is not None:
                entries = floor_copy.on_active_floor(uidoc.Document, uidoc.ActiveView, entries)
                window._placed_floor_name = apartment_group_placement._element_name(uidoc.ActiveView.GenLevel)
        except Exception:
            logger.warning("DKUI: квартири поверху не відфільтровано: %s", traceback.format_exc())
    entries.sort(key=lambda entry: (entry.get("name") or u"", entry["groupId"]))

    # The edits work on EVERY apartment placed on this floor (there is no list to pick from any more); nothing is painted.
    window._placed_selected = set()
    window._placed_targets = sorted(entry["groupId"] for entry in entries)
    window._placed_all = entries
    _sync_placed_controls(window)
    try:        # (also the way section mode is noticed when the window opens on a section view)
        if getattr(window, "_floors_event", None) is not None:
            window._floors_event.Raise()
    except Exception:
        pass


# ============================================================
# Section mode ("Розміщені поверхи"): while the active view is one of the views made for the
# chosen building sections, the right column shows the floors of those sections that are
# completely laid out (an apartment on every footprint), and "Копіювання розпланованого поверху
# на решту поверхів" copies the picked one onto the identical floors (floor_copy.py).
# window._floor_copy = {"sections", "floors", "laid": [original laid-out floors], "entries", "selected": {level ids}}
# ============================================================

def _refresh_laid_out_floors(window, uiapp):
    """API-context handler: enter / leave section mode and rebuild the list of laid-out floors.
    Never raises (it runs on every view change)."""
    from System.Windows import Visibility

    try:
        uidoc = uiapp.ActiveUIDocument
        sections = None
        if uidoc is not None:
            import floor_copy
            sections = floor_copy.active_sections(uidoc.Document, uidoc.ActiveView)
        if not sections:
            window._floor_copy = None
            window.PlacedFloorsPanel.Visibility = Visibility.Collapsed
            return
        floors, laid, entries = floor_copy.laid_out_floors(uidoc.Document, sections)
        try:        # the 3D view of the sections: footprints with an apartment are almost fully transparent
            floor_copy.sync_3d_transparency(uidoc.Document, uidoc.ActiveView, floors, set(entries))
        except Exception:
            logger.warning("DKUI: прозорість плям у 3D виді не застосована: %s", traceback.format_exc())
        previous = getattr(window, "_floor_copy", None) or {}
        selected = set(previous.get("selected") or ()) & set(floor["levelId"] for floor in laid)
        window._floor_copy = {
            "sections": sections, "floors": floors, "laid": laid, "entries": entries, "selected": selected,
        }
        window.PlacedFloorsPanel.Visibility = Visibility.Visible
        _fill_laid_out_floors(window)
    except Exception:
        logger.warning("DKUI: не вдалося показати розміщені поверхи: %s", traceback.format_exc())
        _debug_log("DKUI: не вдалося показати розміщені поверхи:\n```\n{}\n```".format(traceback.format_exc()))


def _fill_laid_out_floors(window):
    from System.Windows import CornerRadius, TextWrapping, Thickness
    from System.Windows.Controls import Border, TextBlock
    from System.Windows.Input import Cursors

    import floor_copy

    state = getattr(window, "_floor_copy", None)
    window.PlacedFloorsList.Items.Clear()
    window._floor_boxes = []
    if not state:
        return

    if not state["laid"]:
        empty = TextBlock()
        empty.Text = u"Повністю розпланованих поверхів ще немає."
        empty.Foreground = _muted_brush()
        empty.Margin = Thickness(19, 0, 0, 0)
        window.PlacedFloorsList.Items.Add(empty)
        _sync_floor_controls(window)
        return

    for floor in state["laid"]:
        others = floor_copy.identical_floors(state["floors"], floor)
        label = TextBlock()
        label.Text = u"{}: квартир {}{}".format(
            floor["levelName"], len(floor["roofKeys"]),
            u"; однакові поверхи: {}".format(u", ".join(other["levelName"] for other in others)) if others
            else u"; однакових поверхів немає")
        label.TextWrapping = TextWrapping.Wrap
        label.Foreground = _selected_dark_brush()

        border = Border()
        border.Padding = Thickness(8, 5, 8, 5)
        border.Margin = Thickness(0, 0, 0, 4)
        border.CornerRadius = CornerRadius(2)
        border.BorderThickness = Thickness(1)
        border.BorderBrush = _avr_line_strong_brush()
        border.Background = _transparent_brush()
        border.Cursor = Cursors.Hand
        border.ToolTip = u"Обрати поверх (можна кілька): їхні квартири скопіюються на поверхи з такою ж геометрією"
        border.Child = label
        border.MouseLeftButtonUp += _make_floor_click_handler(window, floor["levelId"])
        window.PlacedFloorsList.Items.Add(border)
        window._floor_boxes.append((floor["levelId"], border, label))
    _sync_floor_controls(window)


def _make_floor_click_handler(window, level_id):
    def _handler(sender, args):
        state = getattr(window, "_floor_copy", None)
        if not state:
            return
        selected = set(state.get("selected") or ())
        selected.symmetric_difference_update([level_id])        # a click picks the floor, another unpicks it
        state["selected"] = selected
        _sync_floor_controls(window)

    return _handler


def _sync_floor_controls(window):
    """The picked floor's box goes black (the copy button keeps its normal look)."""
    from System.Windows.Media import Brushes

    state = getattr(window, "_floor_copy", None) or {}
    selected = state.get("selected") or ()
    for level_id, border, label in getattr(window, "_floor_boxes", []):
        picked = level_id in selected
        border.Background = _selected_dark_brush() if picked else Brushes.Transparent
        border.BorderBrush = _selected_dark_brush() if picked else _avr_line_strong_brush()
        label.Foreground = Brushes.White if picked else _selected_dark_brush()


def _copy_floor_now(window, uiapp):
    """API-context handler of "Копіювання розпланованого поверху на решту поверхів"."""
    import floor_copy

    uidoc = uiapp.ActiveUIDocument
    state = getattr(window, "_floor_copy", None)
    if uidoc is None or not state:
        return
    if not state.get("selected"):
        window.StatusText.Text = u"Оберіть розплановані поверхи у списку «Розміщені поверхи»."
        return
    try:
        result = floor_copy.copy_floors(
            uidoc.Document, state["sections"], sorted(state["selected"]), log=_debug_log)
        window.StatusText.Text = result["message"]
        _debug_log(u"[FLOOR COPY RESULT] " + result["message"])
    except Exception:
        logger.warning("DKUI: поверх не скопійовано: %s", traceback.format_exc())
        _debug_log("DKUI: поверх не скопійовано:\n```\n{}\n```".format(traceback.format_exc()))
        window.StatusText.Text = u"Не вдалося скопіювати поверх (див. журнал)."
    for event in (getattr(window, "_placed_event", None), getattr(window, "_floors_event", None)):
        if event is not None:
            event.Raise()


def _paint_picked_apartments(window, uiapp):
    """API-context handler: the picked apartments are blue in the active view;
    the ones that are no longer picked get their normal look back."""
    import floor_layout
    from Autodesk.Revit.DB import ElementId, OverrideGraphicSettings, Transaction

    uidoc = uiapp.ActiveUIDocument
    if uidoc is None:
        return
    document, view = uidoc.Document, uidoc.ActiveView
    view_id = int(view.Id.Value) if hasattr(view.Id, "Value") else int(view.Id.IntegerValue)

    selected = set(getattr(window, "_placed_selected", set()))
    painted = getattr(window, "_placed_painted", None)
    if painted is None:
        painted = window._placed_painted = {}
    before = painted.get(view_id, set())
    if before == selected:
        return

    transaction = Transaction(document, u"Froglet: picked apartments")
    transaction.Start()
    try:
        blue = floor_layout._roof_override(
            floor_layout.BLUE, floor_layout._solid_pattern_id(document), floor_layout.BLUE
        )
        for group_id in selected - before:
            try:
                view.SetElementOverrides(ElementId(int(group_id)), blue)
            except Exception:
                pass
        for group_id in before - selected:
            try:
                view.SetElementOverrides(ElementId(int(group_id)), OverrideGraphicSettings())
            except Exception:
                pass   # the group may be gone already
        transaction.Commit()
        painted[view_id] = selected
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        logger.warning("DKUI: не вдалося підсвітити квартири: %s", traceback.format_exc())


def _sync_placed_controls(window):
    """The tick boxes beside the two edits show while at least one apartment is placed on the floor (both ticked at first);
    the "Запустити" of "3" is there while "3" is open."""
    from System.Windows import Visibility

    shown = Visibility.Visible if getattr(window, "_placed_targets", []) else Visibility.Collapsed
    for check in (window.ExtendExternalWallsCheck, window.GeometryFixCheck):
        check.Visibility = shown
    _sync_run_buttons(window)


def _run_stair_core_now(window, uiapp):
    """API-context handler of the "Розташування сходово-ліфтового вузла" button."""
    import apartment_edits

    uidoc = uiapp.ActiveUIDocument
    if uidoc is None:
        return
    try:
        result = apartment_edits.run("stair_core", uiapp, uidoc.Document, [])
        window.StatusText.Text = result["message"]
    except Exception:
        logger.warning("DKUI: вузол не вдалося розташувати: %s", traceback.format_exc())
        _debug_log("DKUI: вузол не вдалося розташувати:\n```\n{}\n```".format(traceback.format_exc()))
        window.StatusText.Text = u"Не вдалося розташувати вузол (див. журнал)."


def _run_apartment_edits(window, uiapp):
    """API-context handler of the "Запустити" under "Розміщені квартири": run
    every ticked operation on the picked apartments."""
    import apartment_edits

    uidoc = uiapp.ActiveUIDocument
    group_ids = sorted(getattr(window, "_placed_targets", []))
    keys = list(getattr(window, "_pending_edit_keys", []))
    if uidoc is None:
        return
    if not group_ids:
        window.StatusText.Text = u"На цьому поверсі немає розміщених квартир."
        return
    if not keys:
        window.StatusText.Text = u"Не відмічено жодної дії."
        return

    # (the wall operations share one ungroup/regroup - see apartment_edits.run_many)
    messages = apartment_edits.run_many(uiapp, uidoc.Document, group_ids, keys)
    window.StatusText.Text = u" ".join(messages)


def _refresh_footprint_pick_event(window):
    """(Re-)create the footprint-pick ExternalEvent - called from
    show_apartment_selector() (a valid API context) on every run, same
    reason as _refresh_persistent_placement_event."""
    def _pick_footprint(uiapp):
        floor_mode = bool(getattr(window, "_floor", None)) and dkui_bridge is not None
        try:
            if floor_mode:
                # A cancelled pick must leave nothing chosen (and nothing blue).
                window._chosen_ids = None
                dkui_bridge.set_last_apartment_placement(None)

            _run_footprint_pick(uiapp)

            if dkui_bridge is not None:
                last = dkui_bridge.get_last_apartment_placement()
                roof_id = last.get("roofId") if last else None
                view_items = getattr(window, "_view_items", None)
                if floor_mode:
                    known = roof_id is not None and roof_id in window._floor["footprints"]
                else:
                    # The single pick: the footprint is one of the active plan's and has no apartment yet.
                    occupied_pair = getattr(window, "_occupied", None)
                    taken = occupied_pair[1] if occupied_pair else ()
                    known = roof_id is not None and roof_id not in taken and bool(view_items) and any(
                        item["id"] == roof_id for item in view_items[1])
                if known:
                    # Selected in Revit too, so Esc / a click on empty space
                    # unchooses it like any other choice - and blue, like every chosen footprint.
                    from System.Collections.Generic import List
                    from Autodesk.Revit.DB import ElementId

                    selection = List[ElementId]()
                    selection.Add(ElementId(int(roof_id)))
                    uiapp.ActiveUIDocument.Selection.SetElementIds(selection)
                    window._chosen_ids = set([roof_id])
                    window._chosen_group_number = None
                    _sync_floor_colors(window, uiapp, [roof_id])
                else:
                    _sync_floor_colors(window, uiapp, [])
        finally:
            pass

    handler = _CallbackExternalEventHandler(
        _pick_footprint, "Froglet Pick Footprint"
    )
    window._footprint_pick_event = ExternalEvent.Create(handler)

    def _create_floor_plan(uiapp):
        try:
            _run_floor_plan_creation(uiapp)
            _refresh_created_views(window, uiapp)
            if window._groups_event is not None:
                window._groups_event.Raise()
        finally:
            # (black while the choice in Revit is going on)
            window.SelectLevelSectionButton.Style = window.FindResource("FlashActionButtonStyle")

    floor_handler = _CallbackExternalEventHandler(
        _create_floor_plan, "Froglet Create Floor Plan"
    )
    window._floor_plan_event = ExternalEvent.Create(floor_handler)

    def _choose_sections(uiapp):
        """"Вибір секцій для розпланування": a click chooses a whole section, Esc ends the
        choosing, then the views are created."""
        import section_selection

        window.StatusText.Text = u"Клацніть пляму у 3D виді (обереться вся секція); Enter - підтвердити вибір, Esc - скасувати."
        try:
            message = section_selection.create_and_choose(uiapp)
        except Exception:
            logger.warning("DKUI: вибір секцій не вдався: %s", traceback.format_exc())
            _debug_log("DKUI: вибір секцій не вдався:\n```\n{}\n```".format(traceback.format_exc()))
            message = u"Не вдалося створити вид з секціями (див. журнал)."
        finally:
            window.SelectSectionsButton.Style = window.FindResource("FlashActionButtonStyle")
        window.StatusText.Text = message
        try:
            _refresh_created_views(window, uiapp)
        except Exception:
            pass

    window._sections_event = ExternalEvent.Create(
        _CallbackExternalEventHandler(_choose_sections, "Froglet Choose Sections"))

    def _delete_section_views(uiapp):
        import created_views

        set_key = window._pending_section_delete
        window._pending_section_delete = None
        uidoc = uiapp.ActiveUIDocument
        if set_key is None or uidoc is None:
            return
        try:
            deleted = created_views.delete_section_set(uidoc.Document, set_key)
            window.StatusText.Text = u"Видалено видів секцій: {}.".format(deleted)
        except Exception:
            logger.warning("DKUI: види секцій не видалено: %s", traceback.format_exc())
            _debug_log("DKUI: види секцій не видалено:\n```\n{}\n```".format(traceback.format_exc()))
            window.StatusText.Text = u"Не вдалося видалити види секцій (див. журнал)."
        try:
            _refresh_created_views(window, uiapp)
        except Exception:
            pass

    window._section_delete_event = ExternalEvent.Create(
        _CallbackExternalEventHandler(_delete_section_views, "Froglet Delete Section Views"))
    views_handler = _CallbackExternalEventHandler(
        lambda uiapp: _refresh_created_views(window, uiapp),
        "Froglet Refresh Created Views",
    )
    window._views_refresh_event = ExternalEvent.Create(views_handler)

    groups_handler = _CallbackExternalEventHandler(
        lambda uiapp: _refresh_available_groups(window, uiapp),
        "Froglet Refresh Apartment Groups",
    )
    window._groups_event = ExternalEvent.Create(groups_handler)

    select_handler = _CallbackExternalEventHandler(
        lambda uiapp: _select_group(window, uiapp), "Froglet Select Apartment Group"
    )
    window._group_select_event = ExternalEvent.Create(select_handler)
    window.SelectTypeButton.Click += lambda sender, args: _choose_whole_type(window)
    window.ClearChoiceButton.Click += lambda sender, args: _clear_chosen_layouts(window)

    open_view_handler = _CallbackExternalEventHandler(
        lambda uiapp: _open_created_view(window, uiapp), "Froglet Open Created View"
    )
    window._open_view_event = ExternalEvent.Create(open_view_handler)

    window._floor_start_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _start_floor_layout(window, uiapp), "Froglet Start Floor Layout"
    ))
    window._floor_cancel_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _cancel_floor_layout(window, uiapp), "Froglet Cancel Floor Layout"
    ))
    window._floor_mark_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _mark_floor_confirmed(window, uiapp), "Froglet Mark Floor Footprints"
    ))
    window._room_tags_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _flush_room_tags(uiapp), "Froglet Room Tags"
    ))
    window._occupancy_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _occupancy_changed(window, uiapp), "Froglet Apartments Changed"
    ))
    window._placed_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _refresh_placed_apartments(window, uiapp), "Froglet Placed Apartments"
    ))
    window._edit_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _run_apartment_edits(window, uiapp), "Froglet Edit Apartments"
    ))
    window._floors_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _refresh_laid_out_floors(window, uiapp), "Froglet Laid Out Floors"
    ))
    window._floor_copy_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _copy_floor_now(window, uiapp), "Froglet Copy Floor"
    ))
    window._placed_paint_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _paint_picked_apartments(window, uiapp), "Froglet Paint Picked Apartments"
    ))
    window._stair_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _run_stair_core_now(window, uiapp), "Froglet Stair Core"
    ))
    window._floor_clear_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _clear_floor_assignments(window, uiapp), "Froglet Clear Floor Footprints"
    ))
    window._floor_auto_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _auto_pick_floor_plans(window, uiapp), "Froglet Auto Pick Floor Plans"
    ))
    window._floor_pick_event = ExternalEvent.Create(_CallbackExternalEventHandler(
        lambda uiapp: _pick_floor_footprints(window, uiapp), "Froglet Pick Floor Footprints"
    ))

    try:
        _subscribe_created_views_live_update(window)
    except Exception as error:
        logger.warning("DKUI: не вдалося підписатися на зміни документа: %s", error)

    # The scheme of the active floor is read once at the start (a plan opened before the window); that first read only notes
    # the stage, so "0. Ознайомлення" stays open.
    try:
        window._stage_quiet = True
        window._groups_event.Raise()
    except Exception as error:
        logger.warning("DKUI: не вдалося прочитати активний вид: %s", error)


def _refresh_persistent_placement_event():
    """(Re-)create the persistent placement ExternalEvent and store it
    via dkui_bridge - called from show_apartment_selector() itself (a
    guaranteed-valid Revit API context) on every run, creation or reuse
    alike, since its handler's Execute() is Python code bound to
    whichever engine created it, which goes stale once that invocation's
    engine is gone (both pushbuttons that can open this window are
    separate persistent IronPython engines - see dkui_bridge.py's own
    docstring for why AppDomain data slots, not a plain Python global,
    are used to share state between them)."""
    handler = _CallbackExternalEventHandler(
        _execute_pending_placement_from_bridge, "DKUI Place Apartment Group"
    )
    event = ExternalEvent.Create(handler)

    if dkui_bridge is not None:
        try:
            dkui_bridge.set_persistent_placement_event(event)
        except Exception as error:
            logger.warning(
                "DKUI: не вдалося зберегти подію розміщення: %s", error
            )

    return event


def _flush_room_tags(uiapp):
    """Second try, in its own ExternalEvent (so Revit has finished the
    placement and the rooms are enclosed), for room tags that could not be
    created while the apartment was being placed."""
    uidoc = uiapp.ActiveUIDocument
    if uidoc is None or apartment_group_placement is None:
        return
    apartment_group_placement.flush_deferred_room_tags(uidoc.Document)
    apartment_group_placement.flush_pending_room_tags(uidoc.Document)
    # The apartment files bring their own tag families (some of them the project's older ones: blue text, no frame on the
    # apartment tag); every placed apartment gets the same ones (room_tag_tidy.CANONICAL_TAG_FAMILIES), right after its placement.
    try:
        import room_tag_tidy

        switched = room_tag_tidy.unify_placed(uidoc.Document)
        if switched:
            _debug_log(u"DKUI: позначки приведено до одних сімейств: {}".format(switched))
    except Exception:
        _debug_log(u"DKUI: сімейства позначок не вирівняно:\n```\n{}\n```".format(traceback.format_exc()))


def _active_design_option_problem(document):
    """None when no design option is being edited (new apartments then go
    into the Main Model); otherwise a message telling the user how to leave
    the option."""
    from Autodesk.Revit.DB import DesignOption, Element, ElementId

    try:
        active_id = DesignOption.GetActiveDesignOptionId(document)
    except Exception:
        return None

    if active_id is None or active_id == ElementId.InvalidElementId:
        return None

    try:
        name = (
            Element.Name.__get__(document.GetElement(active_id))
            .replace(u"<primary>", u"").strip()
        )
    except Exception:
        name = u"?"

    return (
        u"Квартири розміщуються в Main Model, але зараз редагується варіант "
        u"«{}».\n\nПовернися в Main Model: Manage → Design Options → Finish "
        u"Editing (або вибери «Main Model» у рядку стану внизу праворуч), "
        u"потім повтори «Запустити».".format(name)
    )


# What "Редагування геометрії" used to run on "Запустити" with both boxes ticked: the walls pulled to the footprints' limits,
# the duplicate walls, the stair/lift node, the areas of the rooms, the floor slab. It runs by itself after a floor is laid out.
_AUTO_GEOMETRY_KEYS = ["external_walls", "internal_walls", "duplicate_walls", "stair_core", "apartment_areas", "floor_slab"]


# The share of the "Work in Frogress" card (splash.py) each part of a run takes: the apartments, the bedroom check, the geometry.
_CARD_PLACE_SHARE = 0.80
_CARD_BEDROOM_SHARE = 0.85
_CARD_GEOMETRY_STEPS = 5          # (the walls inside, the node, the walls after it, the areas, the slab)


def _card():
    """The progress card module, or None when it cannot be had (the card is cosmetic: a run never depends on it)."""
    try:
        import splash

        return splash
    except Exception:
        return None


def _card_start(card):
    """Put the card up; True when it is up (the reason is logged when it is not)."""
    if card is None:
        return False
    shown = card.start()
    if not shown:
        _debug_log(u"DKUI: картка прогресу: " + card.status())
    return bool(shown)


def _run_geometry_after_floor(uiapp):
    """Right after a whole floor is laid out: the geometry edits run on every apartment placed on that floor (the same
    apartments the stage "Редагування геометрії" worked on). Never raises."""
    try:
        import apartment_edits
        import placed_apartments

        uidoc = uiapp.ActiveUIDocument
        if uidoc is None:
            return
        entries = placed_apartments.live_entries(uidoc.Document)
        try:
            import floor_copy

            if floor_copy.active_level_id(uidoc.ActiveView) is not None:
                entries = floor_copy.on_active_floor(uidoc.Document, uidoc.ActiveView, entries)
        except Exception:
            logger.warning("DKUI: квартири поверху не відфільтровано: %s", traceback.format_exc())
        group_ids = sorted(entry["groupId"] for entry in entries)
        if not group_ids:
            return
        # The card again for the last part of the run (the dialogs of the placement were between): it goes on from where it was.
        card = _card()
        shown = _card_start(card)
        steps = {"done": 0}

        def _progress(name):
            if not shown:
                return True
            if card.cancel_requested:
                return False                          # (the edits stop between two steps, never inside one)
            share = min(1.0, steps["done"] / float(_CARD_GEOMETRY_STEPS))
            card.blink(_CARD_BEDROOM_SHARE + (1.0 - _CARD_BEDROOM_SHARE) * share)
            steps["done"] += 1
            return True

        if shown:
            card.blink(_CARD_BEDROOM_SHARE)
        try:
            messages = apartment_edits.run_many(uiapp, uidoc.Document, group_ids, list(_AUTO_GEOMETRY_KEYS), _progress)
            if shown:
                card.blink(1.0)
        finally:
            if shown:
                card.stop()
        window = dkui_bridge.get_persistent_window() if dkui_bridge is not None else None
        if window is not None:
            window.StatusText.Text = u" ".join(messages)
    except Exception:
        _debug_log(u"DKUI: геометрію після розпланування не дотягнуто:\n```\n{}\n```".format(traceback.format_exc()))


def _execute_pending_placement_from_bridge(uiapp):
    """The persistent placement ExternalEvent's own Execute() callback -
    module-level and reading its request fresh from dkui_bridge rather
    than from a closure, so it's not tied to whichever click happened to
    be current when the event was (re-)created."""
    request = None
    if dkui_bridge is not None:
        try:
            request = dkui_bridge.pop_pending_run_request()
        except Exception as error:
            _debug_log(
                "DKUI: не вдалося прочитати запит на розміщення: {}".format(
                    error
                )
            )

    if request is None:
        _debug_log(
            "DKUI: _execute_pending_placement_from_bridge() спрацювала "
            "БЕЗ запиту - нічого розміщувати, виходжу без дій."
        )
        return

    # Apartments are placed into the Main Model. Revit creates new elements in
    # whichever design option is being edited and the API cannot switch it, so
    # refuse (with instructions) while an option is active.
    problem = None
    try:
        active_document = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
        if active_document is not None:
            problem = _active_design_option_problem(active_document)
    except Exception:
        problem = None

    if problem:
        forms.alert(problem, title="Froglet")
        return

    if "batch" in request:
        # Each distinct apartment file is opened once for the whole batch
        # (not once per footprint) and closed afterwards.
        # "docs": open source documents; "memo": per-file analysis results
        # reused for every footprint (see place_apartment_group).
        source_cache = {"docs": {}, "memo": {}}
        results = []
        unresolved, swaps = [], []
        cancelled = False
        # The "Work in Frogress" card for the whole run: after the dialogs above, when the work really starts; the share of
        # the apartments placed, then the bedroom check; it is taken down before the dialogs that follow.
        card = _card()
        card_shown = _card_start(card)
        total = max(1, len(request["batch"]))
        try:
            for index, single in enumerate(request["batch"]):
                if card_shown and card.cancel_requested:
                    cancelled = True                  # (the x on the card: stop between two apartments; what is placed stays)
                    break
                results.append(
                    _execute_pending_placement(
                        uiapp, single, quiet=True, source_cache=source_cache
                    )
                )
                # One UI-message pump per apartment (instead of several
                # inside each placement) keeps Revit from looking frozen.
                apartment_group_placement._pump_ui_messages()
                if card_shown:
                    card.blink(_CARD_PLACE_SHARE * (index + 1) / float(total))

            # Bedrooms on both sides of a сходово-ліфтовий вузол: apartments
            # the script chose are swapped for others (the user's are not).
            if not cancelled:
                if card_shown:
                    card.blink(_CARD_PLACE_SHARE)
                try:
                    unresolved, swaps = _resolve_bedroom_conflicts(uiapp, request["batch"], source_cache)
                except Exception:
                    _debug_log("DKUI: перевірка спалень біля вузла не вдалася:\n```\n{}\n```".format(
                        traceback.format_exc()))
                if card_shown:
                    card.blink(_CARD_BEDROOM_SHARE)
        finally:
            if card_shown:
                card.stop()                           # (always: whatever happened, before any dialog)
            for cached_doc in source_cache["docs"].values():
                try:
                    cached_doc.Close(False)
                except Exception as error:
                    _debug_log(
                        "DKUI: не вдалося закрити відкритий файл квартири: {}".format(error)
                    )
        placed = len([ok for ok in results if ok])
        summary = u"Розміщено квартир: {} з {}.".format(placed, len(results))
        if cancelled:
            summary = (u"Роботу скасовано (хрестик на картці): розміщено квартир {} з {}. Розміщене лишилось, "
                       u"геометрію не дотягнуто.").format(placed, len(request["batch"]))
        if swaps:
            summary += u"\nЗамінено квартир через спальню біля вузла: {}.".format(len(swaps))
        forms.alert(summary, title="DKUI")
        if unresolved:
            _report_bedroom_conflicts(uiapp, unresolved)
        if placed and not cancelled:
            _run_geometry_after_floor(uiapp)         # (after the report: the edits regroup the apartments)
        return

    _execute_pending_placement(uiapp, request)
    _check_stair_bedrooms(uiapp)


_BEDROOM_NEAR_STAIRS_MESSAGE = u"Біля ліфта розташована спальня кімната, слід замінити квартиру на іншу"


def _bedroom_conflicts_remote(document, entries):
    """The conflicts from the backend: the rooms of the placed apartments and, for every floor
    height, the footprints of the nodes on it go there; the answer's group ids become sets again."""
    levels = []
    for z in sorted(set(round(entry["z"], 1) for entry in entries if entry.get("z") is not None)):
        nodes = apartment_group_placement._stair_roof_polygons(document, z, None)
        levels.append({"z": z, "nodes": [[[p[0], p[1]] for p in node] for node in nodes]})
    answer = backend_api.bedroom_conflicts({
        "entries": [{
            "groupId": entry["groupId"], "z": entry.get("z"),
            "rooms": [{"t": room["t"], "p": [[p[0], p[1]] for p in room["p"]]} for room in entry["rooms"]],
        } for entry in entries],
        "levels": levels,
    })
    for note in answer["notes"]:
        _debug_log(note)
    return [{"positive": set(item["positive"]), "negative": set(item["negative"])} for item in answer["conflicts"]]


def _find_stair_bedroom_conflicts(uiapp):
    """Bedrooms (rooms with "Тип приміщення" = 1) may touch a сходово-ліфтовий
    вузол from ONE side only. Returns the conflicts - one per node and axis
    where apartments with such rooms touch it from OPPOSITE sides - as
    [{"positive": set of group ids, "negative": set of group ids}].
    Runs in an API context."""
    import math
    import placed_apartments

    uidoc = uiapp.ActiveUIDocument
    if uidoc is None or apartment_group_placement is None:
        return []
    document = uidoc.Document

    entries = [entry for entry in placed_apartments.live_entries(document) if entry.get("rooms")]
    if not entries:
        return []

    # The backend decides (core/bedrooms.find_conflicts) from the rooms and the nodes' footprints
    # read here.
    return (_bedroom_conflicts_remote(document, entries))


def _report_bedroom_conflicts(uiapp, conflicts):
    """Select the apartments of the conflicts in Revit and say what is wrong."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId

    uidoc = uiapp.ActiveUIDocument
    ids = set()
    for conflict in conflicts:
        ids.update(conflict["positive"])
        ids.update(conflict["negative"])

    selection = List[ElementId]()
    for group_id in sorted(ids):
        selection.Add(ElementId(int(group_id)))
    try:
        uidoc.Selection.SetElementIds(selection)
    except Exception:
        pass

    try:
        persistent_window = dkui_bridge.get_persistent_window() if dkui_bridge is not None else None
        if persistent_window is not None:
            persistent_window.StatusText.Text = _BEDROOM_NEAR_STAIRS_MESSAGE
    except Exception:
        pass
    forms.alert(_BEDROOM_NEAR_STAIRS_MESSAGE, title="Froglet")


def _check_stair_bedrooms(uiapp):
    """After a single placement: report a bedroom conflict, if any."""
    conflicts = _find_stair_bedroom_conflicts(uiapp)
    if not conflicts:
        return None
    _report_bedroom_conflicts(uiapp, conflicts)
    return _BEDROOM_NEAR_STAIRS_MESSAGE


def _delete_placed_apartment(document, group_id):
    """Delete a placed apartment's group (and its group type, if nothing else
    uses it)."""
    from Autodesk.Revit.DB import ElementId, Transaction

    group = document.GetElement(ElementId(int(group_id)))
    if group is None:
        return
    group_type = group.GroupType
    transaction = Transaction(document, u"Froglet: replace apartment")
    transaction.Start()
    try:
        apartment_group_placement.apply_silent_failure_handling(transaction)
        document.Delete(group.Id)
        try:
            if group_type.Groups.Size == 0:
                document.Delete(group_type.Id)
        except Exception:
            pass
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise


def _resolve_bedroom_conflicts(uiapp, requests, source_cache):
    """After a whole-floor batch: while bedrooms touch a сходово-ліфтовий вузол
    from opposite sides, swap an apartment the SCRIPT chose (request["source"]
    == "auto") for another from its pool (request["alternatives"]). The user's
    own choices (and apartments placed earlier) are never swapped. When a node's
    two sides each hold an apartment that can't be swapped, nothing is done
    (the caller reports it); when both sides can be swapped, the apartment with
    the bigger pool is. Returns (conflicts that remain, [(roof id, new name)])."""
    import placed_apartments

    document = uiapp.ActiveUIDocument.Document
    by_roof = dict((int(r["roofId"]), r) for r in requests if r.get("roofId") is not None)
    tried = {}      # roof id -> rvt paths already tried there
    stuck = set()   # roof ids whose apartment could not be swapped
    swaps = []

    def _roof_of(entries, group_id):
        entry = entries.get(group_id)
        return int(entry["roofId"]) if entry and entry.get("roofId") is not None else None

    def _pool_left(roof_id):
        request = by_roof.get(roof_id)
        if request is None or request.get("source") != "auto" or roof_id in stuck:
            return []
        used = tried.setdefault(roof_id, set([request["rvtPath"]]))
        return [alt for alt in request.get("alternatives") or [] if alt["rvtPath"] not in used]

    for _round in range(12):
        conflicts = _find_stair_bedroom_conflicts(uiapp)
        if not conflicts:
            return [], swaps

        entries = dict((e["groupId"], e) for e in placed_apartments.live_entries(document))

        def _victim_remote():
            # (core/bedrooms.choose_victim: the group ids go in the order this module's sets iterate)
            answer = backend_api.bedroom_victim({
                "conflicts": [{"positive": list(c["positive"]), "negative": list(c["negative"])} for c in conflicts],
                "groups": [{"groupId": g, "roofId": _roof_of(entries, g)}
                           for c in conflicts for g in list(c["positive"]) + list(c["negative"])],
                "requests": [{
                    "roofId": int(r["roofId"]), "source": r.get("source"), "rvtPath": r.get("rvtPath"),
                    "alternatives": [{"rvtPath": alt["rvtPath"]} for alt in r.get("alternatives") or []],
                } for r in requests if r.get("roofId") is not None],
                "tried": [{"roofId": roof_id, "paths": list(paths)} for roof_id, paths in tried.items()],
                "stuck": sorted(stuck),
            })
            return (answer["victim"],)


        victim = (_victim_remote())[0]
        if victim is None:
            return conflicts, swaps

        roof_id = _roof_of(entries, victim)
        request = by_roof[roof_id]
        swapped = _swap_apartment(uiapp, victim, roof_id, request, _pool_left(roof_id), tried, source_cache)
        if swapped is None:
            stuck.add(roof_id)
        else:
            swaps.append((roof_id, swapped.get("displayName")))
            _debug_log(u"Спальня біля вузла: квартиру на плямі {} замінено на «{}».".format(
                roof_id, swapped.get("displayName")))

    return _find_stair_bedroom_conflicts(uiapp), swaps


def _swap_apartment(uiapp, group_id, roof_id, request, alternatives, tried, source_cache):
    """Replace the placed apartment `group_id` by the first of `alternatives`
    that leaves no bedroom conflict; puts the original back (and returns None)
    when none does."""
    import placed_apartments

    document = uiapp.ActiveUIDocument.Document
    _delete_placed_apartment(document, group_id)

    for alt in alternatives:
        tried.setdefault(roof_id, set()).add(alt["rvtPath"])
        alt_request = dict(request)
        alt_request["rvtPath"] = alt["rvtPath"]
        alt_request["displayName"] = alt["displayName"]
        if not _execute_pending_placement(uiapp, alt_request, quiet=True, source_cache=source_cache):
            continue

        new_ids = [
            e["groupId"] for e in placed_apartments.live_entries(document)
            if e.get("roofId") is not None and int(e["roofId"]) == roof_id
        ]
        in_conflict = set()
        for conflict in _find_stair_bedroom_conflicts(uiapp):
            in_conflict.update(conflict["positive"])
            in_conflict.update(conflict["negative"])
        if new_ids and not (set(new_ids) & in_conflict):
            return alt
        for new_id in new_ids:
            _delete_placed_apartment(document, new_id)

    _execute_pending_placement(uiapp, request, quiet=True, source_cache=source_cache)
    return None


def _execute_pending_placement(uiapp, request, quiet=False, source_cache=None):
    """Runs inside the ExternalEvent callback, with a proper Revit API
    context - this is the only place allowed to start the Transactions
    that apartment_group_placement.place_apartment_group needs. Pure
    Revit API code - unchanged by the WebView2-to-WPF rewrite."""
    rvt_path = request["rvtPath"]
    display_name = request["displayName"]

    from Autodesk.Revit.DB import ElementId, FilteredElementCollector, Level

    document = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    application = document.Application if document else None

    level = None
    level_id = request.get("levelId")
    if document is not None and level_id is not None:
        try:
            level = document.GetElement(ElementId(int(level_id)))
        except Exception:
            level = None

    if level is None and document is not None:
        # No footprint was picked (Банк квартир opened directly by its
        # own pushbutton, not via "Вибір плями квартири") - there's no
        # levelId to resolve. place_apartment_group still needs SOME
        # valid Level object (its own required parameter), even though
        # the actual target position falls back to (0, 0, 0) regardless
        # of which level this resolves to - so the active view's own
        # level is used if it has one, else just the first level found.
        try:
            active_view = uiapp.ActiveUIDocument.ActiveView if uiapp.ActiveUIDocument else None
            level = getattr(active_view, "GenLevel", None)
        except Exception:
            level = None

        if level is None:
            try:
                level = FilteredElementCollector(document).OfClass(Level).FirstElement()
            except Exception:
                level = None

    if document is None or application is None or level is None:
        if not quiet:
            forms.alert(
                "Не вдалося розмістити квартиру: рівень плями більше "
                "недоступний у цьому документі. Виберіть пляму квартири "
                "ще раз.",
                title="DKUI",
            )
        return False

    _debug_log(
        "**DKUI: розміщення квартири «{}»...**".format(
            display_name or os.path.basename(rvt_path)
        )
    )

    try:
        placed_group = apartment_group_placement.place_apartment_group(
            document,
            application,
            rvt_path,
            level,
            request["targetXFt"],
            request["targetYFt"],
            request["targetZFt"],
            request["angleRad"],
            entrance_alignment=request.get("entranceAlignment"),
            corner_alignment=request.get("cornerAlignment"),
            footprint_polygon_ft=request.get("footprintFt"),
            source_cache=source_cache,
            through_alignment=request.get("throughAlignment"),
        )
    except Exception as error:
        _debug_log(
            "DKUI: розміщення квартири не вдалося:\n```\n{}\n```".format(
                traceback.format_exc()
            )
        )
        logger.warning("DKUI: розміщення квартири не вдалося: %s", error)
        placed_group = None

    if placed_group is None:
        if not quiet:
            forms.alert(
                "Не вдалося розмістити квартиру «{}». Подробиці - у "
                "журналі pyRevit.".format(display_name or ""),
                title="DKUI",
            )
        return False

    _debug_log(
        "**DKUI: квартиру «{}» розміщено (група Id {}).**".format(
            display_name or "", placed_group.Id.IntegerValue
        )
    )

    # Remember which footprint got this apartment (keeps the footprint
    # transparent and lists the apartment under "Розміщені квартири").
    try:
        import placed_apartments
        roof_id = request.get("roofId")
        placed_apartments.record(
            document, placed_group,
            int(roof_id) if roof_id is not None else None,
            display_name or os.path.splitext(os.path.basename(rvt_path))[0],
            rvt_path,
            rooms=apartment_group_placement.last_placed_rooms.get("rooms"),
            z=apartment_group_placement.last_placed_rooms.get("z"),
            flip=apartment_group_placement.last_placed_rooms.get("flip"),
            pending_tags=(dict(apartment_group_placement.last_pending_tags)
                          if apartment_group_placement.last_pending_tags.get("metas") else None),
        )
        # A neighbour placed earlier that may still be mirrored (Рядове, no added
        # part) is mirrored when that matches its bedroom to this apartment's.
        try:
            apartment_group_placement.rebalance_neighbours(
                document, int(placed_group.Id.Value) if hasattr(placed_group.Id, "Value")
                else int(placed_group.Id.IntegerValue),
            )
        except Exception:
            logger.warning("DKUI: сусідні квартири не вирівняно: %s", traceback.format_exc())
    except Exception:
        logger.warning("DKUI: не вдалося запам'ятати плям квартири: %s", traceback.format_exc())

    # Room tags that had to wait for their rooms to close: retried in a later event.
    try:
        persistent_window = dkui_bridge.get_persistent_window() if dkui_bridge is not None else None
        tags_event = getattr(persistent_window, "_room_tags_event", None)
        if tags_event is not None:
            tags_event.Raise()
        # The footprint→apartment record was just written: refresh the
        # transparency and the "Розміщені квартири" list.
        for event_name in ("_occupancy_event", "_placed_event"):
            event = getattr(persistent_window, event_name, None)
            if event is not None:
                event.Raise()
    except Exception:
        pass

    if quiet:
        return True

    ft_to_m = apartment_group_placement.FT_TO_M
    entrance_alignment = request.get("entranceAlignment")

    if entrance_alignment is not None:
        angle_line = "Кут: автоматично, за напрямком стіни входу (див. журнал)"
    else:
        angle_line = "Кут (за годинниковою стрілкою від півночі): {:.2f}°".format(
            math.degrees(request["angleRad"])
        )

    try:
        from Autodesk.Revit.DB import Element as _Element
        level_name = _Element.Name.__get__(level)
    except Exception:
        level_name = str(request.get("levelId"))

    forms.alert(
        "Квартиру «{}» розміщено.\n\n"
        "Файл: {}\n"
        "Рівень: {}\n"
        "Цільова точка (X, Y, Z): ({:.3f}, {:.3f}, {:.3f}) м\n"
        "{}\n"
        "Група Id: {}".format(
            display_name or os.path.basename(rvt_path),
            rvt_path,
            level_name,
            request["targetXFt"] * ft_to_m,
            request["targetYFt"] * ft_to_m,
            request["targetZFt"] * ft_to_m,
            angle_line,
            placed_group.Id.IntegerValue,
        ),
        title="DKUI",
    )
    return True


# Footprint group chosen in "Доступні групи квартир" - placement payloads, one
# per footprint. When non-empty, "Запустити" places the selected apartment on
# EVERY one of them.
_selected_group = {"placements": []}


def _build_placement_request(rvt_path, display_name, placement):
    """Turn one footprint placement payload (see "Вибір плями квартири"'s
    _store_apartment_placement) into the request dict that
    _execute_pending_placement consumes - built by the backend
    (core/placement_payload.py)."""
    def _remote():
        request = backend_api.placement_request(rvt_path, display_name, placement)
        _debug_log(
            u"**DKUI: параметри розміщення для «{}»** (backend)\n\n- Файл: `{}`\n- Рівень Id: {}\n- Режим: {}".format(
                display_name or os.path.basename(rvt_path), rvt_path, request.get("levelId"),
                placement.get("alignMode") or "footprintCenter",
            )
        )
        return request

    return (_remote())


def _request_apartment_placement(rvt_path, display_name):
    """Gather every placement input (level, target X/Y/Z, angle), then
    hand off to _execute_pending_placement via the persistent
    ExternalEvent. Runs on the UI thread from the "Запустити" button's
    Click handler - it must not touch the document itself (see
    _CallbackExternalEventHandler), only read out of dkui_bridge and
    log, both safe from here."""
    if apartment_group_placement is None:
        forms.alert(
            "Не вдалося розмістити квартиру: модуль "
            "apartment_group_placement не завантажено.",
            title="DKUI",
        )
        return

    if dkui_bridge is None:
        forms.alert(
            "Не вдалося розмістити квартиру: внутрішній міст DKUI "
            "недоступний.",
            title="DKUI",
        )
        return

    try:
        placement = dkui_bridge.get_last_apartment_placement()
    except Exception as error:
        logger.warning(
            "DKUI: не вдалося прочитати параметри розміщення: %s", error
        )
        placement = None

    if not placement:
        # Банк квартир was opened directly by its own pushbutton, not
        # via "Вибір плями квартири" first - there's no footprint/level
        # to align to. Rather than refusing outright, fall back to the
        # plain "footprintCenter" mode's own shape with target (0, 0, 0)
        # and no rotation - _execute_pending_placement resolves SOME
        # valid Level for place_apartment_group's own required
        # parameter (the active view's, or the document's first) since
        # levelId is None here.
        _debug_log(
            "DKUI: «Запустити» натиснуто без обраної плями - "
            "розміщую квартиру за замовчуванням у (0, 0, 0)."
        )
        placement = {
            "alignMode": "footprintCenter",
            "targetXFt": 0.0,
            "targetYFt": 0.0,
            "targetZFt": 0.0,
            "angleRad": 0.0,
            "levelId": None,
        }

    if _selected_group["placements"]:
        # A footprint group is selected: one placement request per footprint,
        # executed together by the same ExternalEvent.
        request = {
            "batch": [
                _build_placement_request(rvt_path, display_name, group_placement)
                for group_placement in _selected_group["placements"]
            ]
        }
    else:
        request = _build_placement_request(rvt_path, display_name, placement)

    if dkui_bridge is not None:
        try:
            dkui_bridge.set_pending_run_request(request)
        except Exception as error:
            _debug_log(
                "DKUI: не вдалося зберегти запит на розміщення: {}".format(
                    error
                )
            )

    event = dkui_bridge.get_persistent_placement_event() if dkui_bridge is not None else None

    if event is None:
        _debug_log(
            "DKUI: немає готової ExternalEvent для розміщення - "
            "закрийте і знову відкрийте «Банк квартир»."
        )
        forms.alert(
            "Внутрішня помилка: немає готової події розміщення. "
            "Закрийте і знову відкрийте «Банк квартир».",
            title="DKUI",
        )
        return

    # Raise() itself can be refused (e.g. Denied, if Revit's API is
    # currently busy with something else) without throwing - logged
    # visibly so a refusal is distinguishable from Execute() actually
    # running and failing silently inside itself.
    raise_result = event.Raise()
    _debug_log("DKUI: ExternalEvent.Raise() -> {}".format(raise_result))


# ============================================================
# FILTER / SORT / RENDER - module-level, not bound methods
#
# "Select Apartment" and "Вибір плями квартири" are different pyRevit
# buttons, each its own separate persistent IronPython engine. When one
# button's engine reuses a window the OTHER button's engine originally
# created, its custom Python-level bound methods belong to a DIFFERENT
# engine's nested window class - safe to trust for plain .NET-native
# member access (window.SomeControl.Text = "...", window.SomeControl.
# IsChecked = True, Panel.Children.Add(...)), same as it always was for
# Show()/Activate() on the WebView2-era window, but the functions that
# NEED to run across that boundary (applying a footprint payload,
# re-rendering the grid on external state changes) stay module-level
# functions taking the window instance as an explicit parameter, exactly
# like this file's WebView2-era _dispatch_web_message/_execute_pending_
# placement always did - never bound methods relied on from outside
# their own originating engine.
# ============================================================

PRIORITY_PARAMETER_KEYS_DEFAULT = [
    "Передпокій", "Вітальня", "Санвузол", "Гардероб",
    "Спальня", "Пральня", "Літні приміщення", "Кухня",
]

APARTMENT_TYPE_CODES = ["0", "1", "2", "3"]
WINDOW_PLACEMENT_CODES = ["R", "C", "L", "N"]
ADDED_PART_CODES = ["AP", "NP"]

APARTMENT_SORT_MODES = ["area-asc", "area-desc", "type-asc", "type-desc", "best-fit"]

_CARD_WIDTH_BY_SIZE = {"s": 160, "m": 230, "l": 300}
_CARD_COLUMNS_BY_SIZE = {"s": 5, "m": 3, "l": 2}


_FIXED_CARD_SIZE = "l"           # two cards in a row
_FIXED_PAGE_SIZE = 12


def _read_filter_state(window):
    """Read the current filter/sort/paging selection straight off the
    window's own native controls - the controls ARE the state, no
    separate model to keep in sync. Pure .NET member reads, safe from
    any engine."""

    def _checked_tags(toggle_buttons):
        return [
            str(button.Tag)
            for button in toggle_buttons
            if bool(button.IsChecked)
        ]

    apartment_types = _checked_tags([
        window.TypeStudioToggle, window.Type1Toggle,
        window.Type2Toggle, window.Type3Toggle,
    ])
    window_placements = _checked_tags([
        window.WPRToggle, window.WPCToggle, window.WPLToggle, window.WPNToggle,
    ])
    added_parts = _checked_tags([window.APAPToggle, window.APNPToggle])

    sort_mode = APARTMENT_SORT_MODES[0]
    if window.SortComboBox.SelectedItem is not None:
        sort_mode = str(window.SortComboBox.SelectedItem.Tag)

    # The size of the cards and the number on a page have no buttons any more: fixed.
    card_size = _FIXED_CARD_SIZE
    page_size = _FIXED_PAGE_SIZE

    return {
        "apartmentTypes": apartment_types,
        "windowPlacements": window_placements,
        "addedParts": added_parts,
        "areaMin": window.AreaMinTextBox.Text.strip(),
        "areaMax": window.AreaMaxTextBox.Text.strip(),
        "dimWidth": window.DimWidthTextBox.Text.strip(),
        "dimLength": window.DimLengthTextBox.Text.strip(),
        "dimTolerance": window.DimToleranceTextBox.Text.strip(),
        # The two sizes are still the facade and the depth the footprint gave (not typed over since).
        "dimOrdered": getattr(window, "_dim_ordered_for", None) == (
            window.DimWidthTextBox.Text.strip(), window.DimLengthTextBox.Text.strip(),
        ),
        "sortMode": sort_mode,
        "fit": getattr(window, "_fit_context", None),
        "cardSize": card_size,
        "pageSize": page_size,
    }


def _parse_number(raw_text):
    text = (raw_text or "").strip().replace(",", ".")
    if not text:
        return None, True
    try:
        value = float(text)
    except Exception:
        return None, False
    return value, True


def _validate_area(state):
    minimum, minimum_valid = _parse_number(state["areaMin"])
    maximum, maximum_valid = _parse_number(state["areaMax"])

    if not minimum_valid or not maximum_valid:
        return None, None, "Введіть коректне числове значення площі."
    if minimum is not None and maximum is not None and minimum > maximum:
        return None, None, "Мінімальна площа не може бути більшою за максимальну."
    return minimum, maximum, ""


def _validate_dimensions(state):
    width, width_valid = _parse_number(state["dimWidth"])
    length, length_valid = _parse_number(state["dimLength"])
    tolerance, tolerance_valid = _parse_number(state["dimTolerance"])

    if not width_valid or not length_valid or not tolerance_valid:
        return None, None, None, "Введіть коректні додатні розміри та невід'ємну похибку."
    if (width is None) != (length is None):
        return None, None, None, "Введіть і ширину, і довжину квартири."
    if width is not None and width <= 0:
        return None, None, None, "Введіть коректні додатні розміри та невід'ємну похибку."
    if length is not None and length <= 0:
        return None, None, None, "Введіть коректні додатні розміри та невід'ємну похибку."
    if tolerance is not None and tolerance < 0:
        return None, None, None, "Введіть коректні додатні розміри та невід'ємну похибку."

    return width, length, (tolerance or 0.0), ""


def _layout_facade_depth(item):
    """(facade, depth) of a Рядове layout: from its JSON, else its file name's sizes in order - width (the
    facade) first, depth second (layout_orientation); None without sizes."""
    if item.get("windowPlacement") != "R":
        return None
    try:
        return layout_orientation.nominal_facade_depth(item)
    except Exception:
        return None


def _fit_cost(item, fit):
    """How badly `item` suits the picked footprint(s) described by `fit`
    ({"area", "dims", "added", "footprints"}) - lower is better."""
    area, dims = fit.get("area"), fit.get("dims")
    item_area = item.get("apartmentArea")
    cost = abs(item_area - area) if (item_area is not None and area is not None) else 50.0

    first, second = item.get("apartmentDimension1"), item.get("apartmentDimension2")
    ordered = fit.get("orderedDims")
    layout = _layout_facade_depth(item) if ordered else None
    if layout is not None:
        # Рядове: the layout's facade is compared with the footprint's facade and its depth with the depth.
        cost += facade_fit.forward_cost_mm(layout, ordered) / 200.0
    elif first is not None and second is not None and dims:
        forward = abs(first - dims[0]) + abs(second - dims[1])
        swapped = abs(first - dims[1]) + abs(second - dims[0])
        cost += min(forward, swapped) / 200.0
    else:
        cost += 25.0

    # Footprints WITH an added part (Присутня): compare the outlines.
    if fit.get("added") == "AP" and fit.get("footprints") and item.get("addedPart") == "AP":
        cost += _shape_cost(item, fit)
    return cost


def _shape_cost(item, fit):
    """0..30: how far the apartment's outline (JSON) is from the footprint(s)
    (best overlap averaged over up to 3 footprints); a middling value when the
    apartment has no JSON."""
    try:
        footprints = fit["footprints"]
        cache = item.setdefault("_shapeCosts", {})
        key = fit.get("id")
        if key not in cache:
            fits = []
            for footprint in footprints[:3]:
                cells = apartment_shapes.footprint_cells(footprint)
                fit = apartment_shapes.shape_fit(cells, item.get("rvtPath")) if cells else None
                if fit is not None:
                    fits.append(fit)
            cache[key] = (1.0 - sum(fits) / len(fits)) * 30.0 if fits else 15.0
        return cache[key]
    except Exception:
        return 15.0


def _filter_and_sort_apartments(apartments, state):
    """(filtered, area_error, dim_error, empty_reason): the bank's filter and sort. It is what
    the window shows, so it is decided here, not by the backend."""
    apartment_types = state["apartmentTypes"]
    window_placements = state["windowPlacements"]
    added_parts = state["addedParts"]

    area_min, area_max, area_error = _validate_area(state)
    dim_width, dim_length, dim_tolerance, dim_error = _validate_dimensions(state)

    empty_reason = ""
    if not apartment_types:
        empty_reason = "no-type-selected"

    filtered = []
    for item in apartments:
        if apartment_types and item.get("apartmentType") not in apartment_types:
            continue

        # Fail-open for an unrecognised/blank code, and only actually
        # restrictive once fewer than all codes are selected - matches
        # the original UI's semantics exactly.
        wp_code = item.get("windowPlacement")
        if (
            window_placements
            and len(window_placements) < len(WINDOW_PLACEMENT_CODES)
            and wp_code in WINDOW_PLACEMENT_CODES
            and wp_code not in window_placements
        ):
            continue

        ap_code = item.get("addedPart")
        if (
            added_parts
            and len(added_parts) < len(ADDED_PART_CODES)
            and ap_code in ADDED_PART_CODES
            and ap_code not in added_parts
        ):
            continue

        if not area_error and (area_min is not None or area_max is not None):
            area_value = item.get("apartmentArea")
            if area_value is None:
                continue
            if area_min is not None and area_value < area_min:
                continue
            if area_max is not None and area_value > area_max:
                continue

        if not dim_error and dim_width is not None and dim_length is not None:
            item_w = item.get("apartmentDimension1")
            item_l = item.get("apartmentDimension2")
            if item_w is None or item_l is None:
                continue
            # Рядове footprint (the sizes are its facade and its depth): a layout whose facade is known
            # has to match side for side - the swapped reading would put its windows on the wrong side.
            layout = _layout_facade_depth(item) if state.get("dimOrdered") else None
            if layout is not None:
                if not facade_fit.forward_match(layout, dim_width, dim_length, dim_tolerance):
                    continue
                filtered.append(item)
                continue
            matches_forward = (
                abs(item_w - dim_width) <= dim_tolerance
                and abs(item_l - dim_length) <= dim_tolerance
            )
            matches_swapped = (
                abs(item_w - dim_length) <= dim_tolerance
                and abs(item_l - dim_width) <= dim_tolerance
            )
            if not (matches_forward or matches_swapped):
                continue

        filtered.append(item)

    if not filtered and not empty_reason:
        if apartments and area_min is None and area_max is None and dim_width is None:
            if window_placements and len(window_placements) < len(WINDOW_PLACEMENT_CODES):
                empty_reason = "window-placement"
            elif added_parts and len(added_parts) < len(ADDED_PART_CODES):
                empty_reason = "added-part"
        elif dim_width is not None:
            empty_reason = "dimensions"
        elif area_min is not None or area_max is not None:
            empty_reason = "area"

    sort_mode = state["sortMode"]

    if sort_mode == "best-fit" and state.get("fit"):
        # Most suitable first: area, nominal size and - for footprints with an
        # added part - how well the apartment's outline (from its JSON) fits
        # the footprint.
        fit = state["fit"]
        filtered.sort(key=lambda item: _fit_cost(item, fit))
    elif sort_mode in ("area-asc", "area-desc", "best-fit"):
        if sort_mode == "best-fit":
            sort_mode = "area-asc"    # no footprint picked yet: nothing to fit to
        reverse_flag = sort_mode == "area-desc"

        def _area_key(item):
            value = item.get("apartmentArea")
            # Missing area always sorts last, in EITHER direction -
            # applying reverse= to the whole tuple would instead put it
            # first on a descending sort, which reads as broken.
            missing = value is None
            numeric = value if value is not None else 0.0
            return (missing, -numeric if reverse_flag else numeric)

        filtered.sort(key=_area_key)
    else:
        filtered.sort(
            key=lambda item: item.get("apartmentType") or "",
            reverse=(sort_mode == "type-desc"),
        )

    return filtered, area_error, dim_error, empty_reason


_DIM_TOLERANCE_STEP_MM = 200


def _widen_until_match(window, minimum=1):
    """If the filters (just filled from a picked footprint) match fewer than
    `minimum` apartments, widen them together - area range by 1 m² at both ends
    AND the nominal-size tolerance by 200 mm per step - until `minimum` match
    (or as many as exist).
    Returns {"area": (min, max) or None, "tolerance": mm or None} if it
    widened, else None. Does nothing when no apartment fits the OTHER
    filters at all (widening could not help)."""
    state = _read_filter_state(window)
    result = _widen_filters(window._all_apartments, state, minimum)
    if result:
        if result["area"]:
            window.AreaMinTextBox.Text = str(result["area"][0])
            window.AreaMaxTextBox.Text = str(result["area"][1])
        if result["tolerance"] is not None:
            window.DimToleranceTextBox.Text = str(result["tolerance"])
    return result


def _widen_filters(apartments, state, minimum):
    """The widening, without touching any widget: the filters are widened together
    until enough apartments match."""
    low, low_ok = _parse_number(state["areaMin"])
    high, high_ok = _parse_number(state["areaMax"])
    width, width_ok = _parse_number(state["dimWidth"])
    length, length_ok = _parse_number(state["dimLength"])
    tolerance, tolerance_ok = _parse_number(state["dimTolerance"])

    use_area = (
        low_ok and high_ok and low is not None and high is not None and low <= high
    )
    use_dims = width_ok and length_ok and tolerance_ok and width is not None and length is not None
    if not use_area and not use_dims:
        return None

    tolerance = tolerance or 0.0

    def _count(area_low, area_high, dim_tolerance):
        trial = dict(state)
        if use_area:
            trial["areaMin"] = str(area_low)
            trial["areaMax"] = str(area_high)
        if use_dims:
            trial["dimTolerance"] = str(dim_tolerance)
        return len(_filter_and_sort_apartments(apartments, trial)[0])

    if _count(low, high, tolerance) >= minimum:
        return None
    everything = _count(0, 100000, 100000)
    if not everything:
        return None
    minimum = min(minimum, everything)

    new_low, new_high, new_tolerance = low, high, tolerance
    for _ in range(1000):
        if use_area:
            new_low = max(0, new_low - 1)
            new_high = new_high + 1
        if use_dims:
            new_tolerance = new_tolerance + _DIM_TOLERANCE_STEP_MM
        if _count(new_low, new_high, new_tolerance) >= minimum:
            break
    else:
        return None

    result = {"area": None, "tolerance": None}
    if use_area:
        result["area"] = (int(round(new_low)), int(round(new_high)))
    if use_dims:
        result["tolerance"] = int(round(new_tolerance))
    return result


def _add_closest_room_counts(window, type_value):
    """When the bank has NO apartment with the picked footprint's room count that
    also fits its window placement and added part (the filters are already set),
    check the closest room count's toggle as well - the closest group that has
    such apartments (3к -> 2к; for 2к: 1к and 3к together). Area and size play no
    part here: they are widened afterwards. Returns the room counts it added."""
    if not type_value or str(type_value) not in APARTMENT_TYPE_CODES:
        return []
    type_value = str(type_value)

    state = _read_filter_state(window)
    added = _closest_room_counts(window._all_apartments, state, type_value)
    if added:
        keep = [type_value] + list(added)
        for button in (
            window.TypeStudioToggle, window.Type1Toggle, window.Type2Toggle, window.Type3Toggle,
        ):
            button.IsChecked = str(button.Tag) in keep
    return list(added)


def _closest_room_counts(apartments, state, type_value):
    """The room counts to
    add ([] when `type_value` itself has apartments or none are close)."""
    trial = dict(state)
    trial["areaMin"] = u""
    trial["areaMax"] = u""
    trial["dimWidth"] = u""
    trial["dimLength"] = u""

    def _count(codes):
        trial["apartmentTypes"] = list(codes)
        return len(_filter_and_sort_apartments(apartments, trial)[0])

    if _count([type_value]) > 0:
        return []

    for group in _closest_type_groups(apartments, type_value):
        if _count(group) > 0:
            return list(group)
    return []


def _defer_footprint_payload(window, payload):
    """The parameters of the chosen footprints go to the filters of the bank only when the "Банк квартир" tab is looked at
    (`_flush_footprint_payload`): setting the filters re-renders the grid of cards, which made every click on the scheme
    wait for it. What "Автоматичний вибір" ranks by is remembered at once."""
    window._last_pick_payload = payload or {}
    window._deferred_payload = payload or {}


def _flush_footprint_payload(window):
    payload = getattr(window, "_deferred_payload", None)
    if payload is None:
        return
    window._deferred_payload = None
    _apply_footprint_payload(window, payload)


def _apply_footprint_payload(window, payload):
    """Called (possibly from a DIFFERENT engine than the one that
    created this window - see this section's own header comment) when
    "Вибір плями квартири" picks a footprint while the bank window is
    already open: force-selects the matching filters and jumps back to
    page 1, mirroring what app.js's window.__DKUI_APPLY_FOOTPRINT_
    FILTERS__ used to do. Pure .NET member sets only."""
    payload = payload or {}
    window._deferred_payload = None
    window._last_pick_payload = payload        # what "Автоматичний вибір" ranks the bank by

    type_value = payload.get("apartmentType")
    if type_value and str(type_value) in APARTMENT_TYPE_CODES:
        for button in (
            window.TypeStudioToggle, window.Type1Toggle,
            window.Type2Toggle, window.Type3Toggle,
        ):
            button.IsChecked = (str(button.Tag) == str(type_value))

    wp_value = payload.get("windowPlacement")
    if wp_value and str(wp_value) in WINDOW_PLACEMENT_CODES:
        for button in (
            window.WPRToggle, window.WPCToggle, window.WPLToggle, window.WPNToggle,
        ):
            button.IsChecked = (str(button.Tag) == str(wp_value))

    ap_value = payload.get("addedPart")
    if ap_value and str(ap_value) in ADDED_PART_CODES:
        for button in (window.APAPToggle, window.APNPToggle):
            button.IsChecked = (str(button.Tag) == str(ap_value))

    # No apartment with this room count, window placement and added part (e.g. no
    # 3к Рядове): the closest room count is added to the filter - 3к -> 2к.
    added_types = _add_closest_room_counts(window, type_value)

    if payload.get("areaMin") is not None:
        window.AreaMinTextBox.Text = str(payload.get("areaMin"))
    if payload.get("areaMax") is not None:
        window.AreaMaxTextBox.Text = str(payload.get("areaMax"))

    window._dim_ordered_for = None
    if payload.get("dimWidth") is not None and payload.get("dimLength") is not None:
        window.DimWidthTextBox.Text = str(payload.get("dimWidth"))
        window.DimLengthTextBox.Text = str(payload.get("dimLength"))
        window.DimToleranceTextBox.Text = str(payload.get("dimTolerance", 500))
        if payload.get("dimOrdered"):
            window._dim_ordered_for = (
                window.DimWidthTextBox.Text.strip(), window.DimLengthTextBox.Text.strip(),
            )

    # What "Найбільш підходяще" measures against: the picked footprint(s).
    area_low, area_high = payload.get("areaMin"), payload.get("areaMax")
    window._fit_counter = getattr(window, "_fit_counter", 0) + 1
    window._fit_context = {
        "id": window._fit_counter,
        "area": (area_low + area_high) / 2.0 if (area_low is not None and area_high is not None) else None,
        "dims": (
            (float(payload["dimWidth"]), float(payload["dimLength"]))
            if payload.get("dimWidth") is not None and payload.get("dimLength") is not None else None
        ),
        "added": payload.get("addedPart"),
        "footprints": payload.get("footprintsFt") or [],
        # Рядове: each footprint's (facade, depth); the layouts are compared side for side.
        "orderedDims": (
            [tuple(dims) for dims in payload.get("orderedDims") or []] if payload.get("dimOrdered") else None
        ) or None,
    }
    for combo_item in window.SortComboBox.Items:
        if str(combo_item.Tag) == "best-fit":
            window.SortComboBox.SelectedItem = combo_item
            break

    # A single picked footprint should leave at least 3 suggestions.
    widened = _widen_until_match(window, int(payload.get("minMatches") or 1))

    window._current_page = 1
    _render_grid_now(window, save=True)

    room_count_note = u""
    if added_types:
        room_count_note = u" Квартир {}к з такими параметрами немає - додано {}к.".format(
            _APARTMENT_TYPE_SHORT_LABELS.get(str(type_value), type_value).rstrip(u"к"),
            u", ".join(_APARTMENT_TYPE_SHORT_LABELS.get(code, code).rstrip(u"к") for code in added_types),
        )

    if widened:
        parts = []
        if widened["area"]:
            parts.append(u"площу до {}–{} м²".format(*widened["area"]))
        if widened["tolerance"] is not None:
            parts.append(u"похибку розмірів до {} мм".format(widened["tolerance"]))
        window.StatusText.Text = (
            u"Параметри передані з обраної плями. Розширено " + u" та ".join(parts)
            + u", бо точних збігів не було." + room_count_note
        )
    else:
        window.StatusText.Text = u"Параметри квартири передані з обраної плями." + room_count_note


_APARTMENT_TYPE_SHORT_LABELS = {
    "0": u"Студіо", "1": u"1к", "2": u"2к", "3": u"3к",
}


def _apartment_type_short_label(type_code):
    return _APARTMENT_TYPE_SHORT_LABELS.get(type_code, "")


# Full-word labels for a detail tab's own parameters panel - same wording
# as the filter pills in ui.xaml (WPRToggle/WPCToggle/... and APAPToggle/
# APNPToggle), just looked up here instead of hardcoded in XAML.
_WINDOW_PLACEMENT_FULL_LABELS = {
    "R": u"Рядове", "C": u"Кутове", "L": u"Наскрізне", "N": u"Торцеве",
}
_ADDED_PART_FULL_LABELS = {
    "AP": u"Присутня", "NP": u"Відсутня",
}


def _confirm_apartment(window, item, confirm_only=False):
    """Select `item` and start its placement - the shared body of
    "Запустити", card double-click and the detail tab's own button.
    `confirm_only` (the double click on a card): in the floor-layout mode it only confirms the choice, even for
    the last footprint - placing everything is left to "Запустити"."""
    window._selected_apartment = item

    if getattr(window, "_floor", None):
        # Floor-layout mode: confirm (or, when complete, place everything).
        if _floor_all_assigned(window) and not confirm_only:
            _run_floor_layout(window)
        else:
            _confirm_floor_choice(window, item, quiet=confirm_only and _floor_all_assigned(window))
        return

    _log_priority_order(window._priority_order)
    _debug_log("**DKUI: натиснуто «Запустити»**")
    _request_apartment_placement(item.get("rvtPath"), item.get("displayName"))


def _apartment_tab_key(item):
    """Stable identity for an apartment's own detail tab - rvtPath (the
    actual source file placement reads from) when available, falling
    back to the thumbnail path - so re-clicking the same card reuses its
    already-open tab (see _open_apartment_detail_tab) instead of piling
    up duplicates."""
    return item.get("rvtPath") or item.get("path")


def _build_apartment_detail_tab_content(item, window):
    """The content of one apartment's own Chrome-like detail tab - two
    columns, matching the reference layout: LEFT is the card's own look
    scaled up (big picture with its type/area badge overlaid top-left,
    a divider, then the filename underneath - see _build_apartment_card),
    RIGHT is a plain "Параметри:" list. Not interactive - selecting/
    running a placement still only happens from the card grid on the
    pinned "Банк квартир" tab."""
    from System.Windows import Thickness, CornerRadius, TextWrapping, TextAlignment, HorizontalAlignment, VerticalAlignment, GridLength
    from System.Windows.Controls import (
        Border, StackPanel, TextBlock, Image, ScrollViewer, ScrollBarVisibility, Grid, ColumnDefinition,
    )
    from System.Windows.Media import Brushes, Stretch

    root = Grid()
    root.Margin = Thickness(14, 14, 14, 0)
    # LEFT takes all remaining width, RIGHT (parameters) is a fixed 350px
    # column docked to the window edge.
    root.ColumnDefinitions.Add(ColumnDefinition(MinWidth=200))
    root.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(24)))
    root.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(450), MinWidth=250))

    # ============ LEFT: picture (+ badge) fills the space, filename pinned to the bottom ============
    from System.Windows.Controls import RowDefinition
    left = Grid()
    left.RowDefinitions.Add(RowDefinition())
    left.RowDefinitions.Add(RowDefinition(Height=GridLength.Auto))
    bottom = StackPanel()
    Grid.SetRow(bottom, 1)

    image_overlay = Grid()

    image = Image()
    image.Stretch = Stretch.Uniform
    # Decoded only a little above the display cap (the left column's own
    # 760px width) instead of 1200 - the extra pixels past what's ever
    # actually shown were pure wasted decode time (a large part of the
    # ~5s tab-open delay), with no visible sharpness benefit since
    # Stretch=Uniform downscales it right back down anyway.
    _load_bitmap_async(item.get("path"), 1600, image, sticky=True)
    image_overlay.Children.Add(image)

    # Same type/area badge as the card's own thumbnail (_build_apartment_
    # card) - just a bit larger here to match this tab's own bigger scale.
    type_label = _apartment_type_short_label(item.get("apartmentType"))
    area_value = item.get("apartmentArea")


    Grid.SetRow(image_overlay, 0)
    left.Children.Add(image_overlay)

    divider = Border()
    divider.Height = 1
    divider.Background = _card_divider_brush()
    divider.Margin = Thickness(0, 12, 0, 10)
    bottom.Children.Add(divider)

    file_label = TextBlock()
    file_label.Text = item.get("displayName") or item.get("name") or ""
    file_label.FontFamily = _avr_display_font_family()
    file_label.FontSize = 19
    file_label.FontWeight = _semi_bold_weight()
    file_label.TextAlignment = TextAlignment.Center
    file_label.TextWrapping = TextWrapping.Wrap
    bottom.Children.Add(file_label)
    left.Children.Add(bottom)

    Grid.SetColumn(left, 0)
    root.Children.Add(left)

    # ============ RIGHT: "Параметри:" list ============
    right = StackPanel()
    right.VerticalAlignment = VerticalAlignment.Top

    params_header = TextBlock()
    params_header.Text = u"Параметри:"
    params_header.FontFamily = _avr_display_font_family()
    params_header.FontWeight = _semi_bold_weight()
    params_header.FontSize = 22
    params_header.Margin = Thickness(0, 0, 0, 14)
    right.Children.Add(params_header)

    def _param_row(label_text, value_text):
        row = Grid()
        row.Margin = Thickness(0, 0, 0, 8)
        row.ColumnDefinitions.Add(ColumnDefinition(Width=GridLength(150)))
        row.ColumnDefinitions.Add(ColumnDefinition())

        label = TextBlock()
        label.Text = label_text
        label.Foreground = _muted_brush()
        label.TextWrapping = TextWrapping.Wrap
        row.Children.Add(label)

        value = TextBlock()
        value.Text = value_text if value_text else u"—"
        value.TextWrapping = TextWrapping.Wrap
        value.FontWeight = _semi_bold_weight()
        Grid.SetColumn(value, 1)
        row.Children.Add(value)

        return row

    area_text_full = u"{:.2f} м²".format(float(area_value)).replace(".", ",") if area_value is not None else None
    dim1 = item.get("apartmentDimension1")
    dim2 = item.get("apartmentDimension2")
    dims_text = u"{:.0f} × {:.0f} мм".format(float(dim1), float(dim2)) if dim1 is not None and dim2 is not None else None
    wp_label = _WINDOW_PLACEMENT_FULL_LABELS.get(item.get("windowPlacement"))
    ap_label = _ADDED_PART_FULL_LABELS.get(item.get("addedPart"))
    right.Children.Add(_param_row(u"Тип квартири", type_label))
    right.Children.Add(_param_row(u"Загальна площа", area_text_full))
    living_slot = StackPanel()          # "Житлова площа": filled once the JSON has been read
    right.Children.Add(living_slot)
    right.Children.Add(_param_row(u"Номінальні розміри", dims_text))
    right.Children.Add(_param_row(u"Розташування вікон", wp_label))
    right.Children.Add(_param_row(u"Додана частина", ap_label))
    rooms_slot = StackPanel()           # the rooms: filled once the JSON has been read
    right.Children.Add(rooms_slot)

    def _show_details(described):
        # (on the UI thread, when the JSON has been read - the tab itself was shown long before)
        if not described:
            return
        living_area = (described.get("summary") or {}).get("areaLiving")
        if living_area is not None:
            # float(): IronPython 2.7 refuses a precision for an int, which the JSON may hold
            living_slot.Children.Add(_param_row(u"Житлова площа", u"{:.2f} м²".format(float(living_area)).replace(".", ",")))
        for row in _apartment_room_rows(item, described, _param_row):
            rooms_slot.Children.Add(row)

    _load_details_async(item, living_slot, _show_details)
    # The list is long: it scrolls instead of running off the window.
    right_scroll = ScrollViewer()
    right_scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
    right_scroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
    right_scroll.Content = right
    Grid.SetColumn(right_scroll, 2)
    root.Children.Add(right_scroll)

    # Draggable divider (same look as the main tab): a transparent 24px-wide
    # GridSplitter as the grab area with a thin visible line centred on it.
    from System.Windows.Controls import GridSplitter, GridResizeBehavior
    from System.Windows.Media import SolidColorBrush, Color
    splitter = GridSplitter()
    splitter.Width = 24
    splitter.HorizontalAlignment = HorizontalAlignment.Stretch
    splitter.VerticalAlignment = VerticalAlignment.Stretch
    splitter.Background = Brushes.Transparent
    splitter.ResizeBehavior = GridResizeBehavior.PreviousAndNext
    Grid.SetColumn(splitter, 1)
    root.Children.Add(splitter)

    divider_line = Border()
    divider_line.Width = 2
    divider_line.HorizontalAlignment = HorizontalAlignment.Center
    divider_line.VerticalAlignment = VerticalAlignment.Stretch
    divider_line.Background = SolidColorBrush(Color.FromRgb(0xC8, 0xCB, 0xCF))
    divider_line.IsHitTestVisible = False
    Grid.SetColumn(divider_line, 1)
    root.Children.Add(divider_line)

    # Same position/size/style as RunButton on the "Банк квартир" tab: bottom-
    # right, 10px window margin, 8px above it.
    from System.Windows.Controls import Button, RowDefinition as _RowDef

    outer = Grid()
    outer.Margin = Thickness(10)
    outer.RowDefinitions.Add(_RowDef())
    outer.RowDefinitions.Add(_RowDef(Height=GridLength.Auto))
    Grid.SetRow(root, 0)
    outer.Children.Add(root)

    run_button = Button()
    run_button.Content = _run_button_text(window)
    # Kept in sync with the bank tab's button (floor-layout mode).
    if not hasattr(window, "_detail_run_buttons"):
        window._detail_run_buttons = []
    window._detail_run_buttons.append(run_button)
    run_button.Style = window.FindResource("PrimaryActionButtonStyle")
    run_button.MinWidth = 130
    run_button.Padding = Thickness(18, 9, 18, 9)
    run_button.Margin = Thickness(0, 8, 0, 0)
    run_button.HorizontalAlignment = HorizontalAlignment.Right
    run_button.VerticalAlignment = VerticalAlignment.Center

    def _on_tab_run_click(sender, args):
        _confirm_apartment(window, item)
        _render_grid_now(window)
        window.StatusText.Text = "Обрано: " + (item.get("displayName") or item.get("name") or "")

    run_button.Click += _on_tab_run_click
    Grid.SetRow(run_button, 1)
    outer.Children.Add(run_button)

    return outer


def _apartment_details(item):
    """The JSON's description of the apartment (apartment_details.describe), or None. What the
    apartment's tab shows is the window's own business, so it is read and worked out here."""
    rvt_path = item.get("rvtPath")
    if not rvt_path:
        return None

    try:
        data = apartment_details.load(rvt_path)
        return apartment_details.describe(data) if data else None
    except Exception:
        _debug_log(u"DKUI: не вдалося прочитати JSON квартири:\n```\n{}\n```".format(traceback.format_exc()))
        return None


def _apartment_room_rows(item, described, param_row):
    """The rooms of an apartment, one row each in the style of the parameters above them: the name,
    and in the value column its area, its size and - for the "Житлова кімната" of a 1k / 2k / 3k
    apartment only (not a studio) - the room it is entered from."""
    with_doors = str(item.get("apartmentType")) in ("1", "2", "3")
    rows = []
    for room in (described or {}).get("rooms") or []:
        living = u"житлова" in (room.get("name") or u"").lower()
        title, lines = apartment_details.format_room(room, with_doors and living)
        rows.append(param_row(title, u"\n".join(lines)))
    return rows


_details_cache = {}     # rvt path -> the JSON's description, for this session (cleared on a rescan)


def _load_details_async(item, element, on_ready):
    """on_ready(described) on the UI thread once the JSON has been read. Reading it (a few hundred KB
    over the file share, parsed by IronPython) takes the better part of a second: doing that while the
    tab opens froze the window, so the tab is shown first and this fills in. A file read before is
    answered at once."""
    from System import Action
    from System.Windows.Threading import DispatcherPriority

    rvt_path = item.get("rvtPath")
    dispatcher = element.Dispatcher

    def _deliver(described):
        try:
            on_ready(described)
        except Exception:
            _debug_log(u"DKUI: дані з JSON у вкладці квартири не показано:\n```\n{}\n```".format(traceback.format_exc()))

    if rvt_path and rvt_path in _details_cache:
        _deliver(_details_cache[rvt_path])
        return

    def _work():
        described = None
        try:
            described = _apartment_details(item)
        except Exception:
            _debug_log(u"DKUI: не вдалося прочитати JSON квартири:\n```\n{}\n```".format(traceback.format_exc()))
        if rvt_path and described is not None:
            _details_cache[rvt_path] = described
        dispatcher.BeginInvoke(DispatcherPriority.Background, Action(lambda: _deliver(described)))

    _run_in_thread_pool(_work)

_tab_drag = {"tab": None, "start": None}


def _permanent_tabs(window):
    """The tabs that are never dragged: "Розпланування поверху" and "Банк квартир" (the apartment detail tabs opened
    from a card are the movable ones)."""
    return (window.PickTabItem, window.BankTabItem)


def _is_permanent_tab(window, tab):
    return any(tab is permanent for permanent in _permanent_tabs(window))


def _leading_permanent_count(window):
    """How many tabs at the start of the strip are permanent: a dragged tab never goes ahead of them."""
    count = 0
    for tab in window.MainTabControl.Items:
        if not _is_permanent_tab(window, tab):
            break
        count += 1
    return count


def _close_detail_tab(window, tab_item, key):
    items = window.MainTabControl.Items
    index = items.IndexOf(tab_item)
    was_selected = window.MainTabControl.SelectedItem is tab_item

    try:
        items.Remove(tab_item)
    except Exception:
        pass
    window._apartment_detail_tabs.pop(key, None)

    if was_selected and items.Count:
        window.MainTabControl.SelectedIndex = max(0, min(index, items.Count - 1))

    _schedule_tab_overflow_update(window)


def _move_dragged_tab(window, target_tab):
    """Live reorder while dragging: put the dragged tab at the target's
    position, never ahead of the permanent tabs."""
    dragged = _tab_drag["tab"]
    items = window.MainTabControl.Items

    if dragged is None or dragged is target_tab or dragged not in items:
        return

    target_index = max(items.IndexOf(target_tab), _leading_permanent_count(window))
    if items.IndexOf(dragged) == target_index:
        return

    items.Remove(dragged)
    items.Insert(min(target_index, items.Count), dragged)
    window.MainTabControl.SelectedItem = dragged
    _schedule_tab_overflow_update(window)


def make_tab_drag_over(window, target_tab):
    def _handler(sender, args):
        from System.Windows import DragDropEffects

        if _tab_drag["tab"] is not None:
            _move_dragged_tab(window, target_tab)
            args.Effects = DragDropEffects.Move
        else:
            args.Effects = getattr(DragDropEffects, "None")
        args.Handled = True

    return _handler


def _attach_tab_drag_reorder(window, tab_item, close_button):
    """Detail tabs can be dragged left/right to reorder; the two permanent
    tabs cannot be dragged and always stay first."""
    from System.Windows import DragDrop, DragDropEffects, SystemParameters
    from System.Windows.Input import MouseButtonState

    def _down(sender, args):
        _tab_drag["start"] = args.GetPosition(tab_item)

    def _move(sender, args):
        start = _tab_drag["start"]
        if (
            start is None
            or args.LeftButton != MouseButtonState.Pressed
            or close_button.IsMouseOver
        ):
            return

        position = args.GetPosition(tab_item)
        if (
            abs(position.X - start.X) < SystemParameters.MinimumHorizontalDragDistance
            and abs(position.Y - start.Y) < SystemParameters.MinimumVerticalDragDistance
        ):
            return

        _tab_drag["start"] = None
        _tab_drag["tab"] = tab_item
        try:
            DragDrop.DoDragDrop(tab_item, tab_item, DragDropEffects.Move)
        finally:
            _tab_drag["tab"] = None

    tab_item.PreviewMouseLeftButtonDown += _down
    tab_item.PreviewMouseMove += _move
    tab_item.DragOver += make_tab_drag_over(window, tab_item)


_overflow_state = {"busy": False}


def _schedule_tab_overflow_update(window):
    """Run _update_tab_overflow after the current layout pass."""
    from System import Action
    from System.Windows.Threading import DispatcherPriority

    window.Dispatcher.BeginInvoke(
        DispatcherPriority.Loaded, Action(lambda: _update_tab_overflow(window))
    )


def _update_tab_overflow(window):
    """Keep the tab strip to ONE row: tabs that do not fit are collapsed and
    listed in the dropdown button at the top right. The selected tab is
    always kept visible."""
    from System.Windows import Visibility

    if _overflow_state["busy"]:
        return
    _overflow_state["busy"] = True
    try:
        control = window.MainTabControl
        items = list(control.Items)
        detail_tabs = [tab for tab in items if not _is_permanent_tab(window, tab)]
        permanent_tabs = [tab for tab in items if _is_permanent_tab(window, tab)]

        # Natural width of each tab: DesiredSize from an unconstrained
        # Measure. ActualWidth is NOT usable - while the strip is wrapped
        # onto several rows, TabPanel stretches each tab to fill its row.
        from System import Double
        from System.Windows import Size

        unbounded = Size(Double.PositiveInfinity, Double.PositiveInfinity)
        widths = {}
        for tab in items:
            tab.Visibility = Visibility.Visible
            tab.Measure(unbounded)
            widths[tab] = tab.DesiredSize.Width

        available = control.ActualWidth - 18 - 180
        used = sum(widths[tab] for tab in permanent_tabs)
        selected = control.SelectedItem

        visible = set()
        if selected in detail_tabs:
            used += widths[selected]
            visible.add(selected)

        for tab in detail_tabs:
            if tab in visible:
                continue
            if used + widths[tab] <= available:
                used += widths[tab]
                visible.add(tab)

        hidden_tabs = [tab for tab in detail_tabs if tab not in visible]
        for tab in hidden_tabs:
            tab.Visibility = Visibility.Collapsed

        overflow_visibility = Visibility.Visible if hidden_tabs else Visibility.Collapsed
        window.TabOverflowButton.Visibility = overflow_visibility
        window.TabOverflowSeparator.Visibility = overflow_visibility
        window._overflow_hidden_tabs = hidden_tabs
    finally:
        _overflow_state["busy"] = False


def _wire_tab_overflow(window):
    from System.Windows.Controls import ContextMenu, MenuItem, TextBlock as _TB

    window._overflow_hidden_tabs = []

    def _open_menu(sender, args):
        menu = ContextMenu()
        for tab in window._overflow_hidden_tabs:
            header = tab.Header
            label = u""
            try:
                for child in header.Children:
                    if isinstance(child, _TB):
                        label = child.Text
                        break
            except Exception:
                pass

            entry = MenuItem()
            entry.Header = label or u"Квартира"

            def _make_select(target):
                def _select(s, a):
                    window.MainTabControl.SelectedItem = target
                return _select

            entry.Click += _make_select(tab)
            menu.Items.Add(entry)

        menu.PlacementTarget = window.TabOverflowButton
        menu.IsOpen = True

    def _selection_changed(sender, args):
        if args.OriginalSource is window.MainTabControl:
            _schedule_tab_overflow_update(window)
            if window.MainTabControl.SelectedItem is window.BankTabItem:
                try:
                    _flush_footprint_payload(window)       # the filters follow the chosen footprints now
                except Exception:
                    _debug_log(u"DKUI: фільтри банку не оновлено:\n```\n{}\n```".format(traceback.format_exc()))
            if (
                window.MainTabControl.SelectedItem is window.PickTabItem
                and window._views_refresh_event is not None
            ):
                window._views_refresh_event.Raise()
                if window._groups_event is not None:
                    window._groups_event.Raise()

    window.TabOverflowButton.Click += _open_menu
    window.MainTabControl.SelectionChanged += _selection_changed
    window.SizeChanged += lambda s, a: _schedule_tab_overflow_update(window)


_MANUAL_URL = "https://app.notion.com/p/avrbim/Froglet_-40fba419a45e8064ad65e927f53fd3fe"


def _open_manual(url=_MANUAL_URL):
    """Open the manual in the user's browser."""
    from System.Diagnostics import Process, ProcessStartInfo

    info = ProcessStartInfo(url)
    info.UseShellExecute = True
    Process.Start(info)


def _wire_manual_link(window):
    """"Мануал користування" (bottom left): underlined under the mouse, opens the manual on a click."""
    def _click(sender, args):
        args.Handled = True
        try:
            _open_manual()
        except Exception:
            _debug_log(u"DKUI: мануал не відкрито:\n```\n{}\n```".format(traceback.format_exc()))
            window.StatusText.Text = u"Не вдалося відкрити мануал: " + _MANUAL_URL

    window.ManualLink.MouseEnter += _on_view_label_enter
    window.ManualLink.MouseLeave += _on_view_label_leave
    window.ManualLink.MouseLeftButtonUp += _click


def _fit_window_to_screen(window):
    """The window opens at about 820 x 1000 but never taller than the screen's work area."""
    from System.Windows import SystemParameters

    try:
        window.Height = min(window.Height, SystemParameters.WorkArea.Height - 30)
    except Exception:
        pass


def _wire_caption_buttons(window):
    """Min/max/close for the custom title bar (the tab strip row)."""
    from System.Windows import Thickness, WindowState, SystemParameters

    def _minimize(sender, args):
        window.WindowState = WindowState.Minimized

    def _toggle_maximize(sender, args):
        window.WindowState = (
            WindowState.Normal
            if window.WindowState == WindowState.Maximized
            else WindowState.Maximized
        )

    def _close(sender, args):
        window.Close()

    def _state_changed(sender, args):
        maximized = window.WindowState == WindowState.Maximized
        # A chrome-less maximised window overflows the screen by the resize
        # border - pad it back in, and swap the glyph to "restore".
        window.RootContentGrid.Margin = (
            SystemParameters.WindowResizeBorderThickness if maximized else Thickness(0)
        )
        window.MaximizeButton.Content = u"\uE923" if maximized else u"\uE922"

    window.MinimizeButton.Click += _minimize
    window.MaximizeButton.Click += _toggle_maximize
    window.CloseWindowButton.Click += _close
    window.StateChanged += _state_changed
    _state_changed(None, None)        # the window opens normal (ui.xaml, about 820 x 1000); this sets the padding and the glyph


def _make_close_cross(on_close):
    """The small ✕ of a closable tab (a Border with a hover background): `on_close()` runs when it is released;
    the tab itself is not selected by pressing it. Returns the Border."""
    from System.Windows import CornerRadius, HorizontalAlignment, Thickness, VerticalAlignment
    from System.Windows.Controls import Border, TextBlock
    from System.Windows.Input import Cursors
    from System.Windows.Media import Brushes

    cross_text = TextBlock()
    cross_text.Text = u"✕"
    cross_text.FontSize = 9
    cross_text.Foreground = _muted_brush()
    cross_text.HorizontalAlignment = HorizontalAlignment.Center
    cross_text.VerticalAlignment = VerticalAlignment.Center

    close_button = Border()
    close_button.Child = cross_text
    close_button.Background = Brushes.Transparent
    close_button.Padding = Thickness(5, 3, 5, 3)
    close_button.Margin = Thickness(8, 0, 0, 0)
    close_button.CornerRadius = CornerRadius(2)
    close_button.Cursor = Cursors.Hand
    close_button.VerticalAlignment = VerticalAlignment.Center

    def _enter(sender, args):
        close_button.Background = _card_divider_brush()

    def _leave(sender, args):
        close_button.Background = Brushes.Transparent

    def _down(sender, args):
        args.Handled = True

    def _up(sender, args):
        on_close()
        args.Handled = True

    close_button.MouseEnter += _enter
    close_button.MouseLeave += _leave
    close_button.MouseLeftButtonDown += _down
    close_button.MouseLeftButtonUp += _up
    return close_button


# ---- the tabs: "Розпланування поверху" is the first and always there, "Банк квартир" comes when it is asked for ----

def _init_tabs(window):
    """Built once, before anything else is wired: "Розпланування поверху" is the first tab and is never closed;
    the "Банк квартир" tab waits outside the strip until it is asked for (the first item of "0. Ознайомлення", or a plan
    Froglet made) and has a ✕ - it can be closed (the wheel closes it, too) and opened again."""
    bank_tab = window.BankTabItem
    window.MainTabControl.Items.Remove(bank_tab)
    bank_tab.Header.Children.Add(_make_close_cross(lambda: _close_detail_tab(window, bank_tab, None)))
    bank_tab.PreviewMouseDown += _make_tab_middle_close_handler(window, bank_tab, None)
    window.MainTabControl.SelectedItem = window.PickTabItem
    window.OpenBankButton.Click += lambda sender, args: _open_bank_tab(window)


def _open_bank_tab(window, select=True):
    """Put the "Банк квартир" tab next to "Розпланування поверху" (when it is not in the strip) and, with `select`,
    show it."""
    items = window.MainTabControl.Items
    if window.BankTabItem not in items:
        index = items.IndexOf(window.PickTabItem)
        items.Insert(index + 1 if index >= 0 else items.Count, window.BankTabItem)
        _schedule_tab_overflow_update(window)
    if select:
        window.MainTabControl.SelectedItem = window.BankTabItem


# ---- the rows of "Розпланування поверху" ("0. Ознайомлення" and the three stages): one open at a time, the stage opens its own ----

_STAGE_ACTIVE_COLOR = u"#171C21"
_STAGE_DIMMED_COLOR = u"#B9BDC2"


def _stage_for(created, footprint_ids, occupied_ids):
    """The stage of the layout the user is at: 1 "Вибір поверху" (the second row of the window) - the active view is not a
    plan Froglet made; 2 "Підбір планувань" (the third row) - it is one. (The geometry edits have no stage: they run by
    themselves after a floor is laid out.)"""
    return 2 if created else 1


def _stage_expanders(window):
    return [window.ViewCreationExpander, window.FootprintExpander]


def _all_expanders(window):
    """The rows of the tab, top to bottom: "1. Вибір поверху" and "2. Підбір планувань" (the stages)."""
    return _stage_expanders(window)


def _sync_stage_headers(window):
    """While one list is open the others read light grey (title, subtitle and arrow); with none open all are normal."""
    from System.Windows.Media import BrushConverter

    expanders = _all_expanders(window)
    any_open = any(expander.IsExpanded for expander in expanders)
    converter = BrushConverter()
    for expander in expanders:
        dimmed = any_open and not expander.IsExpanded
        expander.Foreground = converter.ConvertFromString(_STAGE_DIMMED_COLOR if dimmed else _STAGE_ACTIVE_COLOR)
        expander.Header.Opacity = 0.4 if dimmed else 1.0


def _wire_stage_expanders(window):
    """One list open at a time (opening one closes the rest) and the grey of the closed ones. "1. Вибір поверху" is open
    when the window opens (nothing reads the active view then); the second row comes when a plan Froglet made is the
    active view (`_show_footprint_row`) and opens when the stage CHANGES (`_follow_stage`), the first stage being the one
    the window starts at - so the first plan made (or opened) opens "2. Підбір планувань"."""
    window._stage = 1
    window._force_stage = False                 # set when the view is opened from "Створені види" (see _open_created_view)

    def _expanded(sender, args):
        if args.OriginalSource is sender:       # (the lists inside - groups of apartments - are expanders too)
            for other in _all_expanders(window):
                if other is not sender and other.IsExpanded:
                    other.IsExpanded = False
            _sync_stage_headers(window)
            _sync_run_buttons(window)

    def _collapsed(sender, args):
        if args.OriginalSource is sender:
            _sync_stage_headers(window)
            _sync_run_buttons(window)

    for expander in _all_expanders(window):
        expander.Expanded += _expanded
        expander.Collapsed += _collapsed
    window.ViewCreationExpander.IsExpanded = True          # the first row is open when the script opens
    _sync_stage_headers(window)


def _follow_bank_tab(window, created):
    """The "Банк квартир" tab comes next to "Розпланування поверху" when the active view becomes a plan Froglet made (the
    parameters of the footprint(s) chosen there fill its filters); the tab is not switched to. Only the CHANGE to a plan
    opens it, so a tab the user closed stays closed until the next plan; it is not taken away when the view is left."""
    created = bool(created)
    if created == getattr(window, "_created_view", False):
        return
    window._created_view = created
    if created:
        _open_bank_tab(window, select=False)


def _show_footprint_row(window, created):
    """"2. Підбір планувань" is not in the window until the active view is a plan Froglet made; when the user leaves such a
    plan the row goes again (and "1. Вибір поверху" opens if the row was the open one)."""
    from System.Windows import Visibility

    shown = Visibility.Visible if created else Visibility.Collapsed
    window.FootprintExpander.Visibility = shown
    window.StageDivider2.Visibility = shown
    if not created and window.FootprintExpander.IsExpanded:
        window.ViewCreationExpander.IsExpanded = True


def _follow_stage(window, created, footprint_ids, occupied_ids):
    """Open the list of the stage the user is at - only when the stage CHANGED, so a list the user opened by hand
    stays open until the next change (a plan made or opened, every footprint filled). Never raises."""
    try:
        _follow_bank_tab(window, created)
    except Exception:
        _debug_log(u"DKUI: вкладку «Банк квартир» не відкрито:\n```\n{}\n```".format(traceback.format_exc()))
    try:
        _show_footprint_row(window, bool(created))
    except Exception:
        _debug_log(u"DKUI: рядок «Підбір планувань» не показано:\n```\n{}\n```".format(traceback.format_exc()))
    try:
        stage = _stage_for(created, footprint_ids, occupied_ids)
        previous = getattr(window, "_stage", None)
        forced = bool(getattr(window, "_force_stage", False))
        window._force_stage = False
        window._stage = stage
        if getattr(window, "_stage_quiet", False):       # (the read at the window's start: nothing opens)
            window._stage_quiet = False
            return
        if stage == previous and not forced:
            return
        _stage_expanders(window)[stage - 1].IsExpanded = True
    except Exception:
        _debug_log(u"DKUI: етап розпланування не визначено:\n```\n{}\n```".format(traceback.format_exc()))


def _set_pick_tab_icon(window):
    try:
        # The Froglet icon (icon.png next to ui.xaml: the window's own icon, too).
        bitmap = _load_bitmap(os.path.join(BUNDLE_DIR, "icon.png"), decode_pixel_width=32)
        if bitmap is not None:
            window.PickIconImage.Source = bitmap
    except Exception as error:
        logger.warning("DKUI: не вдалося встановити іконку вкладки: %s", error)


def _set_bank_tab_icon(window):
    """The bank building on the "Банк квартир" tab (bank_icon.png next to ui.xaml)."""
    try:
        bitmap = _load_bitmap(os.path.join(BUNDLE_DIR, "bank_icon.png"), decode_pixel_width=32)
        if bitmap is not None:
            window.BankIconImage.Source = bitmap
    except Exception as error:
        logger.warning("DKUI: не вдалося встановити іконку вкладки банку: %s", error)


def _make_tab_middle_close_handler(window, tab_item, key):
    """Chrome's own "middle-click a tab to close it" - on the apartment tabs and on "Банк квартир" (not on
    "Розпланування поверху", which stays)."""
    from System.Windows.Input import MouseButton

    def _handler(sender, args):
        if args.ChangedButton != MouseButton.Middle:
            return
        _close_detail_tab(window, tab_item, key)
        args.Handled = True

    return _handler


def _open_apartment_detail_tab(window, item, activate):
    """Open (or, if already open, reuse) a Chrome-like tab showing one
    apartment's own big picture + parameters, alongside the pinned "Банк
    квартир" tab. activate=True (a plain left click) also switches to it,
    matching "open link" behaviour; activate=False (a middle click) opens
    it in the background without moving focus off whatever tab is
    currently active - same as a middle-click on a link in an actual
    browser."""
    from System.Windows.Controls import TabItem, TextBlock
    from System.Windows import TextTrimming, Thickness

    key = _apartment_tab_key(item)
    existing = window._apartment_detail_tabs.get(key)

    if existing is not None and existing in window.MainTabControl.Items:
        if activate:
            window.MainTabControl.SelectedItem = existing
        return
    from System.Windows import CornerRadius, HorizontalAlignment, VerticalAlignment
    from System.Windows.Controls import Orientation, StackPanel, Border
    from System.Windows.Input import Cursors
    from System.Windows.Media import Brushes

    header_text = TextBlock()
    header_text.Text = item.get("displayName") or item.get("name") or u"Квартира"
    header_text.MaxWidth = 150
    header_text.TextTrimming = TextTrimming.CharacterEllipsis
    header_text.Margin = Thickness(0)
    header_text.VerticalAlignment = VerticalAlignment.Center

    # Small close cross - Border gives it a comfortable hit area and a hover
    # background; the tab itself is not selected by pressing it.
    close_button = _make_close_cross(lambda: _close_detail_tab(window, tab_item, key))

    header = StackPanel()
    header.Orientation = Orientation.Horizontal
    header.Children.Add(header_text)
    header.Children.Add(close_button)

    tab_item = TabItem()
    tab_item.Header = header
    tab_item.Style = window.FindResource("ChromeTabItemStyle")
    tab_item.Content = _build_apartment_detail_tab_content(item, window)
    tab_item.AllowDrop = True
    tab_item.PreviewMouseDown += _make_tab_middle_close_handler(window, tab_item, key)

    _attach_tab_drag_reorder(window, tab_item, close_button)

    window.MainTabControl.Items.Add(tab_item)
    window._apartment_detail_tabs[key] = tab_item

    if activate:
        window.MainTabControl.SelectedItem = tab_item

    _schedule_tab_overflow_update(window)


_last_click = {"path": None, "time": None}


def _double_click_interval_ms():
    try:
        from System.Windows.Forms import SystemInformation
        return int(SystemInformation.DoubleClickTime)
    except Exception:
        return 500


def _register_click_is_double(item):
    """Record this click; True if it is the second click on the same
    apartment within one double-click interval."""
    import time

    now = time.time()
    path = item.get("path")
    previous_path, previous_time = _last_click["path"], _last_click["time"]

    is_double = (
        previous_time is not None
        and previous_path == path
        and (now - previous_time) * 1000.0 <= _double_click_interval_ms()
    )

    # A double click consumes both clicks - a third quick click starts over.
    if is_double:
        _last_click["path"], _last_click["time"] = None, None
    else:
        _last_click["path"], _last_click["time"] = path, now

    return is_double


def _build_apartment_card(item, card_size, is_selected, on_select, on_open_tab, on_run=None):
    from System.Windows import Thickness, CornerRadius, TextWrapping, TextAlignment, HorizontalAlignment, VerticalAlignment
    from System.Windows.Controls import Border, StackPanel, TextBlock, Image, Grid, Viewbox
    from System.Windows.Media import Brushes, Stretch
    from System.Windows.Input import Cursors

    # A rough target only, used for bitmap decode resolution - the card's
    # own on-screen width is no longer fixed here (ApartmentGridPanel is a
    # UniformGrid now, dividing the full row width evenly by Columns, so
    # the actual rendered width depends on the window size, not this).
    approx_width = _CARD_WIDTH_BY_SIZE.get(card_size, _CARD_WIDTH_BY_SIZE["m"])

    card = Border()
    card.Margin = Thickness(4)
    card.BorderThickness = Thickness(2 if is_selected else 1)
    card.BorderBrush = _selected_dark_brush() if is_selected else _avr_line_strong_brush()
    card.CornerRadius = CornerRadius(2)
    card.Background = Brushes.White
    card.Cursor = Cursors.Hand

    stack = StackPanel()

    image_frame = Border()
    image_frame.Background = Brushes.White
    # Top corners rounded to match the card's own CornerRadius (2, the
    # AVR design system's own avr-radius) - it's the first thing inside
    # the card, flush against its top edge, so without this the image's
    # own sharp corners visually squared off the card's rounded top
    # corners. Bottom corners stay square, where the divider + filename
    # label follow below.
    image_frame.CornerRadius = CornerRadius(2, 2, 0, 0)

    image = Image()
    # Fully visible, never cropped - a long/narrow plan (e.g. "Наскрізне")
    # just ends up smaller within its own square frame instead of having
    # its edges cut off.
    image.Stretch = Stretch.Uniform
    _load_bitmap_async(item.get("path"), int(approx_width * 2), image)

    image_frame.Child = image

    # Square the picture area DETERMINISTICALLY, not reactively - a plain
    # SizeChanged handler (sender.Height = ActualWidth) was tried and
    # worked for M/L (3 and 2 columns) but stayed stuck non-square for S
    # (5 columns, so more cards all invalidating/resizing each other in
    # the same layout pass than WPF's convergence budget reliably settles
    # in one go). A Viewbox has no such feedback loop: scaling a fixed 1:1
    # (240x240) square down/up to fit whatever width this column ends up
    # being is resolved in a SINGLE measure call, so it comes out exactly
    # square regardless of how many cards are being laid out at once.
    square = Grid()
    square.Width = 240
    square.Height = 240
    square.Children.Add(image_frame)
    image_viewbox = Viewbox()
    image_viewbox.Stretch = Stretch.Uniform
    image_viewbox.Child = square

    # Inset from the card's own left/right borders - a full-width divider
    # touching them read as an extra border line rather than a separator
    # between the picture and the filename below it.
    divider = Border()
    divider.Height = 1
    divider.Margin = Thickness(10, 0, 10, 0)
    divider.Background = _card_divider_brush()

    label = TextBlock()
    label.Text = item.get("displayName") or item.get("name") or ""
    label.Margin = Thickness(6)
    label.TextWrapping = TextWrapping.Wrap
    label.TextAlignment = TextAlignment.Center
    label.FontSize = 12

    stack.Children.Add(image_viewbox)
    stack.Children.Add(divider)
    stack.Children.Add(label)
    card.Child = stack

    def _handle_click(sender, args):
        # on_select(item) rebuilds the ENTIRE apartment grid (_render_
        # apartment_grid), which destroys and recreates every card -
        # INCLUDING this very `card`, the sender of the MouseLeftButtonUp
        # event currently being handled. Doing that synchronously, still
        # inside this card's own event handler, was corrupting WPF's
        # implicit mouse capture (the element that had capture during the
        # click just got ripped out of the visual tree mid-dispatch) -
        # symptom: after a LEFT click opened a detail tab, no further
        # clicks anywhere in the window (e.g. back to "Банк квартир")
        # registered at all. Middle-click never hit this, since
        # _handle_middle_click below never calls on_select. Deferring the
        # rebuild to the next dispatcher cycle lets THIS click finish
        # dispatching normally (mouse capture released the usual way)
        # before the card gets torn down.
        # A single click CHOOSES the apartment; a double click (the second click
        # on the same card within one double-click interval - our own timing
        # rather than args.ClickCount, which did not report 2 reliably here)
        # chooses it AND does what "Запустити" does with it (on_run).
        double = _register_click_is_double(item)

        from System import Action
        from System.Windows.Threading import DispatcherPriority

        def _apply_selection():
            # An exception out of a dispatcher callback is unhandled for WPF - and takes Revit down with it.
            try:
                on_select(item)
                if double and on_run is not None:
                    on_run(item)
            except Exception:
                _debug_log(u"DKUI: вибір/запуск квартири не вдався:\n```\n{}\n```".format(traceback.format_exc()))

        card.Dispatcher.BeginInvoke(DispatcherPriority.Background, Action(_apply_selection))

    card.MouseLeftButtonUp += _handle_click

    # Middle click (mouse wheel button): open the apartment's own tab
    # WITHOUT selecting the card for "Запустити" and
    # WITHOUT switching to the new tab - same as a middle-click on a link
    # opening a background tab in an actual browser. MouseLeftButtonUp
    # above already handles the left button on its own dedicated event;
    # this one only ever reacts to the middle button.
    def _handle_middle_click(sender, args):
        from System.Windows.Input import MouseButton
        if args.ChangedButton == MouseButton.Middle:
            try:
                on_open_tab(item, False)
            except Exception:
                _debug_log(u"DKUI: вкладку квартири не відкрито:\n```\n{}\n```".format(traceback.format_exc()))

    card.MouseUp += _handle_middle_click

    return card


_GRID_RENDER_DELAY_MS = 150


class _WpfTimer(object):
    """A one-shot DispatcherTimer that can be restarted (what ui_throttle.Debouncer needs)."""

    def __init__(self, delay_ms, on_tick):
        from System import TimeSpan
        from System.Windows.Threading import DispatcherTimer

        self._timer = DispatcherTimer()
        self._timer.Interval = TimeSpan.FromMilliseconds(delay_ms)
        self._timer.Tick += lambda sender, args: on_tick()

    def restart(self):
        self._timer.Stop()
        self._timer.Start()

    def stop(self):
        self._timer.Stop()


def _grid_debouncer(window):
    """The window's debouncer of grid rebuilds (made on first use, on the UI thread)."""
    debouncer = getattr(window, "_grid_debouncer", None)
    if debouncer is None:
        def _rebuild():
            _render_apartment_grid(window)
            _save_ui_state(window)

        def _failed(error):
            _debug_log(u"DKUI: не вдалося оновити список квартир:\n```\n{}\n```".format(traceback.format_exc()))

        debouncer = ui_throttle.Debouncer(
            lambda on_tick: _WpfTimer(_GRID_RENDER_DELAY_MS, on_tick), _rebuild, _failed)
        window._grid_debouncer = debouncer
    return debouncer


def _request_grid_render(window):
    """Rebuild the grid soon: a burst of requests (a dozen toggles, typing in a number box, the
    boxes a footprint pick fills in) is ONE rebuild, a moment after the last of them. Rebuilding on
    every click froze the window - each rebuild asks for the apartments and the pictures again."""
    _grid_debouncer(window).request()


def _filters_changed(state):
    """Whether the filters differ from the neutral start (every room count, window placement and added part
    on, area and sizes empty)."""
    return (
        len(state["apartmentTypes"]) < len(APARTMENT_TYPE_CODES)
        or len(state["windowPlacements"]) < len(WINDOW_PLACEMENT_CODES)
        or len(state["addedParts"]) < len(ADDED_PART_CODES)
        or any(state[key] for key in ("areaMin", "areaMax", "dimWidth", "dimLength", "dimTolerance"))
    )


def _sync_reset_filters_button(window, state):
    """"Очистити все" is there only while a filter is changed."""
    from System.Windows import Visibility

    try:
        window.ResetFiltersButton.Visibility = (
            Visibility.Visible if _filters_changed(state) else Visibility.Collapsed
        )
    except Exception:
        pass


def _reset_filters(window):
    """"Очистити все": every filter back to the neutral start, as on a fresh window, and the footprint the
    suggestions were fitted to is forgotten (the sort falls back to the area)."""
    for button in (
        window.TypeStudioToggle, window.Type1Toggle, window.Type2Toggle, window.Type3Toggle,
        window.WPRToggle, window.WPCToggle, window.WPLToggle, window.WPNToggle,
        window.APAPToggle, window.APNPToggle,
    ):
        button.IsChecked = True
    for text_box in (
        window.AreaMinTextBox, window.AreaMaxTextBox,
        window.DimWidthTextBox, window.DimLengthTextBox, window.DimToleranceTextBox,
    ):
        text_box.Text = ""
    window._dim_ordered_for = None
    window._fit_context = None
    window._current_page = 1
    _render_grid_now(window)
    window.StatusText.Text = u"Параметри скинуто."


def _render_grid_now(window, save=False):
    """Rebuild the grid right now (and forget the rebuild that was waiting)."""
    debouncer = getattr(window, "_grid_debouncer", None)
    if debouncer is not None:
        debouncer.cancel()
    _render_apartment_grid(window)
    if save:
        _save_ui_state(window)


def _render_apartment_grid(window):
    """Filter, sort, paginate and rebuild the apartment grid + pagination
    row from window._all_apartments / the current control state. Module-
    level (see this section's header) - only touches .NET-native members
    and plain instance attributes on `window` (safe from any engine)."""
    from System.Windows import Visibility, Thickness
    from System.Windows.Controls import Button as WpfButton, TextBlock
    from System.Windows.Media import Brushes

    _thumbnails.new_page()      # the cards below replace the old ones: their pictures are not wanted any more
    state = _read_filter_state(window)
    filtered, area_error, dim_error, empty_reason = _filter_and_sort_apartments(
        window._all_apartments, state
    )

    window.AreaErrorText.Text = area_error
    window.DimErrorText.Text = dim_error
    _sync_reset_filters_button(window, state)

    window.ApartmentGridPanel.Children.Clear()
    window._card_by_path = {}

    # ApartmentGridPanel is a UniformGrid - forcing its Columns to the
    # S/M/L target (5/3/2) makes every card's actual on-screen width
    # exactly (row width / N), always spanning edge-to-edge, rather than
    # a WrapPanel's "however many fit at each card's own fixed pixel
    # width" reflow.
    window.ApartmentGridPanel.Columns = _CARD_COLUMNS_BY_SIZE.get(state["cardSize"], 3)
    page_size = state["pageSize"]
    total_items = len(filtered)
    total_pages = max(1, int(math.ceil(total_items / float(page_size))) if page_size else 1)

    if window._current_page > total_pages:
        window._current_page = total_pages
    if window._current_page < 1:
        window._current_page = 1

    start_index = (window._current_page - 1) * page_size
    page_items = filtered[start_index:start_index + page_size]

    if not window._all_apartments:
        window.EmptyStateText.Visibility = Visibility.Visible
        window.EmptyStateText.Text = "Банк квартир порожній. Вкажіть apartments_pool у settings.txt."
    elif not filtered:
        window.EmptyStateText.Visibility = Visibility.Visible
        messages = {
            "no-type-selected": "Виберіть тип квартири.",
            "window-placement": "Немає квартир з обраним розташуванням вікон.",
            "added-part": "Немає квартир з обраною доданою частиною.",
            "area": "Немає квартир з такою площею.",
            "dimensions": "Немає квартир з такими розмірами.",
        }
        window.EmptyStateText.Text = messages.get(
            empty_reason, "Немає квартир, що відповідають фільтрам."
        )
    else:
        window.EmptyStateText.Visibility = Visibility.Collapsed

    def _on_card_selected(item):
        # Restyle just the old/new card instead of rebuilding all (up to
        # 32) cards - that rebuild was the main cost of a double click.
        previous = window._selected_apartment
        window._selected_apartment = item
        for entry, selected in ((previous, False), (item, True)):
            card_ref = window._card_by_path.get(entry.get("path")) if entry else None
            if card_ref is not None:
                card_ref.BorderThickness = Thickness(2 if selected else 1)
                card_ref.BorderBrush = (
                    _selected_dark_brush() if selected else _avr_line_strong_brush()
                )
        window.StatusText.Text = "Обрано: " + (item.get("displayName") or item.get("name") or "")

    def _on_card_open_tab(item, activate):
        _open_apartment_detail_tab(window, item, activate)

    def _on_card_run(item):
        # a double click: the same as "Запустити" with this apartment - but in the floor layout it only confirms
        _confirm_apartment(window, item, confirm_only=True)

    selected_path = None
    if window._selected_apartment is not None:
        selected_path = window._selected_apartment.get("path")

    for item in page_items:
        is_selected = selected_path is not None and item.get("path") == selected_path
        card = _build_apartment_card(
            item, state["cardSize"], is_selected, _on_card_selected, _on_card_open_tab, _on_card_run
        )
        window._card_by_path[item.get("path")] = card
        window.ApartmentGridPanel.Children.Add(card)

    # Pagination row: numbered page buttons with an ellipsis for many
    # pages, current page highlighted - same windowing as the original
    # web UI's getPaginationItems: <=7 pages shows all of them; otherwise
    # 1,2,3,4,5,…,last near the start, 1,…,last-4..last near the end, or
    # 1,…,cur-1,cur,cur+1,…,last in the middle.
    window.PaginationPanel.Children.Clear()

    for entry in _pagination_page_numbers(window._current_page, total_pages):
        if entry is None:
            ellipsis_text = TextBlock()
            ellipsis_text.Text = "…"
            ellipsis_text.Margin = Thickness(4, 0, 4, 0)
            ellipsis_text.VerticalAlignment = _vertical_center()
            window.PaginationPanel.Children.Add(ellipsis_text)
            continue

        page_button = WpfButton()
        page_button.Content = str(entry)
        page_button.Width = 36
        page_button.Height = 32
        page_button.Margin = Thickness(0, 0, 4, 0)

        if entry == window._current_page:
            page_button.Background = _selected_dark_brush()
            page_button.Foreground = Brushes.White
            page_button.IsEnabled = False
        else:
            def _make_goto(target_page):
                def _goto(sender, args):
                    window._current_page = target_page
                    _request_grid_render(window)
                return _goto

            page_button.Click += _make_goto(entry)

        window.PaginationPanel.Children.Add(page_button)

    window.PageCountText.Text = u"{} з {}".format(total_items, len(window._all_apartments))


def _pagination_page_numbers(current_page, total_pages):
    """List of page numbers to show, with None standing in for an
    ellipsis - mirrors the original web UI's compact-ellipsis windowing."""
    if total_pages <= 7:
        return list(range(1, total_pages + 1))

    if current_page <= 4:
        return [1, 2, 3, 4, 5, None, total_pages]

    if current_page >= total_pages - 3:
        return [1, None] + list(range(total_pages - 4, total_pages + 1))

    return [1, None, current_page - 1, current_page, current_page + 1, None, total_pages]


def _vertical_center():
    from System.Windows import VerticalAlignment
    return VerticalAlignment.Center


def _muted_brush():
    # avr-c4 (#5C636B) from the AVR Development design system's own
    # colour scale - matches the muted-text colour used throughout
    # ui.xaml's own re-skinned styles.
    from System.Windows.Media import Color, SolidColorBrush
    return SolidColorBrush(Color.FromRgb(0x5C, 0x63, 0x6B))


def _semi_bold_weight():
    from System.Windows import FontWeights
    return FontWeights.SemiBold


def _card_divider_brush():
    # avr-bg (#E9EAEC) - matches the apartment-grid container's own
    # background (ui.xaml's own re-skinned colour) - reads as a subtle
    # break between an apartment card's picture and its filename, the
    # same tone as the space around the card rather than an unrelated
    # new colour.
    from System.Windows.Media import Color, SolidColorBrush
    return SolidColorBrush(Color.FromRgb(0xE9, 0xEA, 0xEC))


def _avr_display_font_family():
    # Montserrat (falls back to Segoe UI if not installed) - the AVR
    # design system's own "display voice", used for titles/headings built
    # in code. Body text needs no equivalent helper: the root Window
    # already sets FontFamily="Roboto Condensed, Segoe UI", which every
    # control not given its own explicit FontFamily inherits automatically.
    from System.Windows.Media import FontFamily
    return FontFamily("Montserrat Light, Montserrat, Segoe UI")


def _avr_line_strong_brush():
    # avr-line-strong (#C8CBCF) - replaces the plain Brushes.LightGray
    # this used before, for borders that should read as part of the same
    # restrained AVR palette rather than an unrelated system grey.
    from System.Windows.Media import Color, SolidColorBrush
    return SolidColorBrush(Color.FromRgb(0xC8, 0xCB, 0xCF))


def _selected_dark_brush():
    # avr-ink (#171C21) - matches the checked/selected colour used
    # everywhere else (FilterToggleStyle, PickerRadioStyle,
    # PrimaryActionButtonStyle) - used here for the current-page
    # pagination button and the selected-apartment-card border so they
    # all read as the same "selected" state, not an unrelated pure black.
    from System.Windows.Media import Color, SolidColorBrush
    return SolidColorBrush(Color.FromRgb(0x17, 0x1C, 0x21))


def _log_priority_order(order):
    weights = list(range(len(order), 0, -1))
    _debug_log(
        "**DKUI: оновлено пріоритети**\n\n"
        + "\n".join(
            "{}. {} (вага {})".format(i + 1, key, weights[i])
            for i, key in enumerate(order)
        )
    )


def _save_ui_state(window):
    # Deliberately does NOT persist apartmentTypes/windowPlacements/
    # addedParts/area/dimensions - see _restore_ui_state's own docstring
    # for why those always reset to the neutral "no footprint picked yet"
    # defaults instead of remembering the last search. Only genuine UI
    # preferences (sort/card/page size, priority order) are worth
    # remembering across sessions.
    config = load_config()
    state = _read_filter_state(window)
    config["sortMode"] = state["sortMode"]
    config["cardSize"] = state["cardSize"]
    config["pageSize"] = state["pageSize"]
    config["priorityOrder"] = window._priority_order
    save_config(config)


def _restore_ui_state(window, config):
    """Apply saved UI *preferences* (sort/card/page size, priority order)
    from config. Filters (apartment type / window placement
    / added part / area / dimensions) are NOT restored from config here -
    they always reset to "nothing filtered yet" (every type/placement/
    part selected, area and dimensions empty) on a fresh window, exactly
    matching what "Банк квартир launched with no footprint picked" should
    look like. A footprint pick narrows them live afterwards via
    _apply_footprint_payload - that's the only thing that should ever
    change them away from this neutral state, not a leftover search from
    a previous session."""
    for button in (
        window.TypeStudioToggle, window.Type1Toggle,
        window.Type2Toggle, window.Type3Toggle,
    ):
        button.IsChecked = True

    for button in (
        window.WPRToggle, window.WPCToggle, window.WPLToggle, window.WPNToggle,
    ):
        button.IsChecked = True

    for button in (window.APAPToggle, window.APNPToggle):
        button.IsChecked = True

    window.AreaMinTextBox.Text = ""
    window.AreaMaxTextBox.Text = ""
    window.DimWidthTextBox.Text = ""
    window.DimLengthTextBox.Text = ""
    window.DimToleranceTextBox.Text = ""

    sort_mode = config.get("sortMode") or APARTMENT_SORT_MODES[0]
    for combo_item in window.SortComboBox.Items:
        if str(combo_item.Tag) == sort_mode:
            window.SortComboBox.SelectedItem = combo_item
            break
    else:
        window.SortComboBox.SelectedIndex = 0

    card_size = config.get("cardSize") or "m"
    {"s": window.CardSizeSRadio, "m": window.CardSizeMRadio, "l": window.CardSizeLRadio}.get(
        card_size, window.CardSizeMRadio
    ).IsChecked = True

    page_size = config.get("pageSize") or 12
    {
        8: window.PageSize8Radio, 12: window.PageSize12Radio,
        20: window.PageSize20Radio, 32: window.PageSize32Radio,
    }.get(page_size, window.PageSize12Radio).IsChecked = True

    stored_order = config.get("priorityOrder")
    if isinstance(stored_order, list) and sorted(stored_order) == sorted(PRIORITY_PARAMETER_KEYS_DEFAULT):
        window._priority_order = list(stored_order)
    else:
        window._priority_order = list(PRIORITY_PARAMETER_KEYS_DEFAULT)


# ============================================================
# MAIN WINDOW
# ============================================================

def show_apartment_selector():
    # Reuse this session's one window instead of creating a new one, if
    # it already exists - avoids losing whatever the user currently has
    # open/selected when "Вибір плями квартири" wants to push new
    # footprint data into it. See dkui_bridge.get_persistent_window's own
    # docstring. Unlike the WebView2 era, there's no "goes deaf after a
    # placement" failure mode here to work around - this window's own
    # Click/SelectionChanged handlers are plain .NET event delegates
    # wired once at creation, and keep firing correctly for as long as
    # the window itself exists, regardless of which engine currently
    # calls Show()/Activate() on it.
    existing_window = None
    if dkui_bridge is not None:
        try:
            existing_window = dkui_bridge.get_persistent_window()
        except Exception:
            existing_window = None

    if existing_window is not None:
        try:
            _debug_log(
                "DKUI: show_apartment_selector() - reusing this session's "
                "existing window instead of creating a new one."
            )
            existing_window.Show()

            try:
                from System.Windows import SystemParameters

                work_area = SystemParameters.WorkArea
                existing_window.Left = work_area.Left + max(
                    0.0, (work_area.Width - existing_window.ActualWidth) / 2.0
                )
                existing_window.Top = work_area.Top + max(
                    0.0, (work_area.Height - existing_window.ActualHeight) / 2.0
                )
            except Exception as error:
                _debug_log(
                    "DKUI: show_apartment_selector() - could not "
                    "recentre the reused window: {}".format(error)
                )

            try:
                existing_window.ShowInTaskbar = True
            except Exception:
                pass

            try:
                from System.Windows import WindowState

                existing_window.WindowState = WindowState.Normal         # (it opens at about 820 x 1000, not full screen)
            except Exception as error:
                _debug_log("DKUI: show_apartment_selector() - could not restore the reused window: {}".format(error))

            existing_window.Activate()

            try:
                _refresh_persistent_placement_event()
                _refresh_footprint_pick_event(existing_window)
            except Exception as error:
                _debug_log(
                    "DKUI: show_apartment_selector() - could not "
                    "refresh the placement ExternalEvent on reuse: "
                    "{}".format(error)
                )

            if dkui_bridge is not None:
                try:
                    pending_payload = dkui_bridge.pop_pending_footprint_payload()
                except Exception:
                    pending_payload = None

                if pending_payload is not None:
                    _apply_footprint_payload(existing_window, pending_payload)

            return existing_window
        except Exception as error:
            _debug_log(
                "DKUI: show_apartment_selector() - could not reuse the "
                "existing window ({}); creating a new one instead.".format(
                    error
                )
            )

    _debug_log("DKUI: show_apartment_selector() called - creating a new window.")

    xaml_path = os.path.join(BUNDLE_DIR, "ui.xaml")

    class ApartmentSelectorWindow(forms.WPFWindow):

        def __init__(self, xaml_source):
            forms.WPFWindow.__init__(self, xaml_source)

            self._set_window_icon()
            _set_pick_tab_icon(self)
            _set_bank_tab_icon(self)
            # "Розпланування поверху" is the first tab; "Банк квартир" comes when it is asked for.
            _init_tabs(self)
            _wire_stage_expanders(self)
            _set_groups_message(self, u"Не відкрито жодного виду")      # (until the first read of the active view)
            _show_no_created_views(self)
            self._pick_mode = _PICK_FLOOR          # (the floor layout is the only way now)
            # Permanent tabs: not draggable, but dropping a dragged detail tab
            # on them just clamps it to the first movable slot.
            _wire_caption_buttons(self)
            _wire_manual_link(self)
            _fit_window_to_screen(self)
            _wire_tab_overflow(self)
            for permanent in _permanent_tabs(self):
                permanent.DragOver += make_tab_drag_over(self, permanent)

            self._is_closed = False
            self._all_apartments = []
            self._selected_apartment = None
            self._current_page = 1
            self._priority_order = list(PRIORITY_PARAMETER_KEYS_DEFAULT)
            # Chrome-like apartment detail tabs opened from a card (see
            # _open_apartment_detail_tab) - key -> the TabItem itself, so
            # re-clicking an apartment that's already open reuses/focuses
            # that same tab instead of opening a duplicate.
            self._apartment_detail_tabs = {}
            self._card_by_path = {}

            config = load_config()
            _restore_ui_state(self, config)

            image_folder = resolve_image_folder()
            records, folder_state, errors = _scan_apartments(image_folder)
            self._all_apartments = records
            # Pictures remembered from an earlier window: only those of files that are the same.
            _thumbnails.forget_changed(dict((record.get("path"), record.get("signature")) for record in records))
            _details_cache.clear()

            if errors:
                logger.warning("DKUI: помилки читання папки: %s", "; ".join(errors))

            self.Closing += self._on_window_closing
            self.Closed += self._on_window_closed

            self.RunButton.Click += self._on_run_click
            self._footprint_pick_event = None
            self._floor_plan_event = None
            self._views_refresh_event = None
            self._groups_event = None
            self._group_select_event = None
            self._open_view_event = None
            self._floor = None
            self._floor_expanded = set()
            self._floor_parts = []
            self._floor_pick_event = None
            self._floor_auto_event = None
            self._floor_clear_event = None
            self._occupancy_event = None
            self._room_tags_event = None
            self._placed_event = None
            self._edit_event = None
            self._stair_event = None
            self._placed_entries = []
            self._placed_boxes = []
            self._placed_all = []
            self._placed_mode = "sections"
            self._placed_selected = set()
            self._placed_anchor = None
            self._pending_edit_keys = []
            self.PlacedRunButton.Click += self._on_placed_run_click
            self._placed_paint_event = None
            self._placed_painted = {}
            self._view_items = None
            self._occupied = None
            self._roof_shown = {}
            self._display_chosen = set()
            self._pending_clear_ids = []
            self.AutoPickPlansButton.Click += self._on_auto_pick_plans_click
            self.AutoPickChosenButton.Click += self._on_auto_pick_chosen_click
            self.FloorRunButton.Click += self._on_floor_run_click
            self._pending_floor_pick_ids = []
            self._floor_cleanup = None
            self._pending_green_ids = []
            self._floor_start_event = None
            self._floor_cancel_event = None
            self._floor_mark_event = None
            self.SelectLevelSectionButton.Click += self._on_floor_plan_click
            self._sections_event = None
            self._section_delete_event = None
            self._pending_section_delete = None
            self.SelectSectionsButton.Click += self._on_sections_click
            self._floor_copy = None
            self._floor_boxes = []
            self._floors_event = None
            self._floor_copy_event = None
            self.CopyFloorButton.Click += self._on_copy_floor_click

            for button in (
                self.TypeStudioToggle, self.Type1Toggle, self.Type2Toggle, self.Type3Toggle,
                self.WPRToggle, self.WPCToggle, self.WPLToggle, self.WPNToggle,
                self.APAPToggle, self.APNPToggle,
            ):
                button.Click += self._on_filter_changed

            for text_box in (
                self.AreaMinTextBox, self.AreaMaxTextBox,
                self.DimWidthTextBox, self.DimLengthTextBox, self.DimToleranceTextBox,
            ):
                text_box.TextChanged += self._on_filter_changed

            self.ResetFiltersButton.Click += self._on_reset_filters_click
            self.SortComboBox.SelectionChanged += self._on_sort_or_size_changed
            for radio in (self.CardSizeSRadio, self.CardSizeMRadio, self.CardSizeLRadio):
                radio.Checked += self._on_sort_or_size_changed
            for radio in (
                self.PageSize8Radio, self.PageSize12Radio,
                self.PageSize20Radio, self.PageSize32Radio,
            ):
                radio.Checked += self._on_sort_or_size_changed

            _render_grid_now(self)
            self.StatusText.Text = "Готово."

        def _set_window_icon(self):
            # pyRevit's XAML loader resolves a plain Icon="icon.png"
            # attribute as a WPF pack-resource URI, which fails outside a
            # compiled assembly. Setting it here from an absolute file
            # path instead sidesteps that entirely.
            icon_path = os.path.join(BUNDLE_DIR, "icon.png")

            if not os.path.isfile(icon_path):
                return

            try:
                from System import Uri
                from System.Windows.Media.Imaging import BitmapCacheOption, BitmapImage

                bitmap = BitmapImage()
                bitmap.BeginInit()
                bitmap.UriSource = Uri(icon_path)
                bitmap.CacheOption = BitmapCacheOption.OnLoad
                bitmap.EndInit()

                self.Icon = bitmap
            except Exception as error:
                logger.warning("DKUI: не вдалося встановити іконку вікна: %s", error)

        def _on_window_closing(self, sender, args):
            # Moved off-screen instead of actually closing, so this
            # window (and its Click handlers, still bound to THIS
            # engine) can be instantly reused later instead of rebuilt -
            # see show_apartment_selector's own comment.
            try:
                args.Cancel = True
            except Exception:
                pass
            try:
                self.ShowInTaskbar = False
            except Exception:
                pass
            try:
                # A maximised window ignores Left/Top: it goes back to its normal size first (it is
                # maximised again when it is shown, see show_apartment_selector).
                from System.Windows import WindowState

                self.WindowState = WindowState.Normal
            except Exception:
                pass
            try:
                self.Left = _OFFSCREEN_COORDINATE
                self.Top = _OFFSCREEN_COORDINATE
            except Exception as error:
                logger.warning(
                    "DKUI: не вдалося перемістити вікно за межі екрана: %s",
                    error,
                )

        def _on_window_closed(self, sender, args):
            # Only reachable if Revit itself force-closes all windows on
            # exit (Closing above cancels every ordinary close).
            self._is_closed = True

        def _on_floor_run_click(self, sender, args):
            if self._floor and self._floor["assigned"]:
                _run_floor_layout(self)

        def _on_placed_run_click(self, sender, args):
            if self._edit_event is None:
                return
            # "Виправлення геометрії квартири та додавання сходово-ліфтового вузла" is
            # the four steps in one: align the internal walls, remove the duplicate
            # walls, place the stair/lift cores and, last of all, "Квартирографія"
            # (the areas and parameters of the rooms; apartment_edits.run_many puts
            # it at the very end, whatever the order here).
            keys = []
            if self.ExtendExternalWallsCheck.IsChecked:
                keys.append("external_walls")
            if self.GeometryFixCheck.IsChecked:
                keys.extend(["internal_walls", "duplicate_walls", "stair_core", "apartment_areas", "floor_slab"])
            self._pending_edit_keys = keys
            self._edit_event.Raise()

        def _on_auto_pick_plans_click(self, sender, args):
            self._auto_silent = False
            self._auto_scope = "all"                       # the rest of the footprints: those with no layout confirmed
            if self._floor_auto_event is not None:
                self._floor_auto_event.Raise()

        def _on_auto_pick_chosen_click(self, sender, args):
            self._auto_silent = False
            self._auto_scope = "chosen"                    # only the footprints chosen on the scheme
            if self._floor_auto_event is not None:
                self._floor_auto_event.Raise()

        def _on_run_click(self, sender, args):
            if self._floor and _floor_all_assigned(self):
                _run_floor_layout(self)
                return

            item = self._selected_apartment
            if item is None:
                forms.alert(
                    "Квартиру не обрано. Клацніть по картці квартири в "
                    "банку, а потім натисніть «Запустити».",
                    title="DKUI",
                )
                return

            _confirm_apartment(self, item)

        def _on_sections_click(self, sender, args):
            if self._sections_event is None:
                forms.alert(
                    "Вибір секцій недоступний: закрийте і знову відкрийте Froglet.",
                    title="Froglet",
                )
                return
            # Black until the choice ends (restored by the event callback).
            self.SelectSectionsButton.Style = self.FindResource("ActiveActionButtonStyle")
            self._sections_event.Raise()

        def _on_copy_floor_click(self, sender, args):
            if self._floor_copy_event is None:
                forms.alert(
                    "Копіювання поверху недоступне: закрийте і знову відкрийте Froglet.",
                    title="Froglet",
                )
                return
            self._floor_copy_event.Raise()

        def _on_floor_plan_click(self, sender, args):
            if self._floor_plan_event is None:
                forms.alert(
                    "Створення плану недоступне: закрийте і знову відкрийте Froglet.",
                    title="Froglet",
                )
                return
            # Black until the choice ends (restored by the event callback).
            self.SelectLevelSectionButton.Style = self.FindResource("ActiveActionButtonStyle")
            self._floor_plan_event.Raise()

        def _on_filter_changed(self, sender, args):
            # Not a rebuild per click: a burst of clicks / keystrokes is ONE rebuild, a moment after
            # the last of them (_request_grid_render).
            self._current_page = 1
            _request_grid_render(self)

        def _on_reset_filters_click(self, sender, args):
            _reset_filters(self)

        def _on_sort_or_size_changed(self, sender, args):
            self._current_page = 1
            _request_grid_render(self)

    window = ApartmentSelectorWindow(xaml_path)

    if dkui_bridge is not None:
        try:
            dkui_bridge.set_persistent_window(window)
        except Exception as error:
            logger.warning(
                "DKUI: не вдалося зберегти вікно для повторного використання: %s",
                error,
            )

    try:
        _refresh_persistent_placement_event()
        _refresh_footprint_pick_event(window)
    except Exception as error:
        logger.warning(
            "DKUI: не вдалося створити подію розміщення: %s", error
        )

    window.show(modal=False)

    if dkui_bridge is not None:
        try:
            pending_payload = dkui_bridge.pop_pending_footprint_payload()
        except Exception:
            pending_payload = None

        if pending_payload is not None:
            _apply_footprint_payload(window, pending_payload)

    return window
