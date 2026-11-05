# -*- coding: utf-8 -*-
"""
Apartment group placement (pyRevit port).

Ported from a Grasshopper Python 3 / Rhino.Inside.Revit component that
copied a model group (one apartment "type") out of its own standalone RVT
file and placed it into the host project at a given position/rotation/
level, then rebuilt that apartment's dimensioning against the newly
hosted walls. This module keeps the same core placement/rehosting/
dimensioning algorithm, adapted to run as ordinary IronPython inside
Revit - no Rhino/Grasshopper needed - and driven by one explicit call
instead of Grasshopper's tree/branch inputs.

Dropped from the original: all Grasshopper input-tree parsing (no longer
needed - the caller already has plain floats/objects), and the
"mirror the group if it doesn't sufficiently overlap a reference surface"
feature - there's no natural reference surface in this workflow.

Called by "Select Apartment.pushbutton"'s apartment_bank_window.py when
the user presses "Запустити": the target level/position/rotation come
from the footprint most recently picked via "Вибір плями квартири"
(computed in that pushbutton's script.py and handed over through
dkui_bridge.py), and the source RVT path comes from whichever apartment
card is selected in the "Банк квартир" grid.
"""

import io
import math
import os
import random

import clr

clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import Application as _WinFormsApplication  # noqa: E402

clr.AddReference("WindowsBase")
from System import Action  # noqa: E402
from System.Windows.Threading import (  # noqa: E402
    Dispatcher,
    DispatcherFrame,
    DispatcherPriority,
)

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    Color,
    CopyPasteOptions,
    Dimension,
    DimensionType,
    DuplicateTypeAction,
    Element,
    ElementId,
    ElementTransformUtils,
    Face,
    FailureProcessingResult,
    FailureSeverity,
    FamilyInstance,
    FilteredElementCollector,
    Group,
    HostObjectUtils,
    IDuplicateTypeNamesHandler,
    IFailuresPreprocessor,
    Line,
    OverrideGraphicSettings,
    Plane,
    Reference,
    ReferenceArray,
    ShellLayerType,
    Transaction,
    TransactionStatus,
    Transform,
    ViewPlan,
    ViewType,
    Wall,
    XYZ,
)

from System.Collections.Generic import List

from pyrevit import script

logger = script.get_logger()

# Same extension-level lib folder this module already lives in - see
# "Вибір плями квартири.pushbutton"\\script.py's own import of this
# module for the established pattern. Used only to append this
# module's own R+AP depth-measurement debug lines (see
# _draw_r_ap_depth_debug_lines) to the same tracked set that
# pushbutton's own debug lines use, so the NEXT footprint pick's
# cleanup catches these too.
try:
    import dkui_bridge
except Exception:
    dkui_bridge = None

import backend_api

FT_TO_M = 0.3048
M_TO_FT = 1.0 / FT_TO_M

# src DimensionType name -> chosen host DimensionType, persisted for the
# life of this module (same random-fallback-then-remember behaviour as
# the original script).
_dim_type_map_cache = {}

# Same file apartment_bank_window._debug_log writes to - see that
# function's own docstring for why: pyrevit.script.get_output()'s
# print_md resolves its target window LIVE via EXEC_PARAMS.window_handle
# on every call, which tracks whichever pyRevit command invocation is
# CURRENTLY active in this engine, not the one that was active when this
# module was imported or when place_apartment_group started running.
# This function runs entirely from inside an ExternalEvent.Execute()
# callback, well after the script that opened the apartment-bank window
# returned - by the time it's called, that pointer may already be
# stale or reassigned, and output.print_md/logger.info calls here can
# be silently lost. A plain file can't have that problem.
_DEBUG_LOG_PATH = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "DKUI", "webview_debug.log"
)


# Batch placement (place_apartment_group with a source_cache) runs "quiet":
# routine log lines and UI-message pumping are skipped - only lines marked
# ERROR/WARN still reach the log file.
_quiet = {"on": False}


def _log(msg):
    if _quiet["on"]:
        try:
            if "ERROR" not in msg and "WARN" not in msg and "[NOTE]" not in msg:
                return
        except Exception:
            return

    # No logger.info(msg) here on purpose: it writes to pyRevit's own
    # stdout-backed output console, which auto-opens a window the moment
    # anything is written to it - and since this fires dozens of times
    # per placement, from inside an ExternalEvent callback that can end
    # up bound to whichever pushbutton's engine last refreshed it (not
    # necessarily "Select Apartment"), that showed up as stray
    # "Вибір плями квартири" console windows popping open on every
    # placement. The file write below is unaffected by any of that.
    # A plain (non-u-prefixed) string literal containing non-ASCII
    # characters (this file declares "# -*- coding: utf-8 -*-") can be a
    # `str` holding raw UTF-8 bytes rather than `unicode`, and combining
    # that directly into a u"..." template can throw (implicit ASCII
    # decode). Layered fallbacks below so a Unicode quirk NEVER results
    # in total silence the way a single bare try/except pass did before -
    # worst case, repr(msg) still lands in the file, visibly, instead of
    # the message just vanishing with no trace.
    line = None
    try:
        line = u"{}\n".format(msg)
    except Exception:
        try:
            line = u"{}\n".format(msg.decode("utf-8", "replace"))
        except Exception:
            try:
                line = u"{}\n".format(repr(msg))
            except Exception:
                line = u"<_log: unloggable message>\n"

    try:
        log_dir = os.path.dirname(_DEBUG_LOG_PATH)
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        # io.open(..., encoding="utf-8") - NOT the plain open() this used
        # to call: `line` here is always a `unicode` object (guaranteed by
        # the layered fallback above), and writing THAT through a stream
        # opened without an explicit encoding falls back to IronPython's
        # own default (not UTF-8) and throws UnicodeEncodeError for any
        # Cyrillic character - silently swallowed by the except below,
        # which is exactly why EVERY Cyrillic-containing _log() message in
        # this module (as opposed to apartment_bank_window.py's own
        # _debug_log, which already opened its file this same explicit-
        # UTF-8 way) turned out to have never actually reached the log
        # file at all, while plain-ASCII ones always did. Matches
        # _debug_log's own working file-opening convention exactly.
        with io.open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(line)
    except Exception:
        pass


def _pump_ui_messages():
    """Process pending work on Revit's main UI thread without returning
    control to our caller - both the raw Win32 message queue AND WPF's
    own Dispatcher queue.

    One measured placement took 79 seconds start to finish, fully
    synchronous (one long Transaction, document open, dimension
    creation) - and CoreWebView2 events (WebMessageReceived first, later
    NavigationCompleted too, even freshly re-subscribed each time -
    ruling out a stale/accumulated delegate) stopped being delivered to
    the apartment-bank window for the rest of the session, every time,
    right after this ran once, where before it they fired reliably.
    Revit disables its own ribbon/UI interaction for the duration of an
    ExternalEvent callback regardless of message pumping, so pumping
    here can't reintroduce the reentrancy Revit's API-context lock
    exists to prevent - but nothing else on this same UI thread
    (including WebView2's own browser-process IPC delivery) gets a
    chance to run either, for however long place_apartment_group takes.

    System.Windows.Forms.Application.DoEvents() alone (the first
    attempt at this) didn't fix the symptom - it pumps the raw Win32
    message queue, but WPF controls (this WHOLE window, including
    WebView2's WPF wrapper) marshal their own async continuations
    through WPF's Dispatcher specifically, which running DoEvents()
    alone doesn't guarantee actually drains, even though the two are
    usually integrated via the same underlying hidden HWND. Push a
    Dispatcher frame too, the standard "DoEvents() for WPF" idiom, to
    directly drain whatever's actually queued there as well. Cheap to
    call often: a no-op if nothing is queued either way."""
    if _quiet["on"]:
        return

    try:
        _WinFormsApplication.DoEvents()
    except Exception:
        pass

    try:
        frame = DispatcherFrame()

        def _stop_frame():
            frame.Continue = False

        Dispatcher.CurrentDispatcher.BeginInvoke(
            DispatcherPriority.Background, Action(_stop_frame)
        )
        Dispatcher.PushFrame(frame)
    except Exception:
        pass


def _element_name(element):
    """Element.Name is an explicit interface property in the Revit API,
    invisible to IronPython's normal attribute lookup (plain
    element.Name raises "AttributeError: Name") - go through the base
    property descriptor instead. Safe for any Element-derived object
    (GroupType, DimensionType, View, Level, ...), not just Element
    itself. (Category.Name is a different, ordinary property and doesn't
    need this - Category isn't Element-derived.)"""
    if element is None:
        return ""
    try:
        return Element.Name.__get__(element)
    except Exception:
        try:
            return element.Name
        except Exception:
            return ""


class UseDestinationTypes(IDuplicateTypeNamesHandler):
    """On duplicate type names - keep host types."""

    def OnDuplicateTypeNamesFound(self, args):
        return DuplicateTypeAction.UseDestinationTypes


def _resolve_failures_accessor(failuresAccessor):
    """Delete every warning and resolve every error on failuresAccessor,
    silently - shared by AutoResolveCopyWarnings (per-Transaction, via
    IFailuresPreprocessor) and _on_application_failures_processing
    (application-wide, via Application.FailuresProcessing - the only one
    of the two that also catches failures raised OUTSIDE any transaction
    we control, e.g. family warnings surfaced while simply opening a
    source document with OpenDocumentFile). Returns the
    FailureProcessingResult the caller should report back."""
    resolved_error = False
    unresolved_error = False

    try:
        failures = list(failuresAccessor.GetFailureMessages())
    except Exception:
        failures = []

    for f in failures:
        try:
            severity = f.GetSeverity()
        except Exception:
            severity = None

        try:
            desc = f.GetDescriptionText()
        except Exception:
            desc = "<no description>"

        if severity == FailureSeverity.Warning:
            try:
                _log("  [FAILURE SUPPRESS] warning deleted: {}".format(desc))
                failuresAccessor.DeleteWarning(f)
                continue
            except Exception as e_del:
                _log("  [FAILURE SUPPRESS WARN] could not delete warning '{}': {}".format(desc, e_del))
                continue

        error_resolved = False

        try:
            failuresAccessor.ResolveFailure(f)
            _log("  [FAILURE SUPPRESS] error resolved by default resolution: {}".format(desc))
            resolved_error = True
            error_resolved = True
        except Exception:
            try:
                available_resolutions = list(f.GetAvailableResolutionTypes())
            except Exception:
                available_resolutions = []

            for res_type in available_resolutions:
                try:
                    f.SetCurrentResolutionType(res_type)
                    failuresAccessor.ResolveFailure(f)
                    _log("  [FAILURE SUPPRESS] error resolved using '{}': {}".format(res_type, desc))
                    resolved_error = True
                    error_resolved = True
                    break
                except Exception:
                    pass

            if not error_resolved:
                _log("  [FAILURE SUPPRESS ERROR] unresolved error, transaction will rollback silently: {}".format(desc))
                unresolved_error = True

    if unresolved_error:
        return FailureProcessingResult.ProceedWithRollBack

    if resolved_error:
        return FailureProcessingResult.ProceedWithCommit

    return FailureProcessingResult.Continue


class AutoResolveCopyWarnings(IFailuresPreprocessor):
    """
    Suppress Revit failure popups during transactions.

    This is stronger than just deleting warnings:
    - warnings are deleted silently;
    - resolvable errors are resolved silently, e.g. "Can't keep elements joined";
    - an unresolvable error rolls the transaction back silently instead of
      showing a modal Revit dialog.
    """

    def PreprocessFailures(self, failuresAccessor):
        return _resolve_failures_accessor(failuresAccessor)


def _on_application_failures_processing(sender, args):
    """Application.FailuresProcessing handler - catches failures raised
    outside any Transaction we control, most importantly the "family X
    had an error and was automatically resolved" dialog Revit can show
    while simply opening a source document (see place_apartment_group)."""
    try:
        result = _resolve_failures_accessor(args.GetFailuresAccessor())
        args.SetProcessingResult(result)
    except Exception as error:
        _log("  [FAILURE SUPPRESS WARN] application-level handler failed: {}".format(error))


def apply_silent_failure_handling(transaction):
    """Attach the failure preprocessor to a transaction. Call immediately
    after transaction.Start()."""
    if transaction is None:
        return

    try:
        fh_opts = transaction.GetFailureHandlingOptions()
        fh_opts.SetFailuresPreprocessor(AutoResolveCopyWarnings())
        fh_opts.SetClearAfterRollback(True)

        try:
            fh_opts.SetForcedModalHandling(False)
        except Exception:
            pass

        try:
            fh_opts.SetDelayedMiniWarnings(False)
        except Exception:
            pass

        transaction.SetFailureHandlingOptions(fh_opts)

    except Exception as e:
        _log("  [FAILURE SUPPRESS WARN] could not apply failure handling: {}".format(e))


# ============================================================
# GROUP GEOMETRY HELPERS
# ============================================================

def _lower_floating_members(document, group, shift_ft):
    """Lower every free-standing, level-based family instance in `group`
    (not hosted on a wall/floor, not a door or window) by `shift_ft`: set its
    "Elevation from Level" back to what it was in the apartment file."""
    if abs(shift_ft) < 1e-3:
        return 0

    skip = (int(BuiltInCategory.OST_Doors), int(BuiltInCategory.OST_Windows))
    transaction = Transaction(document, "Correct member heights")
    transaction.Start()
    apply_silent_failure_handling(transaction)
    lowered, failed = 0, 0
    try:
        for member_id in group.GetMemberIds():
            element = document.GetElement(member_id)
            if not isinstance(element, FamilyInstance):
                continue
            try:
                if element.Category is None or element.Category.Id.IntegerValue in skip:
                    continue
                if element.Host is not None:
                    continue     # hosted: moves with its host
            except Exception:
                continue

            parameter = None
            for built_in in (
                BuiltInParameter.INSTANCE_ELEVATION_PARAM,
                BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM,
            ):
                candidate = element.get_Parameter(built_in)
                if candidate is not None and not candidate.IsReadOnly:
                    parameter = candidate
                    break
            if parameter is None:
                continue
            try:
                parameter.Set(parameter.AsDouble() - shift_ft)
                lowered += 1
            except Exception:
                failed += 1
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    _log("Member heights corrected: {} lowered, {} could not be.".format(lowered, failed))
    return lowered


def get_group_center_at_lowest_wall_ft(grp):
    """Centre point (FEET) of the group: its bounding box's horizontal
    (X, Y) centre, at the Z of the lowest point of any Wall member of the
    group - not the group's own bounding-box minimum Z, which could be
    pulled down (or up) by non-wall members (furniture legs, a slab,
    whatever else happens to be in the group) and so isn't reliably "the
    apartment's own floor level" the way its walls' own lowest point is.
    Falls back to the bounding box's own minimum Z if the group has no
    (non-nested - matches collect_source_walls) Wall members. Used as the
    group's own alignment reference point, matching the picked
    footprint's own lowest-face centre it gets aligned to (see
    "Вибір плями квартири.pushbutton"\\script.py)."""
    bbox = grp.get_BoundingBox(None) if grp else None
    if bbox is None:
        return XYZ(0.0, 0.0, 0.0)

    center_x = (bbox.Min.X + bbox.Max.X) * 0.5
    center_y = (bbox.Min.Y + bbox.Max.Y) * 0.5

    lowest_wall_z = None

    try:
        member_ids = grp.GetMemberIds()
    except Exception:
        member_ids = []

    for eid in member_ids:
        el = grp.Document.GetElement(eid)
        if not isinstance(el, Wall):
            continue

        try:
            wall_bbox = el.get_BoundingBox(None)
        except Exception:
            wall_bbox = None

        if wall_bbox is None:
            continue

        if lowest_wall_z is None or wall_bbox.Min.Z < lowest_wall_z:
            lowest_wall_z = wall_bbox.Min.Z

    if lowest_wall_z is None:
        lowest_wall_z = bbox.Min.Z

    return XYZ(center_x, center_y, lowest_wall_z)


def rotate_xy_about_origin_ft(pt_rel_ft, angle_rad):
    """Rotate relative XYZ around Z axis, keep Z unchanged."""
    if pt_rel_ft is None:
        return None
    if abs(angle_rad) < 1e-12:
        return pt_rel_ft
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)

    return XYZ(
        pt_rel_ft.X * c - pt_rel_ft.Y * s,
        pt_rel_ft.X * s + pt_rel_ft.Y * c,
        pt_rel_ft.Z
    )


# ============================================================
# GROUP LEVEL / MEMBER RE-HOST
# ============================================================


# ============================================================
# WALLS
# ============================================================

def get_wall_midpoint_ft(wall):
    """Wall midpoint in FEET (curve mid or bbox center)."""
    if wall is None:
        return XYZ(0.0, 0.0, 0.0)
    try:
        if hasattr(wall, "IsValidObject") and not wall.IsValidObject:
            return XYZ(0.0, 0.0, 0.0)
    except Exception:
        pass

    try:
        loc = wall.Location
        if loc and hasattr(loc, "Curve") and loc.Curve:
            c = loc.Curve
            return c.Evaluate(0.5, True)
    except Exception:
        pass

    try:
        bbox = wall.get_BoundingBox(None)
        if bbox:
            return (bbox.Min + bbox.Max) * 0.5
    except Exception:
        pass

    return XYZ(0.0, 0.0, 0.0)


def get_wall_direction2d_ft(wall):
    """Unit direction vector of a wall in XY (feet)."""
    if wall is None:
        return None
    try:
        if hasattr(wall, "IsValidObject") and not wall.IsValidObject:
            return None
    except Exception:
        pass

    try:
        loc = wall.Location
        if not loc or not hasattr(loc, "Curve") or not loc.Curve:
            return None
        c = loc.Curve
        try:
            p0 = c.GetEndPoint(0)
            p1 = c.GetEndPoint(1)
        except Exception:
            try:
                p0 = c.Evaluate(0.0, True)
                p1 = c.Evaluate(1.0, True)
            except Exception:
                return None

        vx = p1.X - p0.X
        vy = p1.Y - p0.Y
        length = (vx * vx + vy * vy) ** 0.5
        if length < 1e-9:
            return None
        return XYZ(vx / length, vy / length, 0.0)
    except Exception:
        return None


def collect_source_walls(src_group):
    """Return dict: src_wall_id -> {wall, mid_ft, dir2d}."""
    res = {}
    if not src_group:
        return res
    for eid in src_group.GetMemberIds():
        el = src_group.Document.GetElement(eid)
        if isinstance(el, Wall):
            mid_ft = get_wall_midpoint_ft(el)
            dir2d = get_wall_direction2d_ft(el)
            res[eid.IntegerValue] = {
                "wall": el,
                "mid_ft": mid_ft,
                "dir2d": dir2d,
            }
    return res


# ============================================================
# DETAIL GROUPS & DIMENSION SCAN
# ============================================================

def attached_detail_groups(src_doc, model_group_type):
    """Every view-specific (detail) group attached to the model group of type
    `model_group_type`: the "D" dimensions and the "T" tags, as placed in the
    source file's plan view."""
    if model_group_type is None:
        return []
    mg_name = _element_name(model_group_type) or ""
    found = []
    for g in FilteredElementCollector(src_doc).OfClass(Group):
        try:
            if not g.ViewSpecific or g.GroupType is None:
                continue
            p_att = g.LookupParameter("Attached to")
            attached_to = p_att.AsString() if p_att else None
            if attached_to and attached_to != mg_name:
                continue
        except Exception:
            continue
        found.append(g)
    return found


def find_detail_groups_for_model_group_type(src_doc, model_group_type):
    """Find detail groups for a given model group type."""
    if model_group_type is None:
        _log("  (Detail scan) No model group type; skip.")
        return []

    mg_name = _element_name(model_group_type) or ""
    _log("  (Detail scan) Model group type name = '{}'".format(mg_name))

    seen_names = set()
    candidate_groups = []

    for g in FilteredElementCollector(src_doc).OfClass(Group):
        try:
            if not g.ViewSpecific:
                continue
        except Exception:
            continue

        gt = g.GroupType
        if gt is None:
            continue

        gtype_name = _element_name(gt) or ""
        seen_names.add(gtype_name)

        attached_ok = True
        p_att = g.LookupParameter("Attached to")
        if p_att:
            try:
                atxt = p_att.AsString()
            except Exception:
                atxt = None
            if atxt and atxt != mg_name:
                attached_ok = False

        if not attached_ok:
            continue

        candidate_groups.append(g)

    if not candidate_groups:
        _log("  (Detail scan) No view-specific groups attached to '{}'.".format(mg_name))
        return []

    labels = ["Floor Plan: D", "D"]
    d_named = []
    for g in candidate_groups:
        gtype_name = _element_name(g.GroupType) or ""
        if any(
            gtype_name == lbl or gtype_name.endswith(lbl) or lbl in gtype_name
            for lbl in labels
        ):
            d_named.append(g)

    if d_named:
        _log("  (Detail scan) Using {} 'D'-named detail group(s) for '{}'.".format(len(d_named), mg_name))
        return d_named

    fallback = []
    for g in candidate_groups:
        gtype_name = _element_name(g.GroupType) or ""
        if gtype_name == mg_name or mg_name in gtype_name:
            fallback.append(g)

    if fallback:
        _log("  (Detail scan) Using fallback detail groups attached to '{}' ({} group(s), no 'D' in name).".format(
            mg_name, len(fallback)))
        return fallback

    _log("  (Detail scan) No matching 'D' or '{}' detail groups; using ALL {} candidate view-specific groups.".format(
        mg_name, len(candidate_groups)))
    return candidate_groups


def analyze_detail_dimensions(src_doc, detail_group, src_group_origin_ft, wall_info_src):
    """Capture dimension metadata from a detail group."""
    dim_list = []
    for eid in detail_group.GetMemberIds():
        el = src_doc.GetElement(eid)
        if isinstance(el, Dimension):
            dim_list.append(el)

    kind_priority = {
        "other/centerline": 0,
        "face": 1,
        "face_exterior": 2,
        "face_interior": 2,
    }

    for dim in dim_list:
        dim_id = dim.Id.IntegerValue

        dim_type_name = "<unknown>"
        try:
            dt = dim.DimensionType
            if dt:
                dim_type_name = _element_name(dt) or "<no name>"
        except Exception:
            pass

        wall_ids = []
        wall_ref_kinds = {}
        ref_infos = []

        try:
            refs = dim.References
        except Exception:
            refs = None

        if refs:
            for r in refs:
                try:
                    rid = r.ElementId
                    if not rid or rid == ElementId.InvalidElementId:
                        continue

                    ref_el = src_doc.GetElement(rid)
                    if ref_el is None:
                        continue

                    ref_id_int = rid.IntegerValue
                    cat = ref_el.Category
                    cat_name = cat.Name if cat else ref_el.__class__.__name__

                    ref_kind_for_info = None
                    offset_from_axis_ft = None

                    if isinstance(ref_el, Wall):
                        wall_ids.append(ref_id_int)

                        ref_kind_local = "other/centerline"

                        face_side = None
                        try:
                            geom_obj = ref_el.GetGeometryObjectFromReference(r)
                        except Exception:
                            geom_obj = None

                        if isinstance(geom_obj, Face):
                            ref_kind_local = "face"
                            try:
                                loc = ref_el.Location
                                if loc and hasattr(loc, "Curve") and loc.Curve:
                                    c = loc.Curve
                                    mid = c.Evaluate(0.5, True)
                                    pt_on_face = getattr(geom_obj, "Origin", None)
                                    if pt_on_face is not None:
                                        n = ref_el.Orientation
                                        d = (pt_on_face - mid).DotProduct(n)
                                        if abs(d) > 1e-6:
                                            offset_from_axis_ft = abs(d)
                                            if d > 0:
                                                face_side = "exterior"
                                            else:
                                                face_side = "interior"
                            except Exception:
                                face_side = None
                                offset_from_axis_ft = None

                            if face_side == "exterior":
                                ref_kind_local = "face_exterior"
                            elif face_side == "interior":
                                ref_kind_local = "face_interior"

                        prev_kind = wall_ref_kinds.get(ref_id_int)
                        if (prev_kind is None or
                                kind_priority.get(ref_kind_local, 0) > kind_priority.get(prev_kind, 0)):
                            wall_ref_kinds[ref_id_int] = ref_kind_local

                        ref_kind_for_info = ref_kind_local

                    stable_str = None
                    try:
                        stable_str = r.ConvertToStableRepresentation(src_doc)
                    except Exception:
                        stable_str = None

                    ref_infos.append({
                        "src_id": ref_id_int,
                        "cat": cat_name,
                        "stable": stable_str,
                        "kind": ref_kind_for_info,
                        "offset_from_axis_ft": offset_from_axis_ft,
                    })

                except Exception:
                    continue

        d_curve = None
        p0_abs = None
        p1_abs = None
        try:
            d_curve = dim.Curve
        except Exception:
            d_curve = None

        if d_curve is not None:
            try:
                p0_abs = d_curve.GetEndPoint(0)
                p1_abs = d_curve.GetEndPoint(1)
            except Exception:
                try:
                    p0_abs = d_curve.Evaluate(0.0, True)
                    p1_abs = d_curve.Evaluate(1.0, True)
                except Exception:
                    p0_abs = None
                    p1_abs = None

        center_abs = None
        try:
            origin = dim.Origin
            if origin is not None:
                center_abs = origin
        except Exception:
            center_abs = None

        if center_abs is None:
            if p0_abs is not None and p1_abs is not None:
                center_abs = XYZ(
                    0.5 * (p0_abs.X + p1_abs.X),
                    0.5 * (p0_abs.Y + p1_abs.Y),
                    0.5 * (p0_abs.Z + p1_abs.Z),
                )
            else:
                pts = []
                for wid in wall_ids:
                    info = wall_info_src.get(wid)
                    if info:
                        pts.append(info["mid_ft"])

                if pts:
                    sx = sy = sz = 0.0
                    for p in pts:
                        sx += p.X
                        sy += p.Y
                        sz += p.Z
                    cx = sx / len(pts)
                    cy = sy / len(pts)
                    cz = sz / len(pts)
                    center_abs = XYZ(cx, cy, cz)
                else:
                    bbox = dim.get_BoundingBox(None)
                    if bbox:
                        c = (bbox.Min + bbox.Max) * 0.5
                        center_abs = XYZ(c.X, c.Y, c.Z)
                    else:
                        center_abs = XYZ(0.0, 0.0, 0.0)

        center_rel_ft = center_abs - src_group_origin_ft

        perp_wall_id = None
        perp_angle_deg = None
        perp_offset_from_axis_ft = None

        if wall_ids:
            main_wall_id = wall_ids[0]
            main_info = wall_info_src.get(main_wall_id)
            main_dir2d = main_info.get("dir2d") if main_info else None

            if main_dir2d is not None:
                best_dist2 = None
                max_angle_from_90 = 10.0

                for wid_cand, info_c in wall_info_src.items():
                    if wid_cand == main_wall_id:
                        continue
                    dir2 = info_c.get("dir2d")
                    if dir2 is None:
                        continue

                    dot = main_dir2d.X * dir2.X + main_dir2d.Y * dir2.Y
                    if dot < -1.0:
                        dot = -1.0
                    elif dot > 1.0:
                        dot = 1.0

                    ang_deg = math.degrees(math.acos(abs(dot)))
                    angle_from_90 = abs(90.0 - ang_deg)
                    if angle_from_90 > max_angle_from_90:
                        continue

                    mid_c = info_c["mid_ft"]
                    dx = mid_c.X - center_abs.X
                    dy = mid_c.Y - center_abs.Y
                    dz = mid_c.Z - center_abs.Z
                    d2 = dx * dx + dy * dy + dz * dz

                    if best_dist2 is None or d2 < best_dist2:
                        best_dist2 = d2
                        perp_wall_id = wid_cand
                        perp_angle_deg = ang_deg

                if perp_wall_id is not None:
                    w_perp = None
                    info_p = wall_info_src.get(perp_wall_id)
                    if info_p is not None:
                        w_perp = info_p.get("wall")
                    if w_perp is None:
                        try:
                            w_perp = src_doc.GetElement(ElementId(perp_wall_id))
                        except Exception:
                            w_perp = None

                    if w_perp is not None:
                        try:
                            locp = w_perp.Location
                            axis = getattr(locp, "Curve", None)
                        except Exception:
                            axis = None

                        if axis is not None:
                            try:
                                proj_res = axis.Project(center_abs)
                            except Exception:
                                proj_res = None

                            if proj_res is not None:
                                proj_pt = proj_res.XYZPoint
                                v = center_abs - proj_pt
                                perp_offset_from_axis_ft = math.sqrt(
                                    v.X * v.X + v.Y * v.Y + v.Z * v.Z
                                )

        line_rel_p0_ft = None
        line_rel_p1_ft = None
        if p0_abs is not None and p1_abs is not None:
            line_rel_p0_ft = p0_abs - src_group_origin_ft
            line_rel_p1_ft = p1_abs - src_group_origin_ft
        else:
            dim_dir2d = None
            for wid in wall_ids:
                info_w = wall_info_src.get(wid)
                if not info_w:
                    continue
                w_dir = info_w.get("dir2d")
                if not w_dir:
                    continue
                vx = -w_dir.Y
                vy = w_dir.X
                length = (vx * vx + vy * vy) ** 0.5
                if length < 1e-9:
                    continue
                dim_dir2d = XYZ(vx / length, vy / length, 0.0)
                break

            if dim_dir2d is not None:
                L = 5.0
                off_vec = XYZ(dim_dir2d.X * L, dim_dir2d.Y * L, 0.0)
                p0_est = XYZ(center_abs.X - off_vec.X, center_abs.Y - off_vec.Y, center_abs.Z)
                p1_est = XYZ(center_abs.X + off_vec.X, center_abs.Y + off_vec.Y, center_abs.Z)
                line_rel_p0_ft = p0_est - src_group_origin_ft
                line_rel_p1_ft = p1_est - src_group_origin_ft
            else:
                line_rel_p0_ft = center_rel_ft
                line_rel_p1_ft = center_rel_ft
                _log("    Dim Id {}: could not capture original curve endpoints; using center-only approximation.".format(dim_id))

        yield {
            "id": dim_id,
            "wall_ids": wall_ids,
            "center_rel_ft": center_rel_ft,
            "type_name": dim_type_name,
            "line_rel_p0_ft": line_rel_p0_ft,
            "line_rel_p1_ft": line_rel_p1_ft,
            "ref_infos": ref_infos,
            "perp_wall_id": perp_wall_id,
            "perp_angle_deg": perp_angle_deg,
            "perp_offset_from_axis_ft": perp_offset_from_axis_ft,
        }


# ============================================================
# WALL MAPPING (source -> host)
# ============================================================

def _reflect_xyz(vector, normal):
    """`vector` (XYZ) mirrored about a plane through the origin with 2D `normal`."""
    if normal is None or vector is None:
        return vector
    x, y = _reflect((vector.X, vector.Y), normal)
    return XYZ(x, y, vector.Z)


def map_walls_by_relative_position(src_group_origin_ft, host_group_origin_ft, wall_info_src, host_walls, label=None, rotation_rad=0.0, mirror_normal=None):
    """Build mapping src_wall_id -> host wall info by sorted relative position inside group."""
    prefix = "[{}] ".format(label) if label else ""
    src_rel_list = []
    for wid, info in wall_info_src.items():
        mid_ft = info["mid_ft"]
        rel = _reflect_xyz(rotate_xy_about_origin_ft(mid_ft - src_group_origin_ft, rotation_rad), mirror_normal)
        src_rel_list.append((wid, rel))

    host_rel_list = []
    for w in host_walls:
        mid_ft = get_wall_midpoint_ft(w)
        rel = mid_ft - host_group_origin_ft
        host_rel_list.append((w.Id.IntegerValue, rel))

    def key_func(rel_xyz):
        return (round(rel_xyz.X, 6), round(rel_xyz.Y, 6), round(rel_xyz.Z, 6))

    src_rel_list.sort(key=lambda item: key_func(item[1]))
    host_rel_list.sort(key=lambda item: key_func(item[1]))

    count = min(len(src_rel_list), len(host_rel_list))
    _log("  {}Wall mapping: {} source walls, {} host walls, pairing {} by relative position.".format(
        prefix, len(src_rel_list), len(host_rel_list), count))

    wall_map = {}
    max_to_log = 8
    for idx in range(count):
        src_id, src_rel = src_rel_list[idx]
        host_id, host_rel = host_rel_list[idx]

        dx = src_rel.X - host_rel.X
        dy = src_rel.Y - host_rel.Y
        dz = src_rel.Z - host_rel.Z
        d_len = (dx * dx + dy * dy + dz * dz) ** 0.5

        if idx < max_to_log:
            _log("    {}SRC wall Id {} -> HOST wall Id {} | Δrel≈{:.4f} m".format(
                prefix, src_id, host_id, d_len * FT_TO_M
            ))

        wall_map[src_id] = {
            "host_id": host_id,
            "rel_src_ft": src_rel,
            "rel_host_ft": host_rel,
            "delta_rel_len_sq": d_len * d_len,
        }

    if count > max_to_log:
        _log("    {}... {} more wall pairs omitted from log.".format(prefix, count - max_to_log))

    if len(src_rel_list) > count:
        _log("  {}Wall mapping: {} source walls left unmatched.".format(prefix, len(src_rel_list) - count))
    if len(host_rel_list) > count:
        _log("  {}Wall mapping: {} host walls left unmatched.".format(prefix, len(host_rel_list) - count))

    return wall_map


def predict_dims_for_host(dim_metas, wall_map, host_group_origin_ft, rotation_rad=0.0):
    if not dim_metas:
        _log("  No captured Dimensions → no prediction.")
        return
    _log("  Predicted Dimension → host Walls mapping (with host position):")
    for dm in dim_metas:
        dim_id = dm["id"]
        src_wall_ids = dm["wall_ids"]
        center_rel_ft = rotate_xy_about_origin_ft(dm["center_rel_ft"], rotation_rad)

        host_wall_ids = []
        for wid in src_wall_ids:
            info = wall_map.get(wid)
            host_wall_ids.append(info["host_id"] if info else None)

        host_center_ft = XYZ(
            host_group_origin_ft.X + center_rel_ft.X,
            host_group_origin_ft.Y + center_rel_ft.Y,
            host_group_origin_ft.Z + center_rel_ft.Z
        )

        _log("    Dim {}: SRC walls {} -> HOST walls {} ; host mid ≈ ({:.3f}, {:.3f}, {:.3f}) m".format(
            dim_id, src_wall_ids, host_wall_ids,
            host_center_ft.X * FT_TO_M, host_center_ft.Y * FT_TO_M, host_center_ft.Z * FT_TO_M
        ))


# ============================================================
# DIMENSION TYPE CHOICE
# ============================================================

def choose_host_dim_type(src_type_name, host_dim_type_by_name, host_dim_type_default):
    global _dim_type_map_cache
    if not host_dim_type_by_name:
        return host_dim_type_default

    if not src_type_name or src_type_name in ("<unknown>", "<no name>"):
        return host_dim_type_default

    dt = host_dim_type_by_name.get(src_type_name)
    if dt:
        return dt

    if src_type_name in _dim_type_map_cache:
        return _dim_type_map_cache[src_type_name]

    all_host_types = list(host_dim_type_by_name.values())
    if not all_host_types:
        return host_dim_type_default

    chosen = random.choice(all_host_types)
    _dim_type_map_cache[src_type_name] = chosen
    _log("  Random host DimensionType chosen for source type '{}': '{}' (Id={})".format(
        src_type_name, _element_name(chosen), chosen.Id.IntegerValue))
    return chosen


# ============================================================
# VIEW FOR DIMENSIONS
# ============================================================

# ============================================================
# ROOM TAGS
#
# Copied like the dimensions: read from the apartment FILE (the plan views the
# "D" detail groups live in), re-created in the host's plan view on the
# placed apartment's own rooms - same tag type, same leader, same offset from
# the room as in the source (rotated / mirrored together with the apartment).
# ============================================================

def _is_room_tag_group(document, detail_group):
    """True if the view-specific `detail_group` holds room tags - the "T" group
    (tags together with the apartment tag's frame and leader): it is kept whole
    and moves with the apartment. (The "D" group, dimensions, is rebuilt.)"""
    try:
        from Autodesk.Revit.DB.Architecture import RoomTag
    except Exception:
        return False

    try:
        member_ids = list(detail_group.GetMemberIds())
    except Exception:
        return False

    if not member_ids:
        return False

    for member_id in member_ids:
        if isinstance(document.GetElement(member_id), RoomTag):
            return True
    return False


def _log_source_annotations(src_doc, view_ids, detail_groups):
    """Diagnostic: list what the source apartment's detail views and detail
    groups actually contain (category / class / type name), so a tag or frame
    that isn't being copied can be identified exactly."""
    try:
        for detail_group in detail_groups or []:
            counts = {}
            for member_id in detail_group.GetMemberIds():
                member = src_doc.GetElement(member_id)
                if member is None:
                    continue
                try:
                    category = member.Category.Name if member.Category else "-"
                except Exception:
                    category = "-"
                try:
                    type_name = _element_name(src_doc.GetElement(member.GetTypeId()))
                except Exception:
                    type_name = ""
                key = (category, member.__class__.__name__, type_name)
                counts[key] = counts.get(key, 0) + 1
            _log("  (Annotation scan) detail group '{}' members: {}".format(
                _element_name(detail_group.GroupType),
                "; ".join("{} x {} / {} / '{}'".format(n, c[0], c[1], c[2]) for c, n in sorted(counts.items())) or "none",
            ))

        for view_id in view_ids:
            counts = {}
            for element in FilteredElementCollector(src_doc, view_id).WhereElementIsNotElementType():
                try:
                    if element.OwnerViewId != view_id:
                        continue  # model elements, not this view's own annotations
                    category = element.Category.Name if element.Category else "-"
                except Exception:
                    continue
                try:
                    type_name = _element_name(src_doc.GetElement(element.GetTypeId()))
                except Exception:
                    type_name = ""
                key = (category, element.__class__.__name__, type_name)
                counts[key] = counts.get(key, 0) + 1
            _log("  (Annotation scan) view '{}' own elements: {}".format(
                _element_name(src_doc.GetElement(view_id)),
                "; ".join("{} x {} / {} / '{}'".format(n, c[0], c[1], c[2]) for c, n in sorted(counts.items())) or "none",
            ))
    except Exception as error:
        _log("  (Annotation scan) failed: {}".format(error))


def _tag_leader_end_offset(tag, room_point):
    """Where the tag's leader ends, relative to its room's location point -
    (dx, dy) in feet - or None (no leader / not readable)."""
    try:
        if not tag.HasLeader:
            return None
        end = tag.LeaderEnd
        return (end.X - room_point.X, end.Y - room_point.Y)
    except Exception:
        return None


def analyze_room_tags(src_doc, src_group, detail_groups):
    """Room tags of the source apartment: for every RoomTag in the views the
    detail groups belong to (the same view the dimensions come from) whose
    room is a member of src_group, one dict with the room's name/number/
    area/location and the tag's type name, leader flag and head offset from
    the room. File-only data - the same for every placement."""
    try:
        from Autodesk.Revit.DB.Architecture import RoomTag
    except Exception:
        return []

    view_ids = []
    for detail_group in detail_groups or []:
        try:
            owner = detail_group.OwnerViewId
        except Exception:
            continue
        if owner is not None and owner != ElementId.InvalidElementId and owner not in view_ids:
            view_ids.append(owner)

    if not view_ids:
        return []

    _log_source_annotations(src_doc, view_ids, detail_groups)

    member_ids = set(eid.IntegerValue for eid in src_group.GetMemberIds())
    metas = []

    for view_id in view_ids:
        # By category (OfClass(RoomTag) is rejected by Revit: RoomTag exists
        # in the API but not in its native object model).
        for tag in (
            FilteredElementCollector(src_doc, view_id)
            .OfCategory(BuiltInCategory.OST_RoomTags)
            .WhereElementIsNotElementType()
        ):
            try:
                if not isinstance(tag, RoomTag):
                    continue
                room = tag.Room
                if room is None or room.Id.IntegerValue not in member_ids:
                    continue

                room_point = room.Location.Point
                head = tag.TagHeadPosition
                tag_type = src_doc.GetElement(tag.GetTypeId())

                metas.append({
                    "room_name": room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or "",
                    "room_number": room.Number or "",
                    "room_area": round(room.Area, 3),
                    "room_xy": (room_point.X, room_point.Y),
                    "offset_ft": (head.X - room_point.X, head.Y - room_point.Y),
                    "type_name": _element_name(tag_type),
                    "type_id": tag_type.Id.IntegerValue,
                    "family_name": getattr(tag_type, "FamilyName", u""),
                    "has_leader": bool(tag.HasLeader),
                    "leader_end_offset": _tag_leader_end_offset(tag, room_point),
                    # where the source tag is (to copy it from there)
                    "tag_id": tag.Id.IntegerValue,
                    "view_id": view_id.IntegerValue,
                })
            except Exception:
                continue

    return metas


def _reflect(vector, normal):
    """`vector` mirrored about a plane through the origin with 2D `normal`."""
    length = math.hypot(normal[0], normal[1])
    if length < 1e-9:
        return vector
    n_x, n_y = normal[0] / length, normal[1] / length
    along = vector[0] * n_x + vector[1] * n_y
    return (vector[0] - 2.0 * along * n_x, vector[1] - 2.0 * along * n_y)


# The framed apartment tag ("1к 41,11"). Its TYPE is taken as it is from the
# apartment's file; one tag per apartment is placed OUTSIDE the apartment, on
# its outside side, with a straight leader perpendicular to that side reaching
# the nearest room.
# How to make the dimensions and room tags of an apartment again (placed group
# UniqueId -> data): they are attached to the placed group, and ungrouping it -
# to move or delete walls - makes Revit drop them. (Kept in memory.)
_rebuild_data = {}


def keep_rebuild_data(group_uid, data):
    _rebuild_data[group_uid] = data


def take_rebuild_data(group_uid):
    return _rebuild_data.pop(group_uid, None)


def _tag_transform(angle_rad, needs_mirror, mirror_check_side, room_mirror_plane):
    """(rotation, mirror normal or None) that carry a source tag offset into the
    host the way the apartment itself was turned/mirrored."""
    mirror = (
        (mirror_check_side["normalXFt"], mirror_check_side["normalYFt"])
        if needs_mirror and mirror_check_side else None
    )
    rotation = angle_rad
    if room_mirror_plane is not None:
        # The extra mirror (about the apartment's centre) comes after the
        # placement's own: on its own it is a plain mirror; after another mirror
        # the pair is a rotation.
        if mirror is None:
            mirror = (room_mirror_plane[0], room_mirror_plane[1])
        else:
            rotation = angle_rad + 2.0 * (
                math.atan2(room_mirror_plane[1], room_mirror_plane[0]) - math.atan2(mirror[1], mirror[0])
            )
            mirror = None
    return rotation, mirror


def rebuild_annotations(document, group, data):
    """Dimensions and room tags of `group` (an apartment that was ungrouped and
    grouped again) from `data` (see keep_rebuild_data): the dimensions are tied
    to the walls as they are now, the tags to the rooms. Returns (dimensions
    made, tags made, tags still waiting for enclosed rooms)."""
    level = document.GetElement(ElementId(int(data["level_id"])))
    view = get_dimension_view(document, level)
    if view is None:
        return 0, 0, []

    before = FilteredElementCollector(document, view.Id).OfCategory(BuiltInCategory.OST_Dimensions).GetElementCount()
    by_name, default = data["dim_types"]
    create_host_dimensions(
        document, view, data["dim_metas"], data["wall_map"], by_name, default,
        data["host_origin"], rotation_rad=data["rotation"], mirror_normal=data.get("mirror"),
    )
    after = FilteredElementCollector(document, view.Id).OfCategory(BuiltInCategory.OST_Dimensions).GetElementCount()

    created, failed = create_host_room_tags(
        document, view, group, data["tag_metas"], data["tag_rotation"], data["tag_mirror"], None,
    )
    return max(0, after - before), created, list(failed)


class _AttachedGroupsShown(Exception):
    """Control flow only: the attached detail groups were shown natively."""


_APARTMENT_TAG_TYPE = u"П_Без площ з коефіцієнтами"
# Two families have a type of that name; the apartment tag is the one of THIS
# family (not "AVR_Марка_Приміщення_Квартира нова").
_APARTMENT_TAG_FAMILY = u"AVR_Марка_Приміщення_Квартира"


def _find_room_tag_type(document, family_name, type_name):
    """The room tag type `type_name` of the family `family_name`, or None."""
    for tag_type in (
        FilteredElementCollector(document)
        .OfCategory(BuiltInCategory.OST_RoomTags)
        .WhereElementIsElementType()
    ):
        try:
            if tag_type.FamilyName == family_name and _element_name(tag_type) == type_name:
                return tag_type
        except Exception:
            continue
    return None
_APARTMENT_TAG_DISTANCE_FT = 10.0 / 1.0     # head this far outside the outside side (~3 m)


def create_host_room_tags(
    document, view, grp_host, tag_metas, rotation_rad, mirror_normal=None, src_doc=None,
):
    """Tag the placed apartment's rooms in `view` the way the source file
    does. Each source tag is matched to a host room by name, number and area
    (ties broken by position relative to the apartment's centre, rotated and
    mirrored like the apartment itself). Returns the number of tags created."""
    if not tag_metas or view is None:
        return 0, []

    try:
        from Autodesk.Revit.DB import LinkElementId, UV
        from Autodesk.Revit.DB.Architecture import Room
    except Exception:
        return 0, list(tag_metas)

    host_rooms = []
    for member_id in grp_host.GetMemberIds():
        element = document.GetElement(member_id)
        if isinstance(element, Room) and element.Location is not None:
            host_rooms.append(element)

    if not host_rooms:
        _log("  Room tags: the placed apartment has no rooms - nothing to tag.")
        return 0, list(tag_metas)

    try:
        _log("  Room tags: placed rooms (area m2): {}".format(
            ", ".join("{:.2f}".format(room.Area * FT_TO_M * FT_TO_M) for room in host_rooms)
        ))
    except Exception:
        pass

    host_tag_types = {}
    host_tag_types_by_family = {}
    for tag_type in (
        FilteredElementCollector(document)
        .OfCategory(BuiltInCategory.OST_RoomTags)
        .WhereElementIsElementType()
    ):
        host_tag_types.setdefault(_element_name(tag_type), tag_type)
        host_tag_types_by_family.setdefault((getattr(tag_type, "FamilyName", u""), _element_name(tag_type)), tag_type)

    # Tags that already exist in the view (e.g. the ones that came along in
    # the apartment's own detail group): (room id, tag type name) pairs.
    existing_pairs = set()
    existing_tags = []    # (room id, tag type name, tag)
    try:
        from Autodesk.Revit.DB.Architecture import RoomTag as _ExistingRoomTag

        for existing in (
            FilteredElementCollector(document, view.Id)
            .OfCategory(BuiltInCategory.OST_RoomTags)
            .WhereElementIsNotElementType()
        ):
            try:
                if isinstance(existing, _ExistingRoomTag) and existing.Room is not None:
                    pair = (
                        existing.Room.Id.IntegerValue,
                        _element_name(document.GetElement(existing.GetTypeId())),
                    )
                    existing_pairs.add(pair)
                    existing_tags.append((pair[0], pair[1], existing))
            except Exception:
                continue
    except Exception:
        pass

    failed = []

    def _host_xy(room):
        point = room.Location.Point
        return (point.X, point.Y)

    def _centre(points):
        return (
            sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points),
        )

    cos_a, sin_a = math.cos(rotation_rad), math.sin(rotation_rad)

    def _transform(vector):
        rotated = (vector[0] * cos_a - vector[1] * sin_a, vector[0] * sin_a + vector[1] * cos_a)
        return _reflect(rotated, mirror_normal) if mirror_normal is not None else rotated

    # Source room positions (once per room) relative to the source centre,
    # carried into the host frame, to break name/area ties.
    source_rooms = {}
    for meta in tag_metas:
        if meta.get("kind") == "apartment_tag":
            continue
        key = (meta["room_name"], meta["room_number"], meta["room_area"])
        source_rooms[key] = meta["room_xy"]
    source_centre = _centre(list(source_rooms.values())) if source_rooms else (0.0, 0.0)
    host_centre = _centre([_host_xy(room) for room in host_rooms])

    created = 0

    transaction = Transaction(document, "Place room tags")
    transaction.Start()
    apply_silent_failure_handling(transaction)
    try:
        # Tag types the project doesn't have yet (e.g. "П_Площа 2", or the
        # framed "П_Без площ з коефіцієнтами") are copied over from the
        # apartment file, otherwise the tags would come out in a default type.
        missing_types = {}
        for meta in tag_metas:
            if meta.get("kind") == "apartment_tag":
                continue
            key = (meta.get("family_name", u""), meta["type_name"])
            if key not in host_tag_types_by_family and meta.get("type_id") is not None:
                missing_types[key] = meta["type_id"]

        if missing_types and src_doc is not None:
            try:
                copy_options = CopyPasteOptions()
                copy_options.SetDuplicateTypeNamesHandler(UseDestinationTypes())
                ElementTransformUtils.CopyElements(
                    src_doc,
                    List[ElementId]([ElementId(type_id) for type_id in missing_types.values()]),
                    document, Transform.Identity, copy_options,
                )
                document.Regenerate()
            except Exception as error:
                _log("[PLACEMENT WARN] Room tag types could not be copied from the apartment file: {}".format(error))

            for tag_type in (
                FilteredElementCollector(document)
                .OfCategory(BuiltInCategory.OST_RoomTags)
                .WhereElementIsElementType()
            ):
                host_tag_types.setdefault(_element_name(tag_type), tag_type)
                host_tag_types_by_family.setdefault(
                    (getattr(tag_type, "FamilyName", u""), _element_name(tag_type)), tag_type)

        for meta in tag_metas:
            if meta.get("kind") == "apartment_tag":
                # The framed apartment tag: outside the apartment, straight
                # leader to the nearest room (see _apartment_tag_spec).
                try:
                    spec = meta["spec"]
                    room = min(
                        host_rooms,
                        key=lambda r: math.hypot(
                            _host_xy(r)[0] - spec["room_xy"][0], _host_xy(r)[1] - spec["room_xy"][1]
                        ),
                    )
                    # The exact type: this FAMILY's type of that name.
                    tag_type = _find_room_tag_type(document, _APARTMENT_TAG_FAMILY, meta["type_name"])
                    if tag_type is None and src_doc is not None:
                        for source_type in (
                            FilteredElementCollector(src_doc)
                            .OfCategory(BuiltInCategory.OST_RoomTags)
                            .WhereElementIsElementType()
                        ):
                            if (
                                source_type.FamilyName == _APARTMENT_TAG_FAMILY
                                and _element_name(source_type) == meta["type_name"]
                            ):
                                copy_options = CopyPasteOptions()
                                copy_options.SetDuplicateTypeNamesHandler(UseDestinationTypes())
                                ElementTransformUtils.CopyElements(
                                    src_doc, List[ElementId]([source_type.Id]), document,
                                    Transform.Identity, copy_options,
                                )
                                document.Regenerate()
                                tag_type = _find_room_tag_type(document, _APARTMENT_TAG_FAMILY, meta["type_name"])
                                break
                    if tag_type is None:
                        raise Exception(u"тип {} : {} не знайдено".format(_APARTMENT_TAG_FAMILY, meta["type_name"]))

                    host_room_ids = set(r.Id.IntegerValue for r in host_rooms)
                    tag = None
                    for room_id, type_name, existing in existing_tags:
                        # A tag of this type name that came along with the
                        # apartment is moved (and switched to the right family),
                        # not duplicated.
                        if type_name == meta["type_name"] and room_id in host_room_ids:
                            tag = existing
                            break

                    if tag is None:
                        room_xy = _host_xy(room)
                        tag = document.Create.NewRoomTag(
                            LinkElementId(room.Id), UV(room_xy[0], room_xy[1]), view.Id
                        )
                        existing_tags.append((room.Id.IntegerValue, meta["type_name"], tag))
                        existing_pairs.add((room.Id.IntegerValue, meta["type_name"]))
                        created += 1
                    if tag.GetTypeId() != tag_type.Id:
                        tag.ChangeTypeId(tag_type.Id)

                    # A straight leader: the head is first measured without the
                    # leader (its box would otherwise include the leader), then
                    # shifted sideways so the middle of the tag lies on the line
                    # from the end point straight out - the leader leaves the tag
                    # at that height - and only then is the leader drawn along it.
                    z = tag.TagHeadPosition.Z
                    head = spec["head"]
                    tag.HasLeader = False
                    tag.TagHeadPosition = XYZ(head[0], head[1], z)
                    try:
                        document.Regenerate()
                        box = tag.get_BoundingBox(view)
                        nx, ny = spec["normal"]
                        side = (-ny, nx)
                        if box is not None:
                            offset = (
                                ((box.Min.X + box.Max.X) / 2.0 - head[0]) * side[0]
                                + ((box.Min.Y + box.Max.Y) / 2.0 - head[1]) * side[1]
                            )
                            tag.TagHeadPosition = XYZ(head[0] - offset * side[0], head[1] - offset * side[1], z)
                    except Exception as error:
                        _log("[PLACEMENT WARN] Apartment tag: could not centre it on the leader line ({}).".format(error))

                    tag.HasLeader = True
                    try:
                        tag.LeaderEnd = XYZ(spec["end"][0], spec["end"][1], z)
                        tag.LeaderElbow = XYZ(
                            (head[0] + spec["end"][0]) / 2.0, (head[1] + spec["end"][1]) / 2.0, z,
                        )
                    except Exception as error:
                        _log("[PLACEMENT WARN] Apartment tag: the leader could not be set ({}).".format(error))
                except Exception as error:
                    failed.append(meta)
                    _log("[PLACEMENT WARN] Apartment tag could not be placed: {}".format(error))
                continue

            # A room may carry several tags (the area tag AND the framed
            # apartment tag), so rooms are NOT used up once tagged.
            candidates = []
            for room in host_rooms:
                try:
                    name = room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or ""
                    number = room.Number or ""
                    area = round(room.Area, 3)
                except Exception:
                    continue
                # (The room NUMBER is not compared: Revit renumbers the copied rooms
                # - 86.664 becomes 86.1085 - to keep numbers unique.)
                if name == meta["room_name"]:
                    candidates.append((abs(area - meta["room_area"]), room))

            if not candidates:
                _log("[PLACEMENT WARN] Room tag '{}': no placed room named '{}'.".format(
                    meta["type_name"], meta["room_name"]
                ))
                continue

            # (the already-tagged check needs the chosen room, see below)

            relative = _transform((
                meta["room_xy"][0] - source_centre[0], meta["room_xy"][1] - source_centre[1],
            ))
            predicted = (host_centre[0] + relative[0], host_centre[1] + relative[1])

            def _score(item):
                host_xy = _host_xy(item[1])
                return (
                    round(item[0], 1),
                    math.hypot(host_xy[0] - predicted[0], host_xy[1] - predicted[1]),
                )

            room = min(candidates, key=_score)[1]

            if (room.Id.IntegerValue, meta["type_name"]) in existing_pairs:
                continue  # this room already carries a tag of this type

            try:
                room_xy = _host_xy(room)
                tag = document.Create.NewRoomTag(
                    LinkElementId(room.Id), UV(room_xy[0], room_xy[1]), view.Id
                )

                tag_type = (
                    host_tag_types_by_family.get((meta.get("family_name", u""), meta["type_name"]))
                    or host_tag_types.get(meta["type_name"])
                )
                if tag_type is not None:
                    tag.ChangeTypeId(tag_type.Id)

                tag.HasLeader = meta["has_leader"]
                offset = _transform(meta["offset_ft"])
                head_z = tag.TagHeadPosition.Z
                tag.TagHeadPosition = XYZ(room_xy[0] + offset[0], room_xy[1] + offset[1], head_z)
                if meta["has_leader"] and meta.get("leader_end_offset"):
                    # The leader as in the apartment's file: same end, straight.
                    try:
                        end_offset = _transform(meta["leader_end_offset"])
                        end = XYZ(room_xy[0] + end_offset[0], room_xy[1] + end_offset[1], head_z)
                        tag.LeaderEnd = end
                        tag.LeaderElbow = XYZ((tag.TagHeadPosition.X + end.X) / 2.0, (tag.TagHeadPosition.Y + end.Y) / 2.0, head_z)
                    except Exception as error:
                        _log("[PLACEMENT WARN] Room tag leader could not be set: {}".format(error))
                existing_pairs.add((room.Id.IntegerValue, meta["type_name"]))
                created += 1
            except Exception as error:
                failed.append(meta)
                _log("[PLACEMENT WARN] Room tag could not be created: {}".format(error))

        status = transaction.Commit()
        if status != TransactionStatus.Committed:
            _log("[PLACEMENT WARN] Room tags transaction did not commit ({}).".format(status))
            return 0, list(tag_metas)
    except Exception as error:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        _log("[PLACEMENT WARN] Room tags failed: {}".format(error))
        return 0, list(tag_metas)

    _log("  Room tags created: {} of {}.".format(created, len(tag_metas)))
    return created, failed


# Tags that could not be created during a placement (their rooms were not
# enclosed yet) wait here for flush_deferred_room_tags.
_deferred_room_tags = []

# The tags of the apartment placed LAST that still wait for enclosed rooms; the
# caller stores them in the apartment's record (see flush_pending_room_tags).
last_pending_tags = {"metas": [], "rotation": 0.0, "mirror": None, "levelId": None}


def flush_pending_room_tags(document):
    """Make the room tags that placed apartments are still waiting for (their
    rooms were not enclosed when the apartment was placed - walls overlapping a
    neighbour's make Revit ignore room boundaries - and may be by now). Returns
    how many tags were created."""
    import placed_apartments

    total = 0
    for entry in placed_apartments.live_entries(document):
        pending = entry.get("pendingTags")
        if not pending or not pending.get("metas"):
            continue
        try:
            group = document.GetElement(ElementId(int(entry["groupId"])))
            level = document.GetElement(ElementId(int(pending["levelId"]))) if pending.get("levelId") else None
            view = get_dimension_view(document, level)
            if group is None or view is None:
                continue
            mirror = tuple(pending["mirror"]) if pending.get("mirror") else None
            created, failed = create_host_room_tags(
                document, view, group, pending["metas"], pending.get("rotation", 0.0), mirror, None,
            )
            total += created
            if created:
                placed_apartments.set_pending_tags(
                    document, entry["groupUid"],
                    dict(pending, metas=list(failed)) if failed else None,
                )
        except Exception as error:
            _log("[PLACEMENT WARN] Pending room tags failed: {}".format(error))
    return total


def flush_deferred_room_tags(document):
    """Second try for the room tags that failed during placement: run from a
    LATER ExternalEvent, when Revit has finished regenerating and the rooms
    have their boundaries. Returns how many tags were created."""
    entries = list(_deferred_room_tags)
    del _deferred_room_tags[:]

    total = 0
    for entry in entries:
        try:
            group = document.GetElement(ElementId(entry["group_id"]))
            view = document.GetElement(ElementId(entry["view_id"]))
            if group is None or view is None:
                continue
            created, _failed = create_host_room_tags(
                document, view, group, entry["metas"], entry["rotation"], entry["mirror"], None,
            )
            total += created
        except Exception as error:
            _log("[PLACEMENT WARN] Deferred room tags failed: {}".format(error))
    return total


def plan_view_of_level(host_doc, level):
    """A plan view that belongs to `level` (the level the footprint roof sits
    on): the active view when it is one, else the first other plan view of that
    level. None if there is none. Pasting into it is what a paste by hand does:
    the apartment lands on THAT level, whatever other levels the project has."""
    if level is None:
        return None
    allowed_types = (ViewType.FloorPlan, ViewType.EngineeringPlan)

    def _on_level(view):
        try:
            return (
                view is not None and not view.IsTemplate and view.ViewType in allowed_types
                and view.GenLevel is not None and view.GenLevel.Id == level.Id
            )
        except Exception:
            return False

    try:
        active = host_doc.ActiveView
        if _on_level(active):
            return active
    except Exception:
        pass

    try:
        for view in FilteredElementCollector(host_doc).OfClass(ViewPlan):
            if _on_level(view):
                return view
    except Exception:
        pass
    return None


def get_dimension_view(host_doc, level=None):
    allowed_types = (ViewType.FloorPlan, ViewType.EngineeringPlan, ViewType.CeilingPlan)
    try:
        av = host_doc.ActiveView
        if av and not av.IsTemplate:
            vt = av.ViewType
            if vt in allowed_types:
                _log("  Using ActiveView '{}' (type={}) for dimension creation.".format(_element_name(av), vt))
                return av
    except Exception:
        pass

    chosen_any_plan = None
    try:
        collector = FilteredElementCollector(host_doc).OfClass(ViewPlan)
        for vp in collector:
            try:
                if vp.IsTemplate:
                    continue
                vt = vp.ViewType
                if vt not in allowed_types:
                    continue

                if chosen_any_plan is None:
                    chosen_any_plan = vp

                if level is not None:
                    try:
                        gen_lvl = vp.GenLevel
                    except Exception:
                        gen_lvl = None
                    if gen_lvl is not None and gen_lvl.Id == level.Id:
                        _log("  Using plan view '{}' (type={}) on level '{}' for dimensions.".format(
                            _element_name(vp), vt, _element_name(level)))
                        return vp
            except Exception:
                continue
    except Exception:
        pass

    if chosen_any_plan is not None:
        _log("  Using fallback plan view '{}' (type={}) for dimensions.".format(
            _element_name(chosen_any_plan), chosen_any_plan.ViewType))
        return chosen_any_plan

    try:
        av = host_doc.ActiveView
        if av:
            _log("  WARNING: No plan view found; falling back to ActiveView '{}' (type={}).".format(
                _element_name(av), av.ViewType))
            return av
    except Exception:
        pass

    _log("  ERROR: No suitable view found for dimension creation.")
    return None


def adjust_dim_offset_to_match_source(host_doc, host_dim, host_perp_wall, src_offset_ft, src_dim_id=None, src_perp_wall_id=None):
    if host_dim is None or host_perp_wall is None:
        return
    try:
        dim_curve = host_dim.Curve
        if dim_curve is None:
            return
    except Exception:
        return

    try:
        p0 = dim_curve.GetEndPoint(0)
        p1 = dim_curve.GetEndPoint(1)
    except Exception:
        try:
            p0 = dim_curve.Evaluate(0.0, True)
            p1 = dim_curve.Evaluate(1.0, True)
        except Exception:
            return

    dim_mid = XYZ(0.5 * (p0.X + p1.X), 0.5 * (p0.Y + p1.Y), 0.5 * (p0.Z + p1.Z))

    loc = host_perp_wall.Location
    if not loc or not hasattr(loc, "Curve") or not loc.Curve:
        return

    wcurve = loc.Curve
    try:
        w0 = wcurve.GetEndPoint(0)
        w1 = wcurve.GetEndPoint(1)
    except Exception:
        try:
            w0 = wcurve.Evaluate(0.0, True)
            w1 = wcurve.Evaluate(1.0, True)
        except Exception:
            return

    axis_mid = XYZ(0.5 * (w0.X + w1.X), 0.5 * (w0.Y + w1.Y), 0.5 * (w0.Z + w1.Z))

    vx = w1.X - w0.X
    vy = w1.Y - w0.Y
    axis_len = (vx * vx + vy * vy) ** 0.5
    if axis_len < 1e-9:
        return

    axis_dir = XYZ(vx / axis_len, vy / axis_len, 0.0)
    normal = XYZ(-axis_dir.Y, axis_dir.X, 0.0)

    v = XYZ(dim_mid.X - axis_mid.X, dim_mid.Y - axis_mid.Y, 0.0)
    v_dot = v.X * normal.X + v.Y * normal.Y

    src_offset_abs_ft = abs(src_offset_ft) if src_offset_ft is not None else 0.0

    if abs(v_dot) < 1e-9:
        sign = 1.0
    else:
        sign = 1.0 if v_dot > 0 else -1.0

    target_signed_ft = sign * src_offset_abs_ft
    delta_signed_ft = target_signed_ft - v_dot

    delta_mm = delta_signed_ft * 304.8
    if abs(delta_mm) <= 0.5:
        return

    move_vec = XYZ(normal.X * delta_signed_ft, normal.Y * delta_signed_ft, 0.0)
    ElementTransformUtils.MoveElement(host_doc, host_dim.Id, move_vec)


# ============================================================
# DIMENSION CREATION IN HOST
# ============================================================

def create_host_dimensions(doc, view, dim_metas, wall_map, host_dim_type_by_name, host_dim_type_default, host_group_origin_ft, rotation_rad=0.0, mirror_normal=None):
    if not dim_metas or not wall_map:
        _log("  Dimension creation skipped: no dim metadata or no wall mapping.")
        return

    if view is None:
        _log("  Dimension creation skipped: no view provided.")
        return

    try:
        vt = view.ViewType
        if vt not in (ViewType.FloorPlan, ViewType.EngineeringPlan, ViewType.CeilingPlan):
            _log("  View '{}' is of type {}; dimension creation skipped.".format(_element_name(view), vt))
            return
    except Exception:
        pass

    src_to_host = {}
    for src_id, info in wall_map.items():
        try:
            hid = info.get("host_id")
        except Exception:
            hid = None
        if hid is not None:
            src_to_host[src_id] = hid

    if not src_to_host:
        _log("  Dimension creation skipped: wall map has no host_id entries.")
        return

    created_count = 0
    z_plane = view.Origin.Z

    def element_mid_ft(el):
        if isinstance(el, Wall):
            return get_wall_midpoint_ft(el)
        bbox = el.get_BoundingBox(None)
        if bbox:
            return (bbox.Min + bbox.Max) * 0.5
        return XYZ(0.0, 0.0, z_plane)

    t3 = Transaction(doc, "Create dimensions for loaded groups")
    t3.Start()
    apply_silent_failure_handling(t3)
    try:
        for dm_index, dm in enumerate(dim_metas):
            if dm_index % 5 == 0:
                _pump_ui_messages()

            dim_id = dm["id"]
            src_type_name = dm["type_name"]

            ref_infos = dm.get("ref_infos")
            if not ref_infos:
                ref_infos = [{
                    "src_id": wid,
                    "cat": "Wall",
                    "stable": None,
                    "kind": None,
                    "offset_from_axis_ft": None,
                } for wid in dm.get("wall_ids", [])]

            host_ref_entries = []
            host_ids_for_log = []

            for ri in ref_infos:
                src_eid = ri.get("src_id")
                if src_eid is None:
                    continue

                host_id = src_to_host.get(src_eid)
                if host_id is None:
                    continue

                kind = ri.get("kind")
                off_ft = ri.get("offset_from_axis_ft")

                host_ref = None
                host_el = None
                try:
                    host_el = doc.GetElement(ElementId(host_id))
                except Exception:
                    host_el = None

                if isinstance(host_el, Wall) and kind in ("face_exterior", "face_interior", "face"):
                    shells_to_try = []
                    if kind == "face_exterior":
                        shells_to_try = [ShellLayerType.Exterior]
                    elif kind == "face_interior":
                        shells_to_try = [ShellLayerType.Interior]
                    else:
                        shells_to_try = [ShellLayerType.Exterior, ShellLayerType.Interior]

                    for shell in shells_to_try:
                        try:
                            side_refs = HostObjectUtils.GetSideFaces(host_el, shell)
                        except Exception:
                            side_refs = None
                        if side_refs and side_refs.Count > 0:
                            host_ref = side_refs[0]
                            break

                if host_ref is None and host_el is not None:
                    try:
                        host_ref = Reference(host_el)
                    except Exception:
                        host_ref = None

                if host_ref is None:
                    continue

                host_ids_for_log.append(host_id)
                host_ref_entries.append({
                    "src_id": src_eid,
                    "host_id": host_id,
                    "src_kind": kind,
                    "offset_ft": off_ft,
                    "ref": host_ref,
                })

            if len(host_ref_entries) < 2:
                continue

            perp_src_id = dm.get("perp_wall_id")
            perp_offset_ft = dm.get("perp_offset_from_axis_ft")
            host_perp_el = None
            src_offset_ft = None

            if perp_src_id is not None and perp_offset_ft is not None:
                host_perp_id = src_to_host.get(perp_src_id)
                if host_perp_id is not None:
                    try:
                        host_perp_el = doc.GetElement(ElementId(host_perp_id))
                    except Exception:
                        host_perp_el = None
                src_offset_ft = abs(perp_offset_ft)

            line_rel_p0 = _reflect_xyz(rotate_xy_about_origin_ft(dm.get("line_rel_p0_ft"), rotation_rad), mirror_normal)
            line_rel_p1 = _reflect_xyz(rotate_xy_about_origin_ft(dm.get("line_rel_p1_ft"), rotation_rad), mirror_normal)

            p1 = p2 = None
            if line_rel_p0 is not None and line_rel_p1 is not None:
                try:
                    p1 = XYZ(host_group_origin_ft.X + line_rel_p0.X, host_group_origin_ft.Y + line_rel_p0.Y, z_plane)
                    p2 = XYZ(host_group_origin_ft.X + line_rel_p1.X, host_group_origin_ft.Y + line_rel_p1.Y, z_plane)
                except Exception:
                    p1 = p2 = None

            if p1 is None or p2 is None:
                first_id = host_ids_for_log[0]
                last_id = host_ids_for_log[-1]
                e1 = doc.GetElement(ElementId(first_id))
                e2 = doc.GetElement(ElementId(last_id))
                p1m = element_mid_ft(e1)
                p2m = element_mid_ft(e2)
                p1 = XYZ(p1m.X, p1m.Y, z_plane)
                p2 = XYZ(p2m.X, p2m.Y, z_plane)

            if (abs(p1.X - p2.X) < 1e-6 and abs(p1.Y - p2.Y) < 1e-6 and abs(p1.Z - p2.Z) < 1e-6):
                continue

            host_mid = XYZ(0.5 * (p1.X + p2.X), 0.5 * (p1.Y + p2.Y), 0.5 * (p1.Z + p2.Z))

            main_vec = XYZ(p2.X - p1.X, p2.Y - p1.Y, 0.0)
            len_mv = math.sqrt(main_vec.X * main_vec.X + main_vec.Y * main_vec.Y)
            if len_mv < 1e-6:
                main_dir = XYZ(1.0, 0.0, 0.0)
            else:
                main_dir = XYZ(main_vec.X / len_mv, main_vec.Y / len_mv, 0.0)

            try:
                first_el = doc.GetElement(ElementId(host_ids_for_log[0]))
                last_el = doc.GetElement(ElementId(host_ids_for_log[-1]))
                fm = element_mid_ft(first_el)
                lm = element_mid_ft(last_el)
                span_vec = XYZ(lm.X - fm.X, lm.Y - fm.Y, 0.0)
                span_len = math.sqrt(span_vec.X * span_vec.X + span_vec.Y * span_vec.Y)
            except Exception:
                span_len = 10.0

            half_len_ft = max(span_len * 0.6, 5.0)

            ref_arr = ReferenceArray()
            for e in host_ref_entries:
                ref_arr.Append(e["ref"])

            host_dt = choose_host_dim_type(src_type_name, host_dim_type_by_name, host_dim_type_default)
            if host_dt is None:
                continue

            p_start = XYZ(host_mid.X - main_dir.X * half_len_ft, host_mid.Y - main_dir.Y * half_len_ft, z_plane)
            p_end = XYZ(host_mid.X + main_dir.X * half_len_ft, host_mid.Y + main_dir.Y * half_len_ft, z_plane)
            line = Line.CreateBound(p_start, p_end)

            new_dim = None
            try:
                new_dim = doc.Create.NewDimension(view, line, ref_arr)
            except Exception:
                new_dim = None

            if new_dim and new_dim.IsValidObject:
                try:
                    if new_dim.DimensionType.Id != host_dt.Id:
                        new_dim.DimensionType = host_dt
                except Exception:
                    pass

                if host_perp_el is not None and src_offset_ft is not None and src_offset_ft > 1e-6:
                    try:
                        adjust_dim_offset_to_match_source(
                            doc, new_dim, host_perp_el, src_offset_ft,
                            src_dim_id=dim_id, src_perp_wall_id=perp_src_id
                        )
                    except Exception:
                        pass

                created_count += 1

        commit_status = t3.Commit()
        if commit_status != TransactionStatus.Committed:
            _log("  Dimension creation did not commit (status: {}).".format(commit_status))
        else:
            _log("  Dimension creation finished; created {} Dimensions in host.".format(created_count))
        _pump_ui_messages()
    except Exception as e_tx:
        _log("  Dimension creation transaction failed, rolling back: {}".format(e_tx))
        if t3.HasStarted() and not t3.HasEnded():
            t3.RollBack()


# ============================================================
# ENTRANCE-WALL ALIGNMENT (рядове apartments: align the entrance door's
# host wall to the footprint's side facing a МЗК roof, instead of
# aligning the whole group's own centre to the footprint's centre - see
# "Вибір плями квартири.pushbutton"\\script.py)
# ============================================================

# Family name fragment identifying the apartment's entrance door,
# matched case-insensitively as a substring (e.g. "AVR_Двері_Вхідні").
_ENTRANCE_DOOR_FAMILY_NAME_SUBSTRING = "вхід"


def find_entrance_wall(src_group):
    """Find the apartment's entrance door - a FamilyInstance among the
    group's own (non-nested - matches collect_source_walls) members
    whose family name contains "Вхід" - and return the Wall it's hosted
    on, or None if no such door/host is found."""
    try:
        member_ids = src_group.GetMemberIds()
    except Exception:
        return None

    document = src_group.Document

    for eid in member_ids:
        el = document.GetElement(eid)

        if not isinstance(el, FamilyInstance):
            continue

        try:
            family_name = el.Symbol.Family.Name
        except Exception:
            family_name = ""

        if _ENTRANCE_DOOR_FAMILY_NAME_SUBSTRING not in str(family_name or "").strip().lower():
            continue

        try:
            host = el.Host
        except Exception:
            host = None

        if isinstance(host, Wall):
            return host

    return None


def _normalize_angle_rad(angle_rad):
    while angle_rad > math.pi:
        angle_rad -= 2.0 * math.pi
    while angle_rad <= -math.pi:
        angle_rad += 2.0 * math.pi
    return angle_rad


def _wall_face_point_and_normal_ft(wall, group_centroid_xy):
    """The wall's own outward-facing face point (its centreline midpoint,
    offset by half its own width toward whichever side is OUTSIDE the
    apartment's own rooms) and that same outward unit normal (as an XYZ,
    Z always 0). Returns (None, None) if the wall has no clear direction.

    Which side is "outward" is decided with a genuine Room-based test
    first: Document.GetRoomAtPoint, a few feet above the wall's own base,
    on each of the two candidate face points - whichever one falls
    inside ANY room is the INWARD side, so the OTHER one is outward. A
    Room boundary is Revit's own understanding of where the enclosed
    space actually is, unlike a "which side points away from some
    reference point" heuristic. Falls back to the OLDER such heuristic
    (away from the group's own overall centroid) only when neither or
    both candidate points resolve to a room (no usable room data, or an
    unbounded/improperly-enclosed room that doesn't reliably say either
    way) - that heuristic can flip for an L-shaped or notched apartment,
    where no single centroid point is reliably inside every wall's own
    true interior, which is exactly why the Room test is tried first."""
    mid_ft = get_wall_midpoint_ft(wall)
    dir2d = get_wall_direction2d_ft(wall)

    if dir2d is None:
        return None, None

    normal_x, normal_y = -dir2d.Y, dir2d.X

    try:
        half_width_ft = wall.Width / 2.0
    except Exception:
        half_width_ft = 0.0

    flipped_by_room_test = False

    if half_width_ft > 1e-9:
        try:
            document = wall.Document
            probe_z = mid_ft.Z + 3.0

            room_a = document.GetRoomAtPoint(XYZ(
                mid_ft.X + normal_x * half_width_ft,
                mid_ft.Y + normal_y * half_width_ft,
                probe_z,
            ))
            room_b = document.GetRoomAtPoint(XYZ(
                mid_ft.X - normal_x * half_width_ft,
                mid_ft.Y - normal_y * half_width_ft,
                probe_z,
            ))
        except Exception:
            room_a = room_b = None

        if (room_a is not None) != (room_b is not None):
            flipped_by_room_test = True
            if room_a is not None:
                # Side A (the current normal's own direction) is INSIDE a
                # room - flip to point at side B (outside every room)
                # instead.
                normal_x, normal_y = -normal_x, -normal_y

    if not flipped_by_room_test:
        to_mid_x = mid_ft.X - group_centroid_xy[0]
        to_mid_y = mid_ft.Y - group_centroid_xy[1]

        if normal_x * to_mid_x + normal_y * to_mid_y < 0:
            normal_x, normal_y = -normal_x, -normal_y

    face_point_ft = XYZ(
        mid_ft.X + normal_x * half_width_ft,
        mid_ft.Y + normal_y * half_width_ft,
        mid_ft.Z,
    )

    return face_point_ft, XYZ(normal_x, normal_y, 0.0)


_NEIGHBOR_WALL_THICKNESS_TOLERANCE_FEET = 0.02


def _point_in_polygon_ft(point, polygon):
    """Standard ray-casting point-in-polygon test - same algorithm as
    "Вибір плями квартири.pushbutton\\script.py"'s own _point_in_polygon,
    duplicated here rather than imported since that script isn't a
    normal importable module (a pyRevit command's own script.py)."""
    x, y = point
    inside = False
    count = len(polygon)
    j = count - 1

    for i in range(count):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]

        if (yi > y) != (yj > y):
            x_intersect = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_intersect:
                inside = not inside

        j = i

    return inside


def _apartment_source_bbox_ft(src_group):
    """Combined XY bounding box (min_x, min_y, max_x, max_y), in the
    SOURCE document's own coordinates, across every Wall member of the
    group - the apartment's own overall footprint rectangle, before any
    placement transform. None if the group has no Wall members with
    resolvable geometry."""
    min_x = min_y = max_x = max_y = None
    document = src_group.Document

    for eid in src_group.GetMemberIds():
        el = document.GetElement(eid)
        if not isinstance(el, Wall):
            continue

        bbox = el.get_BoundingBox(None)
        if bbox is None:
            continue

        if min_x is None or bbox.Min.X < min_x:
            min_x = bbox.Min.X
        if min_y is None or bbox.Min.Y < min_y:
            min_y = bbox.Min.Y
        if max_x is None or bbox.Max.X > max_x:
            max_x = bbox.Max.X
        if max_y is None or bbox.Max.Y > max_y:
            max_y = bbox.Max.Y

    if min_x is None:
        return None

    return min_x, min_y, max_x, max_y


_WALL_SAMPLE_SPACING_FT = 1.0


# ============================================================
# РЯДОВЕ + ПРИСУТНЯ (R+AP) ALIGNMENT
#
# Відсутня footprints normally have exactly one МЗК-facing side.
# Присутня ones typically have 2-3 (entrance_alignment["mzkCandidates"],
# from "Вибір плями квартири.pushbutton"\\script.py's own
# _mzk_touching_sides_ft), so picking which one the entrance door goes
# on - and whether the apartment needs mirroring - needs the extra
# solve below instead of just taking the first one. See
# _solve_r_ap_alignment_ft's own docstring for the full rule.
# ============================================================

# How far off from parallel (degrees) an apartment wall may be from the
# window side's own line and still count as a candidate for
# _r_ap_apartment_depth_extremes_ft's own depth measurement.
_R_AP_WALL_PARALLEL_TOLERANCE_DEGREES = 15.0


def _apartment_type_codes_from_filename(rvt_path):
    """Window-placement (R/C/L/N) and added-part (AP/NP) codes parsed
    directly from the apartment file's own filename (2nd and 3rd
    underscore-separated fields - e.g. "1_R_AP_00_6270.9360_42_1.rvt")
    - the same naming convention "Select Apartment.pushbutton"'s own
    apartment_scan.py parses for the Банк квартир UI's own filters,
    duplicated here rather than imported (that module lives in a
    DIFFERENT pushbutton's own lib folder, not guaranteed to be on
    sys.path for every engine this function might run under) since it's
    only two split() lookups.

    Lets the R+AP debug-line visual (see place_apartment_group's own
    comment) recognise the PLACED APARTMENT's own type from the file
    itself, regardless of whether entrance_alignment/footprint data is
    even available (Банк квартир opened directly, with no footprint
    chosen, still places a real apartment file whose own type is known).

    Returns ("", "") if the filename doesn't have enough underscore-
    separated fields to read from."""
    try:
        stem = os.path.splitext(os.path.basename(str(rvt_path or "")))[0]
    except Exception:
        return "", ""

    parts = stem.split("_")
    window_placement_code = parts[1].strip().upper() if len(parts) > 1 else ""
    added_part_code = parts[2].strip().upper() if len(parts) > 2 else ""
    return window_placement_code, added_part_code


def _r_ap_reference_window_wall_ft(src_group, group_centroid_xy):
    """The apartment's own single "window side" reference wall - a real
    line/position to measure depths from, not just a direction (unlike
    _dominant_window_direction_ft's own result). Among the window-
    hosting walls contributing to that same function's winning
    direction bucket (same 15-degree merge), returns the one whose own
    outward face sits FARTHEST from the group's own centroid along that
    bucket's own direction - the true outermost/frontmost window wall.

    Returns (face_point_ft, normal_XYZ) - same shape as
    _wall_face_point_and_normal_ft's own return - or None if no window
    has a resolvable host-wall direction."""
    try:
        member_ids = src_group.GetMemberIds()
    except Exception:
        return None

    document = src_group.Document
    wall_normal_cache = {}
    window_entries = []

    for eid in member_ids:
        el = document.GetElement(eid)

        if not isinstance(el, FamilyInstance):
            continue

        try:
            category = el.Category
            is_window = (
                category is not None
                and category.Id.IntegerValue == int(BuiltInCategory.OST_Windows)
            )
        except Exception:
            is_window = False

        if not is_window:
            continue

        try:
            host = el.Host
        except Exception:
            host = None

        if not isinstance(host, Wall):
            continue

        host_id = host.Id.IntegerValue

        if host_id not in wall_normal_cache:
            wall_normal_cache[host_id] = _wall_face_point_and_normal_ft(host, group_centroid_xy)

        _face_point, normal = wall_normal_cache[host_id]
        if normal is not None:
            window_entries.append((host, normal))

    if not window_entries:
        return None

    buckets = []  # each: [sum_x, sum_y, count, [(wall, normal), ...]]
    merge_threshold_rad = math.radians(15.0)

    for wall, normal in window_entries:
        angle = math.atan2(normal.Y, normal.X)
        placed = False

        for bucket in buckets:
            bucket_angle = math.atan2(bucket[1], bucket[0])
            diff = abs(_normalize_angle_rad(angle - bucket_angle))

            if diff < merge_threshold_rad:
                bucket[0] += normal.X
                bucket[1] += normal.Y
                bucket[2] += 1
                bucket[3].append((wall, normal))
                placed = True
                break

        if not placed:
            buckets.append([normal.X, normal.Y, 1, [(wall, normal)]])

    buckets.sort(key=lambda bucket: bucket[2], reverse=True)
    best_x, best_y, _count, members = buckets[0]
    best_length = math.hypot(best_x, best_y)

    if best_length < 1e-9:
        return None

    dir_x, dir_y = best_x / best_length, best_y / best_length

    best_wall = None
    best_projection = None

    for wall, _normal in members:
        face_point, _n = _wall_face_point_and_normal_ft(wall, group_centroid_xy)
        if face_point is None:
            continue

        projection = (
            (face_point.X - group_centroid_xy[0]) * dir_x
            + (face_point.Y - group_centroid_xy[1]) * dir_y
        )

        if best_projection is None or projection > best_projection:
            best_projection = projection
            best_wall = wall

    if best_wall is None:
        return None

    return _wall_face_point_and_normal_ft(best_wall, group_centroid_xy)


def _r_ap_apartment_depth_extremes_ft(src_group, door_width_ft, window_face_point_ft, window_normal):
    """For R+AP's mirror-matching: find every Wall member of src_group
    that's roughly parallel to the window side's own line
    (window_normal's own perpendicular direction) and has the SAME
    thickness as the entrance door's own host wall (door_width_ft) - the
    door wall itself isn't excluded; it can be one of these if it
    happens to qualify (a common, valid case, not an error). For each
    qualifying wall, measure its own perpendicular distance from the
    window side's own line, and its own position ALONG the window
    side's own direction (from window_face_point_ft).

    Returns (near, far) - each a dict {"distance_ft", "position_ft",
    "start_ft", "end_ft"} - the shallowest/deepest measurement found
    (identical if only one wall qualifies), or (None, None) if none
    qualify. start_ft/end_ft are the qualifying wall's own CENTRE and
    the perpendicular foot of that centre on the window side's own
    line - a genuine perpendicular measuring line from wall to window,
    same drawing convention "Вибір плями квартири.pushbutton"\\script.py
    uses for the matching footprint-side measurement
    (_r_ap_outside_ray_depth_extremes_ft) - not the wall's own extent."""
    window_dir_x, window_dir_y = -window_normal.Y, window_normal.X
    window_angle = math.atan2(window_dir_y, window_dir_x)
    parallel_threshold_rad = math.radians(_R_AP_WALL_PARALLEL_TOLERANCE_DEGREES)
    document = src_group.Document

    measurements = []

    for eid in src_group.GetMemberIds():
        el = document.GetElement(eid)

        if not isinstance(el, Wall):
            continue

        try:
            wall_width_ft = el.Width
        except Exception:
            continue

        if abs(wall_width_ft - door_width_ft) > _NEIGHBOR_WALL_THICKNESS_TOLERANCE_FEET:
            continue

        wall_dir = get_wall_direction2d_ft(el)
        if wall_dir is None:
            continue

        wall_angle = math.atan2(wall_dir.Y, wall_dir.X)
        angle_diff = abs(_normalize_angle_rad(wall_angle - window_angle))
        angle_diff = min(angle_diff, abs(math.pi - angle_diff))

        if angle_diff > parallel_threshold_rad:
            continue

        wall_mid = get_wall_midpoint_ft(el)
        rel_x = wall_mid.X - window_face_point_ft.X
        rel_y = wall_mid.Y - window_face_point_ft.Y

        distance_ft = abs(rel_x * window_normal.X + rel_y * window_normal.Y)

        if distance_ft < 1e-3:
            # This IS (or is right on) the window's own host wall.
            continue

        position_ft = rel_x * window_dir_x + rel_y * window_dir_y

        foot_x = window_face_point_ft.X + window_dir_x * position_ft
        foot_y = window_face_point_ft.Y + window_dir_y * position_ft

        measurements.append({
            "distance_ft": distance_ft,
            "position_ft": position_ft,
            # The wall's own centre, and the perpendicular foot of that
            # centre on the window side's own line - for drawing a
            # genuine perpendicular measuring line (see
            # _draw_r_ap_depth_debug_lines); unused by the
            # mirror-matching maths itself.
            "start_ft": wall_mid,
            "end_ft": XYZ(foot_x, foot_y, wall_mid.Z),
        })

    if not measurements:
        return None, None

    near = min(measurements, key=lambda m: m["distance_ft"])
    far = max(measurements, key=lambda m: m["distance_ft"])
    return near, far


# Brown = shorter distance, purple = longer distance (near/far are the
# same thing - _r_ap_apartment_depth_extremes_ft and
# _outside_depth_extremes_ft both return "near" as the SHORTER-distance
# measurement and "far" as the LONGER-distance one). Dark shades for the
# apartment side, light shades for the footprint side - see
# _draw_r_ap_depth_debug_lines. Both drawn together, only once an
# apartment is actually placed (per the user's own correction: these
# should never appear before that, unlike the red/exterior and
# green/МЗК lines "Вибір плями квартири.pushbutton"\\script.py still
# draws at pick time).
_R_AP_DEBUG_APARTMENT_NEAR_COLOR = Color(102, 51, 0)      # shorter - dark brown
_R_AP_DEBUG_APARTMENT_FAR_COLOR = Color(85, 0, 127)       # longer - dark purple
_R_AP_DEBUG_FOOTPRINT_NEAR_COLOR = Color(222, 184, 135)   # shorter - light brown
_R_AP_DEBUG_FOOTPRINT_FAR_COLOR = Color(216, 191, 240)    # longer - light purple
_R_AP_DEBUG_LINE_WEIGHT = 6


def _draw_r_ap_depth_debug_lines(document, view, apt_near, apt_far, entrance_alignment, target_z_ft):
    """Draw the R+AP depth-measurement debug lines - BOTH sides
    together, only once an apartment is actually placed (never at
    footprint-pick time, unlike the red/exterior and green/МЗК lines):

    - The two apartment-side measurements _r_ap_apartment_depth_
      extremes_ft found (run on the PLACED, host-document group) - each
      a genuine perpendicular line from a qualifying wall's own centre
      to the window side's own line, not the wall's own extent - as
      dark brown (shorter distance) and dark purple (longer distance)
      DetailLines.
    - The matching two footprint-side segments "Вибір плями квартири.
      pushbutton"\\script.py's own _outside_depth_extremes_ft found (its
      own endpoints carried through entrance_alignment - see
      apartment_bank_window.py's own forwarding of outsideDepthNear/Far
      StartX/YFt/EndX/YFt) as light brown (shorter distance) and light
      purple (longer distance) DetailLines, at target_z_ft.

    Appended to the SAME tracked debug-line set "Вибір плями квартири"
    uses for its own red/green lines (dkui_bridge.append_debug_side_line_ids),
    so the next footprint pick's cleanup removes these too.

    Best-effort only: any failure here is logged and swallowed, never
    raised - a debug visual aid is not worth breaking the actual
    placement over."""
    if view is None or dkui_bridge is None:
        return

    # None when there's no footprint at all (Банк квартир opened
    # directly, no entrance_alignment) - the footprint-side half of the
    # visual then simply has nothing to draw, but the apartment-side
    # half (apt_near/apt_far) still can.
    entrance_alignment = entrance_alignment or {}

    new_ids = []

    def _footprint_line_ft(start_key, end_key):
        start_x = entrance_alignment.get(start_key + "XFt")
        start_y = entrance_alignment.get(start_key + "YFt")
        end_x = entrance_alignment.get(end_key + "XFt")
        end_y = entrance_alignment.get(end_key + "YFt")

        if start_x is None or start_y is None or end_x is None or end_y is None:
            return None

        return XYZ(start_x, start_y, target_z_ft), XYZ(end_x, end_y, target_z_ft)

    footprint_near_line = _footprint_line_ft("outsideDepthNearStart", "outsideDepthNearEnd")
    footprint_far_line = _footprint_line_ft("outsideDepthFarStart", "outsideDepthFarEnd")

    lines_to_draw = []

    for measurement, color in (
        (apt_near, _R_AP_DEBUG_APARTMENT_NEAR_COLOR),
        (apt_far, _R_AP_DEBUG_APARTMENT_FAR_COLOR),
    ):
        if measurement is None:
            continue

        start_ft = measurement.get("start_ft")
        end_ft = measurement.get("end_ft")

        if start_ft is not None and end_ft is not None:
            lines_to_draw.append((start_ft, end_ft, color))

    for footprint_line, color in (
        (footprint_near_line, _R_AP_DEBUG_FOOTPRINT_NEAR_COLOR),
        (footprint_far_line, _R_AP_DEBUG_FOOTPRINT_FAR_COLOR),
    ):
        if footprint_line is not None:
            lines_to_draw.append((footprint_line[0], footprint_line[1], color))

    if not lines_to_draw:
        return

    try:
        t = Transaction(document, "DKUI: R+AP depth debug lines")
        t.Start()

        for start_ft, end_ft, color in lines_to_draw:
            try:
                line = Line.CreateBound(start_ft, end_ft)
                detail_curve = document.Create.NewDetailCurve(view, line)

                override = OverrideGraphicSettings()
                override.SetProjectionLineColor(color)
                override.SetProjectionLineWeight(_R_AP_DEBUG_LINE_WEIGHT)
                view.SetElementOverrides(detail_curve.Id, override)

                new_ids.append(detail_curve.Id.IntegerValue)
            except Exception as error:
                _log("[PLACEMENT WARN] R+AP: could not draw one depth debug line: {}".format(error))

        t.Commit()
    except Exception as error:
        _log("[PLACEMENT WARN] R+AP: could not draw depth debug lines: {}".format(error))
        return

    if new_ids and dkui_bridge is not None:
        try:
            dkui_bridge.append_debug_side_line_ids(new_ids)
        except Exception:
            pass


# ============================================================
# КУТОВЕ (C+NP and C+AP) ALIGNMENT
#
# Used for BOTH Кутове + Відсутня and Кутове + Присутня apartments -
# added_part_code (NP/AP) makes no difference to this solve at all,
# unlike Рядове's own split (R+NP uses door-alone alignment, R+AP uses
# window-driven rotation instead because Присутня footprints usually
# have more than one МЗК-facing side): a Кутове footprint has exactly 2
# ADJACENT outside sides (one longer, one shorter, meeting at the
# footprint's own exterior corner) regardless of added-part code, and a
# Кутове apartment has windows on 2 adjacent bounding-box sides the same
# way, so the window-driven rotation this solve already uses works
# identically for both. Match longer to longer, corner to corner,
# mirror/rotate-180 only if that barely lands inside the footprint, then
# refine translation onto the entrance door - see
# _solve_c_np_alignment_ft's own docstring for the full rule.
# ============================================================


def _line_intersection_2d_ft(point_a, dir_a, point_b, dir_b):
    """Where the infinite 2D line through point_a (direction dir_a)
    crosses the infinite 2D line through point_b (direction dir_b), as
    an (x, y) tuple - or None if they're parallel (or nearly so)."""
    denom = dir_a[0] * dir_b[1] - dir_a[1] * dir_b[0]
    if abs(denom) < 1e-9:
        return None
    dx = point_b[0] - point_a[0]
    dy = point_b[1] - point_a[1]
    t = (dx * dir_b[1] - dy * dir_b[0]) / denom
    return (point_a[0] + dir_a[0] * t, point_a[1] + dir_a[1] * t)


def _c_np_window_bearing_sides_ft(src_group):
    """For a Кутове ("corner") + Відсутня apartment: which of its own
    axis-aligned bounding box's 4 sides (in the SOURCE document's own
    local frame - these prefab apartment groups are modelled with their
    walls running along their own local X/Y, so the bounding box's own
    sides ARE real wall runs, not just an abstract box) have at least
    one window on them, using each window's own host wall - the same
    "which of 4 cardinal directions" idea as _classify_window_placement's
    own bucketing in "Вибір плями квартири.pushbutton"\\script.py, but
    without that function's own "dominant orientation" rotation step
    (unlike an arbitrarily-oriented footprint, a source apartment
    group's own local frame IS already axis-aligned with its own walls).

    Returns a dict {side_key: {"lengthFt": ..., "pointXFt": ...,
    "pointYFt": ..., "dirXFt": ..., "dirYFt": ...}} for whichever of
    "north", "south", "east", "west" have at least one window -
    "north"/"south" both share the bounding box's own WIDTH
    (max_x-min_x), "east"/"west" both share its own HEIGHT
    (max_y-min_y); point/dir describe that window-bearing wall's own
    centerline (its real line, not the bounding box's edge - a wall
    that's recessed slightly from the overall bounding box still gets
    used by its own true position this way). Empty if no window-hosting
    wall or no resolvable bounding box."""
    bbox = _apartment_source_bbox_ft(src_group)
    if bbox is None:
        return {}

    min_x, min_y, max_x, max_y = bbox
    width_ft = max_x - min_x
    height_ft = max_y - min_y
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0

    document = src_group.Document
    sides = {}

    for eid in src_group.GetMemberIds():
        el = document.GetElement(eid)

        if not isinstance(el, FamilyInstance):
            continue

        try:
            category = el.Category
            is_window = (
                category is not None
                and category.Id.IntegerValue == int(BuiltInCategory.OST_Windows)
            )
        except Exception:
            is_window = False

        if not is_window:
            continue

        try:
            host = el.Host
        except Exception:
            host = None

        if not isinstance(host, Wall):
            continue

        wall_dir = get_wall_direction2d_ft(host)
        if wall_dir is None:
            continue

        normal_x, normal_y = -wall_dir.Y, wall_dir.X

        wall_mid = get_wall_midpoint_ft(host)
        to_mid_x = wall_mid.X - center_x
        to_mid_y = wall_mid.Y - center_y

        if normal_x * to_mid_x + normal_y * to_mid_y < 0:
            normal_x, normal_y = -normal_x, -normal_y

        if abs(normal_x) >= abs(normal_y):
            side_key = "east" if normal_x > 0 else "west"
        else:
            side_key = "north" if normal_y > 0 else "south"

        if side_key not in sides:
            sides[side_key] = {
                "lengthFt": width_ft if side_key in ("north", "south") else height_ft,
                "pointXFt": wall_mid.X,
                "pointYFt": wall_mid.Y,
                "dirXFt": wall_dir.X,
                "dirYFt": wall_dir.Y,
            }

    return sides


def _thickest_wall_door_ft(src_group):
    """Fallback entrance-door search for an apartment with no "вхід"-
    named door family at all (see find_entrance_wall) - some apartments
    (seen on Кутове + Присутня ones) instead name their corridor-facing
    doors after the МЗК side itself, e.g. "AVR_Дв_1П_МЗК_Сталь1", with
    no single one distinguishable as THE entrance by name. Among every
    Doors-category FamilyInstance that's a direct member of the group
    and hosted on a Wall, this returns whichever one's host wall has the
    single BIGGEST thickness (Wall.Width) - on the idea that the real
    front door sits in the thickest wall when there's no naming to go
    by.

    Returns that Wall, or None if there are no such doors at all, or
    more than one host wall ties for the biggest thickness (within
    _NEIGHBOR_WALL_THICKNESS_TOLERANCE_FEET) - ambiguous, so the caller
    skips door alignment entirely rather than guessing which one is
    right."""
    try:
        member_ids = src_group.GetMemberIds()
    except Exception:
        return None

    document = src_group.Document
    candidates = []

    for eid in member_ids:
        el = document.GetElement(eid)

        if not isinstance(el, FamilyInstance):
            continue

        try:
            category = el.Category
            is_door = (
                category is not None
                and category.Id.IntegerValue == int(BuiltInCategory.OST_Doors)
            )
        except Exception:
            is_door = False

        if not is_door:
            continue

        try:
            host = el.Host
        except Exception:
            host = None

        if not isinstance(host, Wall):
            continue

        try:
            width_ft = host.Width
        except Exception:
            continue

        candidates.append((width_ft, host))

    if not candidates:
        return None

    max_width_ft = max(width_ft for width_ft, _wall in candidates)
    tied_walls = [
        wall for width_ft, wall in candidates
        if abs(width_ft - max_width_ft) <= _NEIGHBOR_WALL_THICKNESS_TOLERANCE_FEET
    ]

    if len(tied_walls) != 1:
        return None

    return tied_walls[0]


# ============================================================
# НАСКРІЗНЕ (L) + ПРИСУТНЯ (AP) ALIGNMENT
#
# The apartment has windows on two opposite sides, the footprint has two
# opposite outside sides. The apartment's LONGER window side goes onto the
# footprint's LONGER outside side, centre to centre (rotation: its outward
# normal onto the side's outward normal - so the apartment lies inside the
# footprint). The main door has to be on the МЗК side; if the placed apartment
# has it the other way, the apartment is mirrored about the plane through the
# middle of that outside side, perpendicular to it.
# ============================================================


def _group_doors_sorted(group):
    """The Doors-category family instances of `group` that are hosted on a Wall,
    in the order of their Ids. A pasted copy of the group keeps that order, so
    "the k-th door" means the same door in the apartment file and in the
    project."""
    document = group.Document
    doors = []
    try:
        member_ids = group.GetMemberIds()
    except Exception:
        return doors
    for eid in member_ids:
        element = document.GetElement(eid)
        if not isinstance(element, FamilyInstance):
            continue
        try:
            if element.Category is None or element.Category.Id.IntegerValue != int(BuiltInCategory.OST_Doors):
                continue
            if not isinstance(element.Host, Wall):
                continue
        except Exception:
            continue
        doors.append(element)
    doors.sort(key=lambda door: door.Id.IntegerValue)
    return doors


# How far past the wall's face, and how high above the door's base, the points
# are taken that tell which room (if any) lies on each side of a door.
_DOOR_SIDE_PROBE_FT = 0.8
_DOOR_SIDE_PROBE_HEIGHT_FT = 3.0


def _rooms_on_both_sides_of_door(door):
    """(room on one side of the door's wall, room on the other side) - either
    can be None (no room there: the corridor / outside)."""
    document = door.Document
    host = door.Host
    point = door.Location.Point
    direction = get_wall_direction2d_ft(host)
    if direction is None:
        raise ValueError("wall without a direction")
    normal_x, normal_y = -direction.Y, direction.X
    reach = host.Width / 2.0 + _DOOR_SIDE_PROBE_FT
    z = point.Z + _DOOR_SIDE_PROBE_HEIGHT_FT
    first = document.GetRoomAtPoint(XYZ(point.X + normal_x * reach, point.Y + normal_y * reach, z))
    second = document.GetRoomAtPoint(XYZ(point.X - normal_x * reach, point.Y - normal_y * reach, z))
    return first, second


_ENTRANCE_ROOM_WORDS = (u"передпок", u"прихож", u"хол", u"коридор", u"hall")


def _entrance_door_by_rooms_ft(group):
    """The entrance door of an apartment whose doors have no "Вхід" in their
    family names: the door that has a room of THIS apartment on one side only
    (the other side is the corridor/outside) - an inner door has a room on both
    sides. If several doors do (e.g. an outside door), the one in the thickest
    wall, then the one that opens into the hall.

    Returns (index of the door among _group_doors_sorted(group), number of such
    doors in the group) or None when there is no such door or it is ambiguous."""
    doors = _group_doors_sorted(group)
    one_sided = []
    for index, door in enumerate(doors):
        try:
            first, second = _rooms_on_both_sides_of_door(door)
        except Exception:
            continue
        if (first is None) != (second is None):
            one_sided.append((index, door, first or second))

    if not one_sided:
        return None

    if len(one_sided) > 1:
        widest = max(door.Host.Width for _index, door, _room in one_sided)
        one_sided = [
            item for item in one_sided
            if abs(item[1].Host.Width - widest) <= _NEIGHBOR_WALL_THICKNESS_TOLERANCE_FEET
        ]
    if len(one_sided) > 1:
        halls = []
        for item in one_sided:
            try:
                name = (item[2].get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u"").lower()
            except Exception:
                name = u""
            if any(word in name for word in _ENTRANCE_ROOM_WORDS):
                halls.append(item)
        one_sided = halls
    if len(one_sided) != 1:
        return None

    return one_sided[0][0], len(doors)


def _entrance_door_wall_by_index_ft(group, index, expected_count):
    """The host wall of the index-th door of `group` (see _group_doors_sorted),
    or None if the group does not have `expected_count` doors."""
    doors = _group_doors_sorted(group)
    if index is None or len(doors) != expected_count or index >= len(doors):
        return None
    return doors[index].Host


# ============================================================
# Кутове + Присутня: the apartment's two bounding-box sides WITHOUT windows
# go onto the footprint's two INSIDE sides (the ones shared with other
# apartments), wall centre line on the side line; the windows then end up on
# the footprint's outside sides by themselves. Of the two ways to do that (one
# is a pure rotation, the other needs a mirror) the one that brings the entrance
# door closer to a МЗК side is used - the door doesn't have to be ON the МЗК
# side, only to have access to it.
# ============================================================


# ============================================================
# Alternative door walls: in every apartment the door wall encloses a room
# named "Коридор"; the walls of the same thickness that also border that room
# are probable door walls too. Used when the entrance door's own wall cannot
# keep all the placement rules.
# ============================================================


def _entrance_doors(src_group):
    """Every entrance door of the apartment: the family instances whose family name contains "Вхід"."""
    doors = []
    try:
        member_ids = src_group.GetMemberIds()
    except Exception:
        return doors
    document = src_group.Document
    for member_id in member_ids:
        element = document.GetElement(member_id)
        if not isinstance(element, FamilyInstance):
            continue
        try:
            family_name = element.Symbol.Family.Name
        except Exception:
            family_name = ""
        if _ENTRANCE_DOOR_FAMILY_NAME_SUBSTRING in str(family_name or "").strip().lower():
            doors.append(element)
    return doors


# ============================================================
# Room types ("AVR_Тип Приміщення", from the apartment's JSON): type-1 rooms
# (bedrooms) must not be next to a "сходово-ліфтовий вузол" roof, and should
# lie next to type-1 rooms of the neighbouring apartments, not type-2 ones.
# Only an apartment WITHOUT an added part (Відсутня, "NP") may be mirrored to
# get that; for Присутня nothing is changed. The rooms of every placed
# apartment are remembered (placed_apartments) for the neighbours placed next.
# ============================================================

_ROOM_NEAR_FT = 0.8 / FT_TO_M              # rooms this close count as next to each other
# A room this close to a stair/lift node roof counts as next to it (the room is
# set back from the footprint edge by walls, niches, balconies...).
_STAIR_NEAR_FT = 2.0 / FT_TO_M

# For the caller (apartment_bank_window) to remember after a placement: the
# rooms of the apartment as just placed, [{"t": type, "p": [[x, y], ...]}] feet.
last_placed_rooms = {"rooms": [], "z": None, "flip": None}


def _is_stair_core_type_name(name):
    name = (name or u"").lower()
    return u"сходов" in name or u"ліфт" in name or u"лифт" in name


def _stair_roof_polygons(document, z, source_cache):
    """Footprints (feet) of the "сходово-ліфтовий вузол" roofs on the floor at
    height z. Read once per batch."""
    def _compute():
        import apartment_groups

        picker = apartment_groups._picker()
        picker.doc = document
        found = []
        for roof in FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_Roofs).WhereElementIsNotElementType():
            try:
                type_name = picker._element_name(document.GetElement(roof.GetTypeId()))
                if not _is_stair_core_type_name(type_name):
                    continue
                polygon, roof_z = picker._bottom_footprint(roof)
            except Exception:
                continue
            if polygon and len(polygon) >= 3:
                found.append((polygon, roof_z))
        return found

    roofs = _memo(source_cache, ("stair_roof_polygons",), _compute)
    return [polygon for polygon, roof_z in roofs if roof_z is None or abs(roof_z - z) <= 3.0]


def _apply_room_type_rules(
    document, src_group, rvt_path, angle_rad, reference, target, mirror_check_side, needs_mirror,
    footprint_polygon_ft, target_z_ft, may_mirror, outside_dir, source_cache,
):
    """Room-type rules for a footprint WITHOUT an added part (`may_mirror`):
    when a type-1 room of the apartment, as placed, is next to a stair/lift node
    roof (or next to a type-2 room of a neighbour), the apartment is mirrored
    about the plane through its own centre perpendicular to the footprint's
    outside side (`outside_dir`, the side's direction) - windows stay on the
    outside side, the rooms swap ends along it - if that gets the type-1 rooms
    away from the node (and next to the neighbours' type-1 rooms).

    Remembers the rooms as finally placed in last_placed_rooms. Returns the
    mirror plane (nx, ny, ox, oy) to apply after the normal placement, or None.

    The decision is the backend's (core/room_rules.decide_mirror); this side
    only reads what Revit knows (the group's walls' extent, the stair nodes,
    the rooms already placed) and keeps the answer."""
    last_placed_rooms["rooms"] = []
    last_placed_rooms["z"] = target_z_ft
    last_placed_rooms["flip"] = None

    args = (
        document, src_group, rvt_path, angle_rad, reference, target, mirror_check_side, needs_mirror,
        footprint_polygon_ft, target_z_ft, may_mirror, outside_dir, source_cache,
    )
    return (_apply_room_type_rules_remote(*args))


def _apply_room_type_rules_remote(
    document, src_group, rvt_path, angle_rad, reference, target, mirror_check_side, needs_mirror,
    footprint_polygon_ft, target_z_ft, may_mirror, outside_dir, source_cache,
):
    import placed_apartments

    bbox = _apartment_source_bbox_ft(src_group)
    if bbox is None:
        return None

    stair_polygons, neighbour_rooms = [], []
    if may_mirror and outside_dir is not None:
        stair_polygons = _stair_roof_polygons(document, target_z_ft, source_cache)
        try:
            neighbour_rooms = placed_apartments.live_rooms(document, target_z_ft)
        except Exception:
            neighbour_rooms = []

    side = None
    if needs_mirror and mirror_check_side:
        side = dict((key, mirror_check_side[key]) for key in ("normalXFt", "normalYFt", "pointXFt", "pointYFt"))

    answer = backend_api.room_rules({
        "rvtPath": rvt_path,
        "sourceBboxFt": list(bbox),
        "angleRad": angle_rad,
        "reference": [reference.X, reference.Y],
        "target": [target.X, target.Y],
        "mirrorCheckSide": side,
        "needsMirror": bool(needs_mirror),
        "mayMirror": bool(may_mirror),
        "outsideDir": [outside_dir[0], outside_dir[1]] if outside_dir is not None else None,
        "stairPolygons": [[list(point) for point in polygon] for polygon in stair_polygons],
        "neighbourRooms": [
            {"t": room_type, "p": [list(point) for point in polygon]} for room_type, polygon in neighbour_rooms
        ],
    })

    last_placed_rooms["rooms"] = answer["rooms"]
    last_placed_rooms["flip"] = answer.get("flip")
    plane = answer.get("mirrorPlane")
    if plane:
        _log(
            "Room types: mirrored about the apartment's centre, perpendicular to the outside "
            "side (type-1 rooms: cost {:.0f} -> {:.0f}).".format(answer["currentCost"], answer["mirroredCost"])
        )
        return tuple(plane)
    if answer.get("currentCost") is not None:
        _log(
            "Room types: no mirror (cost {:.0f}, mirrored would be {:.0f}).".format(
                answer["currentCost"], answer["mirroredCost"]
            )
        )
    return None


def rebalance_neighbours(document, new_group_id):
    """After an apartment is placed: a neighbour placed EARLIER that may still be
    mirrored (Рядове, no added part) is mirrored if that brings its type-1 rooms
    next to the new apartment's type-1 rooms (and away from type-2 ones).
    Returns how many neighbours were mirrored."""
    import placed_apartments

    entries = [e for e in placed_apartments.live_entries(document) if e.get("rooms")]
    new = [e for e in entries if e["groupId"] == new_group_id]
    if not new or new[0].get("z") is None:
        return 0
    z = new[0]["z"]
    stair_polygons = _stair_roof_polygons(document, z, None)

    # Which neighbours to mirror is the backend's decision (core/room_rules.
    # rebalance_neighbours); mirroring the Revit groups is ours.
    decisions = (backend_api.rebalance_neighbours(
            [
                dict((key, entry.get(key)) for key in ("groupId", "groupUid", "z", "rooms", "flip"))
                for entry in entries
            ],
            new_group_id, stair_polygons))

    mirrored = 0
    for decision in decisions:
        nx, ny, ox, oy = decision["plane"]
        before, after = decision["before"], decision["after"]
        try:
            transaction = Transaction(document, "Mirror neighbour apartment")
            transaction.Start()
            apply_silent_failure_handling(transaction)
            try:
                ElementTransformUtils.MirrorElements(
                    document, List[ElementId]([ElementId(int(decision["groupId"]))]),
                    Plane.CreateByNormalAndOrigin(XYZ(nx, ny, 0.0), XYZ(ox, oy, decision["z"])), False,
                )
                transaction.Commit()
            except Exception:
                if transaction.HasStarted() and not transaction.HasEnded():
                    transaction.RollBack()
                raise
            placed_apartments.update_rooms(
                document, decision["groupUid"],
                [{"t": room["t"], "p": [list(point) for point in room["p"]]} for room in decision["rooms"]],
            )
            mirrored += 1
            _log("Room types: neighbour apartment {} mirrored (type-1 rooms: cost {:.0f} -> {:.0f}).".format(
                decision["groupId"], before, after))
        except Exception as error:
            _log("[PLACEMENT WARN] Neighbour apartment could not be mirrored: {}".format(error))
    return mirrored


# ============================================================
# ENTRY POINT
# ============================================================

def _memo(source_cache, key, compute):
    """Per-batch memo: value of compute() cached under key in
    source_cache["memo"]; just compute() when there is no cache (single
    placement)."""
    if source_cache is None:
        return compute()

    memo = source_cache["memo"]
    if key not in memo:
        memo[key] = compute()
    return memo[key]


def _release_top_levels(src_doc, src_group):
    """Make the apartment's members independent of a level ABOVE them, in the
    apartment file as it is held in memory (it is only ever read and closed
    without saving), so that pasting it high up in a project that has no level
    above the target ("Can't paste these elements at this height because there
    is no Level above to Reference") works:

    - walls: Top Constraint -> Unconnected, Unconnected Height = the wall's
      current height (the walls keep their size);
    - rooms: Upper Limit -> the room's own level, Limit Offset = the room's
      current height;
    - structural columns: Top Level -> their own base level, Top Offset = their
      current height above it.

    Returns (walls, rooms, columns) changed. A member that cannot be changed is
    left as it is (and logged)."""
    from Autodesk.Revit.DB.Architecture import Room

    walls = rooms = columns = failed = 0
    column_categories = (int(BuiltInCategory.OST_StructuralColumns), int(BuiltInCategory.OST_Columns))

    transaction = Transaction(src_doc, "Release top levels")
    transaction.Start()
    apply_silent_failure_handling(transaction)
    try:
        for member_id in src_group.GetMemberIds():
            element = src_doc.GetElement(member_id)
            try:
                if isinstance(element, Wall):
                    top = element.get_Parameter(BuiltInParameter.WALL_HEIGHT_TYPE)
                    if top is None or top.IsReadOnly or top.AsElementId() == ElementId.InvalidElementId:
                        continue
                    box = element.get_BoundingBox(None)
                    height = (box.Max.Z - box.Min.Z) if box is not None else None
                    top.Set(ElementId.InvalidElementId)
                    unconnected = element.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)
                    if height is not None and unconnected is not None and not unconnected.IsReadOnly:
                        unconnected.Set(height)
                    walls += 1

                elif isinstance(element, Room):
                    upper = element.get_Parameter(BuiltInParameter.ROOM_UPPER_LEVEL)
                    offset = element.get_Parameter(BuiltInParameter.ROOM_UPPER_OFFSET)
                    own_level = src_doc.GetElement(element.LevelId)
                    upper_level = src_doc.GetElement(upper.AsElementId()) if upper is not None else None
                    if (
                        upper is None or offset is None or upper.IsReadOnly or offset.IsReadOnly
                        or own_level is None or upper_level is None or upper_level.Id == own_level.Id
                    ):
                        continue
                    height = upper_level.Elevation + offset.AsDouble() - own_level.Elevation
                    upper.Set(own_level.Id)
                    offset.Set(height)
                    rooms += 1

                elif (
                    isinstance(element, FamilyInstance) and element.Category is not None
                    and element.Category.Id.IntegerValue in column_categories
                ):
                    top_level = element.get_Parameter(BuiltInParameter.FAMILY_TOP_LEVEL_PARAM)
                    top_offset = element.get_Parameter(BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM)
                    base_level = element.get_Parameter(BuiltInParameter.FAMILY_BASE_LEVEL_PARAM)
                    if (
                        top_level is None or top_offset is None or base_level is None
                        or top_level.IsReadOnly or top_offset.IsReadOnly
                        or top_level.AsElementId() == base_level.AsElementId()
                    ):
                        continue
                    upper_level = src_doc.GetElement(top_level.AsElementId())
                    own_level = src_doc.GetElement(base_level.AsElementId())
                    if upper_level is None or own_level is None:
                        continue
                    height = upper_level.Elevation + top_offset.AsDouble() - own_level.Elevation
                    top_level.Set(own_level.Id)
                    top_offset.Set(height)
                    columns += 1
            except Exception as error:
                failed += 1
                _log("[PLACEMENT WARN] Top level of member {} not released: {}".format(
                    member_id.IntegerValue, error))

        status = transaction.Commit()
        if status != TransactionStatus.Committed:
            _log("[PLACEMENT WARN] Releasing the top levels did not commit ({}).".format(status))
            return 0, 0, 0
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise

    _log("[NOTE] Top levels released (the apartment no longer needs a level above): "
         "{} wall(s), {} room(s), {} column(s); {} could not be changed.".format(walls, rooms, columns, failed))
    return walls, rooms, columns


_paste_diagnosis = {"done": False}


def _diagnose_paste_failure(document, src_doc, src_group, delta):
    """Which member of the apartment makes Revit refuse the paste. Runs ONCE
    per session, after a failed placement: lists what the group holds, then
    - with the group taken apart in the apartment file (rolled back at the end)
    and every copy into the project rolled back too - tries to paste the
    members one by one at the same height. Each member is announced with a
    [NOTE] line, and the failure Revit reports for it follows in the log."""
    from Autodesk.Revit.DB import TransactionGroup

    if _paste_diagnosis["done"]:
        return
    _paste_diagnosis["done"] = True

    counts = {}
    for member_id in src_group.GetMemberIds():
        element = src_doc.GetElement(member_id)
        try:
            category = element.Category.Name if element.Category is not None else "-"
        except Exception:
            category = "-"
        key = "{} / {}".format(category, element.GetType().Name)
        counts[key] = counts.get(key, 0) + 1
    _log("[NOTE] Diagnosis - the group holds: {}.".format(
        u", ".join(u"{} x {}".format(count, key) for key, count in sorted(counts.items()))))

    source_group_transaction = TransactionGroup(src_doc, "Diagnose paste")
    source_group_transaction.Start()
    try:
        ungroup = Transaction(src_doc, "Ungroup for diagnosis")
        ungroup.Start()
        member_ids = list(src_group.UngroupMembers())
        ungroup.Commit()

        for member_id in member_ids:
            element = src_doc.GetElement(member_id)
            if element is None:
                continue
            try:
                label = "{} / {} / '{}' (Id {})".format(
                    element.Category.Name if element.Category is not None else "-",
                    element.GetType().Name, _element_name(element), member_id.IntegerValue)
            except Exception:
                label = "Id {}".format(member_id.IntegerValue)
            _log("[NOTE] Diagnosis - pasting alone: {}".format(label))

            paste_group = TransactionGroup(document, "Diagnose paste member")
            paste_group.Start()
            try:
                paste = Transaction(document, "Diagnose paste")
                paste.Start()
                apply_silent_failure_handling(paste)
                try:
                    options = CopyPasteOptions()
                    options.SetDuplicateTypeNamesHandler(UseDestinationTypes())
                    ElementTransformUtils.CopyElements(
                        src_doc, List[ElementId]([member_id]), document, Transform.CreateTranslation(delta), options)
                    status = paste.Commit()
                    if status != TransactionStatus.Committed:
                        _log("[NOTE] Diagnosis - REFUSED: {}".format(label))
                except Exception as error:
                    if paste.HasStarted() and not paste.HasEnded():
                        paste.RollBack()
                    _log("[NOTE] Diagnosis - REFUSED: {} ({})".format(label, error))
            finally:
                paste_group.RollBack()
    finally:
        source_group_transaction.RollBack()
    _log("[NOTE] Diagnosis finished.")


def _open_source_document(application, rvt_path):
    """Open an apartment file for reading. Workshared files are opened
    detached (no contact with the central/no local copy) with all worksets
    open - closing worksets would leave the group's elements unloaded and
    uncopyable. Any problem with the lighter path falls back to the plain
    OpenDocumentFile."""
    try:
        from Autodesk.Revit.DB import (
            BasicFileInfo, DetachFromCentralOption, ModelPathUtils, OpenOptions,
            WorksetConfiguration, WorksetConfigurationOption,
        )

        options = OpenOptions()
        try:
            options.Audit = False
        except Exception:
            pass

        if BasicFileInfo.Extract(rvt_path).IsWorkshared:
            options.DetachFromCentralOption = DetachFromCentralOption.DetachAndPreserveWorksets
            options.SetOpenWorksetsConfiguration(
                WorksetConfiguration(WorksetConfigurationOption.OpenAllWorksets)
            )

        return application.OpenDocumentFile(
            ModelPathUtils.ConvertUserVisiblePathToModelPath(rvt_path), options
        )
    except Exception as error:
        _log("[PLACEMENT WARN] Light open failed ({}); using plain open.".format(error))
        return application.OpenDocumentFile(rvt_path)


_LEVEL_SNAP_FT = 0.010 / FT_TO_M     # 10 mm

# The ways the apartment's GROUP is pasted, in this order; the next one is tried
# when a way fails: into the plan of the roof's level together with its attached
# dimensions/tags, the same without them, from file to file with the whole shift,
# from file to file shifted in the plan only (the height put right afterwards).
_PASTE_WAYS = (
    "view_with_details", "view_with_dimensions", "view_with_tags", "view_group_only",
    "doc_full_shift", "doc_plane_shift",
)
_VIEW_PASTE_WAYS = ("view_with_details", "view_with_dimensions", "view_with_tags", "view_group_only")


# ============================================================
# THE BACKEND'S PART OF THE SOLVERS
#
# How the apartment is rotated, mirrored and positioned on its footprint (everything the
# "_solve_*" functions above decide) is decided by the backend (backend/froglet_backend/core/
# solvers.py - the functions above, copied verbatim). They only READ a small part of the Revit
# model group: its walls, doors and windows with their geometry, and which rooms lie next to
# them. None of that depends on the footprint, so it is read ONCE per apartment file into an
# ApartmentDescriptor (see core/apartment.py), sent with every request, and the answer is
# the backend's solution of the alignment and the mirror.
# ============================================================

def _descriptor_xyz(point):
    return None if point is None else [point.X, point.Y, point.Z]


def _descriptor_wall(wall):
    """One wall of the apartment descriptor."""
    entry = {
        "kind": "wall", "id": wall.Id.IntegerValue, "p0": None, "p1": None, "mid": None, "length": None,
        "width": None, "bbox": None, "samples": [], "roomProbe": None,
    }
    curve = None
    try:
        curve = wall.Location.Curve
        entry["p0"] = _descriptor_xyz(curve.GetEndPoint(0))
        entry["p1"] = _descriptor_xyz(curve.GetEndPoint(1))
        entry["length"] = curve.Length
    except Exception:
        entry["p0"] = entry["p1"] = entry["length"] = None
        curve = None
    if curve is not None:
        try:
            entry["mid"] = _descriptor_xyz(curve.Evaluate(0.5, True))
        except Exception:
            pass
        # about one point per foot along the centre line (see _apartment_wall_sample_points_ft)
        try:
            count = max(2, int(curve.Length / _WALL_SAMPLE_SPACING_FT) + 1)
            samples = []
            for index in range(count):
                try:
                    point = curve.Evaluate(index / float(count - 1), True)
                    samples.append([point.X, point.Y])
                except Exception:
                    samples.append(None)
            entry["samples"] = samples
        except Exception:
            pass
    try:
        entry["width"] = wall.Width
    except Exception:
        pass
    try:
        box = wall.get_BoundingBox(None)
        if box is not None:
            entry["bbox"] = [box.Min.X, box.Min.Y, box.Min.Z, box.Max.X, box.Max.Y, box.Max.Z]
    except Exception:
        pass

    # Which room lies just past each face of the wall: what _wall_face_point_and_normal_ft asks Revit.
    try:
        direction = get_wall_direction2d_ft(wall)
        half_width_ft = wall.Width / 2.0
        if direction is not None and half_width_ft > 1e-9:
            mid_ft = get_wall_midpoint_ft(wall)
            normal_x, normal_y = -direction.Y, direction.X
            document = wall.Document
            probe_z = mid_ft.Z + 3.0
            room_a = document.GetRoomAtPoint(XYZ(
                mid_ft.X + normal_x * half_width_ft, mid_ft.Y + normal_y * half_width_ft, probe_z))
            room_b = document.GetRoomAtPoint(XYZ(
                mid_ft.X - normal_x * half_width_ft, mid_ft.Y - normal_y * half_width_ft, probe_z))
            entry["roomProbe"] = [room_a is not None, room_b is not None]
    except Exception:
        entry["roomProbe"] = None
    return entry


def _descriptor_room(room):
    if room is None:
        return None
    try:
        name = room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u""
    except Exception:
        name = u""
    return {"name": name}


def _descriptor_instance(element):
    """(descriptor entry, the Wall that hosts it or None) of a family instance of the group."""
    entry = {
        "kind": "instance", "id": element.Id.IntegerValue, "family": None, "category": None, "categoryName": None,
        "hostWallId": None, "hostDesc": u"None", "point": None, "sideRooms": None,
    }
    try:
        entry["family"] = element.Symbol.Family.Name
    except Exception:
        entry["family"] = None

    category = None
    try:
        category = element.Category
    except Exception:
        category = None
    if category is not None:
        try:
            category_id = category.Id.IntegerValue
            entry["category"] = (
                "door" if category_id == int(BuiltInCategory.OST_Doors)
                else "window" if category_id == int(BuiltInCategory.OST_Windows) else "other"
            )
        except Exception:
            entry["category"] = "other"
        try:
            entry["categoryName"] = category.Name
        except Exception:
            entry["categoryName"] = u"?"

    host = None
    try:
        host = element.Host
        entry["hostDesc"] = (
            u"Wall(w={:.3f}ft)".format(host.Width) if isinstance(host, Wall)
            else type(host).__name__ if host is not None else u"None"
        )
    except Exception:
        entry["hostDesc"] = u"?"
        host = None
    host_wall = host if isinstance(host, Wall) else None
    if host_wall is not None:
        entry["hostWallId"] = host_wall.Id.IntegerValue

    try:
        entry["point"] = _descriptor_xyz(element.Location.Point)
    except Exception:
        entry["point"] = None

    if entry["category"] == "door" and host_wall is not None:
        try:
            first, second = _rooms_on_both_sides_of_door(element)
            entry["sideRooms"] = [_descriptor_room(first), _descriptor_room(second)]
        except Exception:
            entry["sideRooms"] = None
    return entry, host_wall


def build_apartment_descriptor(src_group):
    """The ApartmentDescriptor of the apartment's model group (JSON-ready dict)."""
    document = src_group.Document
    try:
        member_ids = list(src_group.GetMemberIds())
    except Exception:
        member_ids = []

    members, member_wall_ids, outside_walls = [], set(), {}
    for eid in member_ids:
        element = document.GetElement(eid)
        if isinstance(element, Wall):
            members.append(_descriptor_wall(element))
            member_wall_ids.add(element.Id.IntegerValue)
        elif isinstance(element, FamilyInstance):
            entry, host_wall = _descriptor_instance(element)
            members.append(entry)
            if host_wall is not None:
                outside_walls[host_wall.Id.IntegerValue] = host_wall

    # The walls bordering a room named "Коридор": probable door walls (see _alternative_door_walls).
    from Autodesk.Revit.DB import SpatialElementBoundaryOptions

    corridor_ids = []
    options = SpatialElementBoundaryOptions()
    for room in FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType():
        try:
            if room.Area <= 0:
                continue
            name = room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString() or u""
            if u"коридор" not in name.lower():
                continue
            loops = room.GetBoundarySegments(options)
        except Exception:
            continue
        for loop in loops or []:
            for segment in loop:
                try:
                    wall = document.GetElement(segment.ElementId)
                except Exception:
                    continue
                if isinstance(wall, Wall):
                    wall_id = wall.Id.IntegerValue
                    if wall_id not in corridor_ids:
                        corridor_ids.append(wall_id)
                    outside_walls[wall_id] = wall

    # The walls bordering the rooms the entrance doors ("Вхід") lead from / to: probable entrance walls too
    # (see _entrance_wall_candidates).
    entrance_room_ids = []
    entrance_rooms = {}
    for door in _entrance_doors(src_group):
        for room_property in ("FromRoom", "ToRoom"):
            try:
                room = getattr(door, room_property)
            except Exception:
                room = None
            if room is not None:
                try:
                    entrance_rooms[room.Id.IntegerValue] = room
                except Exception:
                    pass
    for room_key in sorted(entrance_rooms):
        try:
            loops = entrance_rooms[room_key].GetBoundarySegments(options)
        except Exception:
            continue
        for loop in loops or []:
            for segment in loop:
                try:
                    wall = document.GetElement(segment.ElementId)
                except Exception:
                    continue
                if isinstance(wall, Wall):
                    wall_id = wall.Id.IntegerValue
                    if wall_id not in entrance_room_ids:
                        entrance_room_ids.append(wall_id)
                    outside_walls[wall_id] = wall

    host_walls = [
        _descriptor_wall(wall) for wall_id, wall in sorted(outside_walls.items()) if wall_id not in member_wall_ids
    ]

    group_bbox = None
    try:
        box = src_group.get_BoundingBox(None)
        if box is not None:
            group_bbox = [box.Min.X, box.Min.Y, box.Min.Z, box.Max.X, box.Max.Y, box.Max.Z]
    except Exception:
        group_bbox = None

    return {"groupBbox": group_bbox, "members": members, "hostWalls": host_walls, "corridorWallIds": corridor_ids,
            "entranceRoomWallIds": entrance_room_ids}


DESCRIPTOR_VERSION = 2     # bump when build_apartment_descriptor changes what it writes: the backend
                            # keeps descriptors by (file, version), so older ones are not used again


class _NoDescriptor(Exception):
    """The apartment could not be described for the backend."""


def _file_signature(path):
    """(modified time in ns, size) of the file - the signature the apartment catalog uses - or
    None when it cannot be read (the descriptor then always goes with the request)."""
    try:
        file_stat = os.stat(path)
        modified_ns = getattr(file_stat, "st_mtime_ns", int(file_stat.st_mtime * 1000000000))
        return [int(modified_ns), int(file_stat.st_size)]
    except Exception:
        return None


def _solve_alignment_remote(
    src_group, source_cache, cache_key, angle_rad, target_x_ft, target_y_ft, target_z_ft,
    entrance_alignment, corner_alignment, through_alignment, footprint_polygon_ft, rvt_path=None,
):
    """The backend's answer for the alignment of this apartment on its footprint, or None when the
    descriptor could not be read. The backend keeps the descriptors by
    (file, signature): it is built - and sent - only when it does not have this version of the file."""
    def _describe():
        try:
            return _memo(source_cache, ("descriptor", cache_key), lambda: build_apartment_descriptor(src_group))
        except Exception as error:
            _log("[PLACEMENT WARN] Could not describe the apartment for the backend ({}).".format(error))
            raise _NoDescriptor()

    signature = _file_signature(rvt_path) if rvt_path else None
    key = {"rvtPath": rvt_path, "signature": signature, "version": DESCRIPTOR_VERSION} if signature else None
    try:
        answer = backend_api.solve_placement_cached({
            "angleRad": angle_rad,
            "targetXFt": target_x_ft, "targetYFt": target_y_ft, "targetZFt": target_z_ft,
            "entranceAlignment": entrance_alignment, "cornerAlignment": corner_alignment,
            "throughAlignment": through_alignment,
            "footprintFt": [[point[0], point[1]] for point in footprint_polygon_ft] if footprint_polygon_ft else None,
        }, key, _describe)
    except _NoDescriptor:
        return None
    for note in answer["notes"]:
        _log(note)
    return answer


def place_apartment_group(document, application, rvt_path, level, target_x_ft, target_y_ft, target_z_ft, angle_rad, entrance_alignment=None, corner_alignment=None, footprint_polygon_ft=None, source_cache=None, through_alignment=None):
    """
    Copy the model group out of rvt_path and place it in `document`.

    - The copied group's own centre (see get_group_center_at_lowest_wall_ft:
      the bounding box's X/Y centre, at the Z of the lowest point of any
      Wall member) lands at (target_x_ft, target_y_ft, target_z_ft), and
      the group is rotated by angle_rad (radians, standard CCW-positive
      convention around +Z) about that same target point - UNLESS
      entrance_alignment is given (a dict with targetXFt/targetYFt/
      dirXFt/dirYFt) and the source file has an entrance door (a
      FamilyInstance whose family name contains "Вхід") hosted on a Wall:
      then that door's host wall's own midpoint is what lands at
      (target_x_ft, target_y_ft) - target_x_ft/target_y_ft should already
      equal entrance_alignment's own target point in that case - and
      angle_rad is recomputed to align that wall's own direction with
      (dirXFt, dirYFt) instead of using the passed-in angle_rad. Z always
      comes from target_z_ft the same way either way. Falls back to the
      plain whole-group-centre behaviour if no such door/wall is found.
    - The entrance-door-to-МЗК-side alignment above is the ONE hard rule
      with no exception: it alone ever determines rotation and
      translation. Whether the group also needs MIRRORING is a separate,
      later decision that never changes that rotation/translation - a
      mirror is a reflection about the door wall's own corner (see the
      mirror block's own comment), which by construction preserves the
      door's face position and outward direction and only flips which
      way the apartment extends sideways from it. That decision: rotate
      the apartment's own dominant window-facing direction (by window
      COUNT across all window-bearing walls, not by wall length - see
      _dominant_window_direction_ft) by the door-determined angle_rad,
      and check whether it still points the same general way (not
      "onto", just "parallel, same sense" - the apartment and the
      footprint rarely have exactly the same depth) as the footprint's
      own EXTERIOR side (entrance_alignment's outsideNormalXFt/YFt, from
      "Вибір плями квартири.pushbutton\\script.py"'s
      _outside_facing_side_target_ft). If it doesn't, the group is
      mirrored. Falls back to the older, cruder
      _apartment_overlap_fraction_in_footprint check (does most of the
      apartment's own bounding box land inside footprint_polygon_ft at
      all) only when no window direction or no exterior-side info is
      available.
    - The group is placed as-is otherwise, for now: it keeps whatever
      Reference Level/Origin Level Offset and member level/offset
      parameters it had in its own source file - rehost_group_members_to_level
      (ungroup, fix every member's level/offset individually, regroup)
      exists but isn't called here anymore; re-enable it later if the
      as-copied levels turn out not to be good enough.
    - Finally, the source file's matching "D"-named detail-group
      dimensions are rebuilt against the newly hosted walls, in whichever
      plan view is active (or the first matching plan view on `level`).

    Returns the final placed Group on success, or None - check the
    pyRevit logger for exactly where/why it failed.
    """
    # Entry marker, deliberately BEFORE even the guard checks below - a
    # placement that produces no further log output at all (not even
    # "=== Placing apartment group ===" two lines down, and no caught-
    # exception "розміщення квартири не вдалося" from
    # _execute_pending_placement's own try/except either) is otherwise
    # indistinguishable between "this function was never entered" and
    # "it was entered and got stuck/killed before its own first _log()
    # call" - this line settles which one it is.
    _log("place_apartment_group() entered for rvt_path={}".format(rvt_path))

    if document is None or application is None or level is None:
        _log("[PLACEMENT ERROR] Missing document/application/level.")
        return None

    if not rvt_path or not os.path.isfile(rvt_path):
        _log("[PLACEMENT ERROR] Source file not found: {}".format(rvt_path))
        return None

    # From the FILE's own name, not entrance_alignment - so the R+AP
    # debug-line visual below still recognises this apartment's own type
    # even when there's no footprint at all (Банк квартир opened
    # directly - see _apartment_type_codes_from_filename's own docstring).
    _apt_window_placement_code, _apt_added_part_code = _apartment_type_codes_from_filename(rvt_path)
    is_r_ap_apartment = _apt_window_placement_code == "R" and _apt_added_part_code == "AP"

    target_offset_ft = target_z_ft - level.Elevation

    # A footprint whose bottom sits a few millimetres off its level (seen: -4 mm)
    # would paste the apartment at a height where no level of the project matches
    # the levels its walls are tied to, and Revit refuses the paste ("Can't paste
    # these elements at this height because there is no Level above to
    # Reference"). Within 10 mm the apartment goes exactly onto the level.
    if 1e-6 < abs(target_offset_ft) <= _LEVEL_SNAP_FT:
        _log("[NOTE] Target height {:.1f} mm off level '{}' - placed exactly on the level.".format(
            target_offset_ft * FT_TO_M * 1000.0, _element_name(level)))
        target_z_ft = level.Elevation
        target_offset_ft = 0.0

    target_pt_ft = XYZ(target_x_ft, target_y_ft, target_z_ft)

    _log("=== Placing apartment group ===")
    _log("Source: {}".format(rvt_path))
    _log("Target level: '{}' | offset {:.3f} m".format(_element_name(level), target_offset_ft * FT_TO_M))
    _log("Target XY (m): X={:.3f}, Y={:.3f}".format(target_x_ft * FT_TO_M, target_y_ft * FT_TO_M))
    if entrance_alignment is None:
        _log("Angle: {:.3f} deg".format(math.degrees(angle_rad)))
    else:
        _log("Angle: resolved below, from the entrance wall's own direction (if found).")

    src_doc = None
    grp_id = None
    kept_tag_groups = []  # copied detail groups that hold only room tags
    last_pending_tags["metas"] = []   # (set again below if some tags have to wait)
    dim_metas_all = []
    wall_map = {}
    failures_processing_subscribed = False
    _quiet["on"] = source_cache is not None

    try:
        # Application.FailuresProcessing (not just the per-Transaction
        # AutoResolveCopyWarnings below) so that the "family X had an
        # error and was automatically resolved, but may require review"
        # dialog Revit can pop up while merely OPENING the source
        # document - before any Transaction of ours is even open - gets
        # silently suppressed instead of blocking on the user pressing OK.
        try:
            application.FailuresProcessing += _on_application_failures_processing
            failures_processing_subscribed = True
        except Exception as error:
            _log("[PLACEMENT WARN] Could not subscribe FailuresProcessing: {}".format(error))

        # source_cache (batch placement): a dict {normalised path: open
        # source document} shared by every call of one batch, so each
        # distinct apartment file is opened once and closed by the caller
        # at the end - the source document is only ever read here.
        cache_key = os.path.normcase(rvt_path)
        if source_cache is not None and cache_key in source_cache["docs"]:
            src_doc = source_cache["docs"][cache_key]
        else:
            try:
                src_doc = _open_source_document(application, rvt_path)
            except Exception as error:
                _log("[PLACEMENT ERROR] Could not open source document: {}".format(error))
                return None
            if source_cache is not None:
                source_cache["docs"][cache_key] = src_doc

        _pump_ui_messages()

        def _find_src_group():
            for g in FilteredElementCollector(src_doc).OfClass(Group):
                try:
                    if g.ViewSpecific:
                        continue
                except Exception:
                    pass
                return g
            return None

        # Everything below that depends only on the apartment FILE (not on
        # the footprint it lands on) is computed once per file per batch.
        src_group = _memo(source_cache, ("group", cache_key), _find_src_group)

        if src_group is None:
            _log("[PLACEMENT ERROR] No model groups in source document.")
            return None

        _log("Source group: '{}' (Id={})".format(_element_name(src_group.GroupType), src_group.Id.IntegerValue))

        # The apartment is placed WITHOUT its walls/rooms/columns being tied to
        # a level above (Top Constraint -> Unconnected and so on), so it can be
        # pasted at any height - also where the project has no level above the
        # target level. Done once per file (the open source document is only
        # read and is closed without saving).
        try:
            _memo(source_cache, ("untop", cache_key), lambda: _release_top_levels(src_doc, src_group))
        except Exception as error:
            _log("[PLACEMENT WARN] Could not release the top levels in the apartment file: {}".format(error))

        src_group_origin_ft = _memo(
            source_cache, ("origin", cache_key),
            lambda: get_group_center_at_lowest_wall_ft(src_group),
        )
        used_entrance_wall_alignment = False
        mirror_check_side = None
        needs_mirror = False
        c_np_used_door_alignment = False
        door_wall_overridden = False
        room_mirror_plane = None

        # The backend decides how the apartment sits on its footprint (this version has no local copy of that
        # decision: an apartment that cannot be described for the backend is not placed).
        solved_remotely = None
        if entrance_alignment is not None or corner_alignment is not None or through_alignment is not None:
            solved_remotely = (_solve_alignment_remote(
                    src_group, source_cache, cache_key, angle_rad, target_x_ft, target_y_ft, target_z_ft,
                    entrance_alignment, corner_alignment, through_alignment, footprint_polygon_ft, rvt_path))
            if solved_remotely is None:
                _log(u"[PLACEMENT ERROR] The apartment could not be described for the backend; this version has no local solvers.")
                return None

        if solved_remotely is not None:
            angle_rad = solved_remotely["angleRad"]
            src_group_origin_ft = XYZ(*solved_remotely["origin"])
            target_x_ft, target_y_ft = solved_remotely["target"]
            target_pt_ft = XYZ(target_x_ft, target_y_ft, target_z_ft)
            mirror_check_side = solved_remotely["mirrorCheckSide"]
            needs_mirror = solved_remotely["needsMirror"]
            used_entrance_wall_alignment = solved_remotely["usedEntranceWallAlignment"]
            c_np_used_door_alignment = solved_remotely["cNpUsedDoorAlignment"]
            door_wall_overridden = solved_remotely["doorWallOverridden"]

        # Room types (type-1 rooms away from the stair/lift node, next to the
        # neighbours' type-1 rooms): a Відсутня apartment may be mirrored for
        # that; the rooms as placed are remembered for the next apartment.
        # Only Рядове footprints WITHOUT an added part are mirrored (about the
        # apartment's centre, perpendicular to the outside side - which only a
        # footprint with a single outside side has).
        room_mirror_plane = None
        try:
            outside_dir = None
            footprint_added = None
            if entrance_alignment is not None:
                footprint_added = entrance_alignment.get("addedPartCode")
                if (
                    entrance_alignment.get("outsideDirXFt") is not None
                    and entrance_alignment.get("outsideDirYFt") is not None
                ):
                    outside_dir = (entrance_alignment["outsideDirXFt"], entrance_alignment["outsideDirYFt"])
            room_mirror_plane = _apply_room_type_rules(
                document, src_group, rvt_path, angle_rad, src_group_origin_ft, target_pt_ft,
                mirror_check_side, needs_mirror, footprint_polygon_ft, target_z_ft,
                (footprint_added or _apt_added_part_code) == "NP" and entrance_alignment is not None,
                outside_dir, source_cache,
            )
        except Exception as error:
            _log("[PLACEMENT WARN] Room types: skipped ({}).".format(error))
            room_mirror_plane = None

        _log(
            "- Точка прив'язки квартири (м): ({:.3f}, {:.3f}, {:.3f})".format(
                src_group_origin_ft.X * FT_TO_M, src_group_origin_ft.Y * FT_TO_M, src_group_origin_ft.Z * FT_TO_M,
            )
        )

        wall_info_src = _memo(
            source_cache, ("walls", cache_key), lambda: collect_source_walls(src_group)
        )
        _log("Found {} wall(s) in source group.".format(len(wall_info_src)))

        def _analyze_dimensions():
            metas = []
            mg_type = src_doc.GetElement(src_group.GetTypeId())
            detail_groups = find_detail_groups_for_model_group_type(src_doc, mg_type) if mg_type else []
            for dg in detail_groups:
                metas.extend(
                    analyze_detail_dimensions(src_doc, dg, src_group_origin_ft, wall_info_src)
                )
            return metas

        # The dimension data is relative to the group origin, which the
        # footprint alignment can change - so it is cached per origin too.
        origin_key = (
            round(src_group_origin_ft.X, 6), round(src_group_origin_ft.Y, 6),
            round(src_group_origin_ft.Z, 6),
        )
        dim_metas_all = _memo(source_cache, ("dims", cache_key, origin_key), _analyze_dimensions)

        _log("Captured {} dimension(s) from matching detail group(s).".format(len(dim_metas_all)))

        def _host_dimension_types():
            by_name = {}
            for dt in FilteredElementCollector(document).OfClass(DimensionType):
                name = _element_name(dt)
                if name and name not in by_name:
                    by_name[name] = dt
            default = by_name[sorted(by_name.keys())[0]] if by_name else None
            return by_name, default

        host_dim_type_by_name, host_dim_type_default = _memo(
            source_cache, ("hostdt",), _host_dimension_types
        )

        # --- Tx1: copy + translate + rotate ---
        tx1_done = False
        for copy_mode in _PASTE_WAYS:
            attached_copied_count = 0
            placement_dz = 0.0
            del kept_tag_groups[:]
            t1 = Transaction(document, "Place apartment group")
            t1.Start()
            apply_silent_failure_handling(t1)

            try:
                ids_to_copy = List[ElementId]()
                ids_to_copy.Add(src_group.Id)

                opts = CopyPasteOptions()
                opts.SetDuplicateTypeNamesHandler(UseDestinationTypes())

                delta = XYZ(
                    target_x_ft - src_group_origin_ft.X,
                    target_y_ft - src_group_origin_ft.Y,
                    target_z_ft - src_group_origin_ft.Z,
                )
                xform = Transform.CreateTranslation(delta)
                placement_dz = delta.Z

                # Copy the apartment group TOGETHER with its attached detail groups
                # (D = dimensions, T = tags with the apartment tag's frame and leader)
                # from the source plan view into the host plan view - what a
                # copy/paste of the selection does in Revit. They stay attached to the
                # placed group, so they sit exactly as in the apartment's file and
                # are rotated, mirrored and moved with it; dimensions stay tied to its
                # walls and tags to its rooms. Nothing then needs rebuilding.
                new_ids = None
                paste_stage = "start"
                try:
                    detail_groups = attached_detail_groups(src_doc, src_doc.GetElement(src_group.GetTypeId()))
                    # Pasted into a plan of the roof's own level, like a paste by hand.
                    together_view = plan_view_of_level(document, level) or get_dimension_view(document, level)
                    try:
                        _log("[NOTE] Pasting into view '{}' (level '{}'); the footprint's level is '{}'.".format(
                            _element_name(together_view),
                            _element_name(together_view.GenLevel) if together_view.GenLevel is not None else "-",
                            _element_name(level)))
                    except Exception:
                        pass
                    if copy_mode in _VIEW_PASTE_WAYS and detail_groups and together_view is not None:
                        source_view = src_doc.GetElement(detail_groups[0].OwnerViewId)
                        together = List[ElementId]()
                        together.Add(src_group.Id)
                        for detail_group in detail_groups:
                            if detail_group.OwnerViewId != source_view.Id:
                                continue
                            # Which of the attached groups go along with the apartment:
                            # all of them, only the dimensions ("D"), only the tags ("T"),
                            # or none - each way is tried in turn when one fails.
                            detail_name = (_element_name(detail_group.GroupType) or u"").strip().upper()
                            if (
                                copy_mode == "view_with_details"
                                or (copy_mode == "view_with_dimensions" and detail_name == u"D")
                                or (copy_mode == "view_with_tags" and detail_name == u"T")
                            ):
                                together.Add(detail_group.Id)
                        _log("[NOTE] Pasting {} item(s) from the apartment file's view '{}' (way '{}').".format(
                            together.Count, _element_name(source_view), copy_mode))
                        # A view-to-view copy only takes a shift INSIDE the view's
                        # plane; the height difference is applied to the placed group
                        # afterwards (the attached groups follow it).
                        paste_stage = "CopyElements"
                        new_ids = ElementTransformUtils.CopyElements(
                            source_view, together, together_view,
                            Transform.CreateTranslation(XYZ(delta.X, delta.Y, 0.0)), opts,
                        )
                        paste_stage = "Regenerate"
                        # The view paste puts the apartment on the view's own level - how
                        # high that is, is measured on the pasted group (its lowest wall)
                        # and only the difference to where that wall has to be is moved:
                        # not a fixed shift worked out from the apartment file's levels.
                        document.Regenerate()
                        for copied_id in new_ids:
                            copied = document.GetElement(copied_id)
                            if isinstance(copied, Group) and not copied.ViewSpecific:
                                placed_z = get_group_center_at_lowest_wall_ft(copied).Z
                                missing_z = target_z_ft - placed_z
                                _log("[NOTE] Pasted group: lowest wall at {:.3f} m, wanted {:.3f} m -> moved by {:.3f} m.".format(
                                    placed_z * FT_TO_M, target_z_ft * FT_TO_M, missing_z * FT_TO_M))
                                paste_stage = "MoveElement"
                                if abs(missing_z) > 1e-3:
                                    ElementTransformUtils.MoveElement(document, copied_id, XYZ(0.0, 0.0, missing_z))
                        paste_stage = "checking the copy"
                        attached_copied = [
                            document.GetElement(i) for i in new_ids
                            if isinstance(document.GetElement(i), Group) and document.GetElement(i).ViewSpecific
                        ]
                        if not any(not g.ViewSpecific for g in (document.GetElement(i) for i in new_ids) if isinstance(g, Group)):
                            raise Exception("the copy has no model group")
                        attached_copied_count = len(attached_copied)
                        _log("Copied the group together with {} attached detail group(s) (dimensions/tags).".format(
                            attached_copied_count))
                except Exception as error:
                    try:
                        error_class = error.clsException.GetType().FullName
                    except Exception:
                        error_class = type(error).__name__
                    _log("[PLACEMENT WARN] Pasting into the view failed in {} (way '{}'): {}: {}".format(
                        paste_stage, copy_mode, error_class, error))
                    new_ids = None
                    attached_copied_count = 0

                if new_ids is None:
                    if copy_mode == "doc_full_shift":
                        new_ids = ElementTransformUtils.CopyElements(src_doc, ids_to_copy, document, xform, opts)
                    elif copy_mode == "doc_plane_shift":
                        # Only the shift in the plan; the height is put right afterwards
                        # from where the pasted group's lowest wall actually is.
                        new_ids = ElementTransformUtils.CopyElements(
                            src_doc, ids_to_copy, document,
                            Transform.CreateTranslation(XYZ(delta.X, delta.Y, 0.0)), opts)
                    else:
                        raise Exception("the paste into the view gave no group")
                if new_ids is None or new_ids.Count == 0:
                    raise Exception("CopyElements returned no ids.")

                model_groups = []
                detail_group_ids_to_delete = []

                for nid in new_ids:
                    el = document.GetElement(nid)
                    if isinstance(el, Group):
                        try:
                            if el.ViewSpecific and attached_copied_count:
                                pass    # copied together with the apartment: all of them stay
                            elif el.ViewSpecific:
                                # Attached detail groups that hold only room
                                # tags are KEPT: Revit re-attaches those tags to
                                # the copied rooms itself, with the exact type,
                                # leader and position (rotated/mirrored with the
                                # apartment). Everything else (the dimensions,
                                # rebuilt below) is deleted as before.
                                if _is_room_tag_group(document, el):
                                    kept_tag_groups.append(el)
                                else:
                                    detail_group_ids_to_delete.append(el.Id)
                            else:
                                model_groups.append(el)
                        except Exception:
                            model_groups.append(el)

                for dg_id in detail_group_ids_to_delete:
                    try:
                        document.Delete(dg_id)
                    except Exception as error:
                        _log("[PLACEMENT WARN] Could not delete copied detail group {}: {}".format(
                            dg_id.IntegerValue, error))

                if not model_groups:
                    raise Exception("No model group among copied elements.")

                grp = model_groups[0]
                grp_id = grp.Id

                _log("Copied into host as '{}' (Id={}).".format(_element_name(grp.GroupType), grp_id.IntegerValue))

                grp = document.GetElement(grp_id)
                if grp is None or not grp.IsValidObject:
                    raise Exception("Copied group became invalid before rotation.")

                if copy_mode == "doc_plane_shift":
                    document.Regenerate()
                    placed_z = get_group_center_at_lowest_wall_ft(grp).Z
                    missing_z = target_z_ft - placed_z
                    _log("[NOTE] Pasted group: lowest wall at {:.3f} m, wanted {:.3f} m -> moved by {:.3f} m.".format(
                        placed_z * FT_TO_M, target_z_ft * FT_TO_M, missing_z * FT_TO_M))
                    if abs(missing_z) > 1e-3:
                        ElementTransformUtils.MoveElement(document, grp_id, XYZ(0.0, 0.0, missing_z))

                # Rotate and (if needs_mirror, decided in advance from the
                # SOURCE group's own geometry - see
                # _apartment_overlap_fraction_in_footprint's own docstring for
                # why) mirror, deliberately BOTH before the one Regenerate() below
                # rather than each getting its own: regenerating after
                # rotation alone would make that intermediate, not-yet-
                # mirrored position briefly visible in the active view for a
                # frame before a second regenerate corrected it - confirmed,
                # this is what a "placed wrong for a split second, then
                # jumped to the right place" report was actually seeing.
                if abs(angle_rad) > 1e-9:
                    rot_axis = Line.CreateBound(
                        XYZ(target_pt_ft.X, target_pt_ft.Y, target_pt_ft.Z),
                        XYZ(target_pt_ft.X, target_pt_ft.Y, target_pt_ft.Z + 1.0),
                    )
                    ElementTransformUtils.RotateElement(document, grp_id, rot_axis, angle_rad)

                if needs_mirror:
                    # The apartment's own footprint is wider (along the МЗК
                    # side's direction) than the room this specific
                    # footprint gives it on the neighbour-wall side - the
                    # entrance-wall-to-МЗК-side alignment alone doesn't know
                    # which way "sideways" needs to go. Mirror about the
                    # neighbour-wall side's own line: for the expected
                    # roughly-rectangular corner between the МЗК side and
                    # this adjacent one, that plane's normal is perpendicular
                    # to the МЗК side's own normal, so the entrance wall's
                    # face alignment (position AND outward direction) is
                    # preserved by this reflection - only which way the
                    # apartment extends sideways from the door flips.
                    mirror_normal = XYZ(
                        mirror_check_side["normalXFt"],
                        mirror_check_side["normalYFt"],
                        0.0,
                    )
                    mirror_origin = XYZ(
                        mirror_check_side["pointXFt"],
                        mirror_check_side["pointYFt"],
                        target_pt_ft.Z,
                    )
                    mirror_plane = Plane.CreateByNormalAndOrigin(mirror_normal, mirror_origin)

                    ElementTransformUtils.MirrorElements(
                        document, List[ElementId]([grp_id]), mirror_plane, False
                    )
                    _log(
                        "Apartment mirrored about the neighbour-wall side's "
                        "line (predicted to overflow the footprint boundary "
                        "otherwise)."
                    )

                    # The mirror plane's own origin generally does NOT pass
                    # through target_pt_ft (the door's own reference point) -
                    # it's chosen for other reasons (e.g. R+AP's own
                    # outside-side midpoint - see _solve_r_ap_alignment_ft's
                    # own comment on mirror_check_side). Unless it happens
                    # to, this reflection also carries the door itself away
                    # from where it was placed. Correct for exactly that:
                    # reflect target_pt_ft through the SAME plane to see
                    # where the door would land, then slide the whole
                    # (already-mirrored) group by the difference so the door
                    # ends up back exactly at target_pt_ft regardless of
                    # where the mirror line itself sits.
                    mirror_normal_len = math.hypot(mirror_normal.X, mirror_normal.Y)
                    if mirror_normal_len > 1e-9:
                        n_x = mirror_normal.X / mirror_normal_len
                        n_y = mirror_normal.Y / mirror_normal_len
                        rel_x = target_pt_ft.X - mirror_origin.X
                        rel_y = target_pt_ft.Y - mirror_origin.Y
                        dot = rel_x * n_x + rel_y * n_y
                        mirrored_door_x = target_pt_ft.X - 2.0 * dot * n_x
                        mirrored_door_y = target_pt_ft.Y - 2.0 * dot * n_y
                        correction_x = target_pt_ft.X - mirrored_door_x
                        correction_y = target_pt_ft.Y - mirrored_door_y

                        if abs(correction_x) > 1e-9 or abs(correction_y) > 1e-9:
                            ElementTransformUtils.MoveElement(
                                document, grp_id, XYZ(correction_x, correction_y, 0.0)
                            )
                            _log(
                                "Corrected door position after mirroring by "
                                "({:.3f}, {:.3f}) ft - the mirror plane's own "
                                "origin doesn't pass through the door.".format(
                                    correction_x, correction_y
                                )
                            )

                if room_mirror_plane is not None:
                    # Room types: mirror about the plane through the apartment's own
                    # centre, perpendicular to the outside side (see
                    # _apply_room_type_rules) - the type-1 room moves away from the
                    # stair/lift node.
                    mirror_nx, mirror_ny, mirror_ox, mirror_oy = room_mirror_plane
                    ElementTransformUtils.MirrorElements(
                        document, List[ElementId]([grp_id]),
                        Plane.CreateByNormalAndOrigin(
                            XYZ(mirror_nx, mirror_ny, 0.0), XYZ(mirror_ox, mirror_oy, target_pt_ft.Z)
                        ),
                        False,
                    )
                    _log("Apartment mirrored about its own centre (room types).")

                if abs(angle_rad) > 1e-9 or needs_mirror or room_mirror_plane is not None:
                    document.Regenerate()

                    grp = document.GetElement(grp_id)
                    if grp is None or not grp.IsValidObject:
                        raise Exception("Copied group became invalid after rotation/mirroring.")

                commit_status = t1.Commit()
                if commit_status != TransactionStatus.Committed:
                    # The failure preprocessor (AutoResolveCopyWarnings) can
                    # force an internal rollback during Commit() without
                    # raising - Commit() then just returns a non-Committed
                    # status instead of throwing, so grp_id would silently
                    # point at nothing if this weren't checked explicitly.
                    raise Exception(
                        "Transaction did not commit (status: {}) - likely an "
                        "unresolved Revit warning/error during placement "
                        "(join conflicts, duplicate types, etc).".format(commit_status)
                    )

                _log("[NOTE] The apartment was pasted as a group the way '{}'.".format(copy_mode))
                # Only the file-to-file paste with the whole shift carries the free
                # standing members (furniture, casework, generic models) up by the
                # shift while the walls stay on their level - only then they have to
                # be lowered again afterwards. A paste into the plan of the level
                # (or one that was put right by measuring the walls) keeps them at
                # the same height above the walls as in the apartment's file;
                # lowering them by the shift left them metres below the building.
                if copy_mode != "doc_full_shift":
                    placement_dz = 0.0
                tx1_done = True
                break

            except Exception as error:
                if t1.HasStarted() and not t1.HasEnded():
                    t1.RollBack()
                _log("[PLACEMENT ERROR] Copy/place transaction failed ({}): {}".format(copy_mode, error))
                try:
                    # What the paste was up against: the project's levels and the
                    # levels of the apartment file, against the height it was pasted at.
                    from Autodesk.Revit.DB import Level
                    def _story_flag(level_element):
                        # "Building Story" - the levels Revit counts as floors.
                        try:
                            flag = level_element.get_Parameter(BuiltInParameter.LEVEL_IS_BUILDING_STORY)
                            return u"" if flag is None or flag.AsInteger() else u" [NOT a building story]"
                        except Exception:
                            return u""

                    host_levels = sorted(
                        (lv.Elevation, _element_name(lv) + _story_flag(lv))
                        for lv in FilteredElementCollector(document).OfClass(Level)
                    )
                    source_levels = sorted(
                        (lv.Elevation, _element_name(lv)) for lv in FilteredElementCollector(src_doc).OfClass(Level)
                    )
                    _log("[NOTE] Paste height shift {:.3f} m; project levels (m): {}; apartment file levels (m): {}.".format(
                        placement_dz * FT_TO_M if placement_dz else (target_z_ft - src_group_origin_ft.Z) * FT_TO_M,
                        u", ".join(u"{} = {:.3f}".format(name, elevation * FT_TO_M) for elevation, name in host_levels),
                        u", ".join(u"{} = {:.3f}".format(name, elevation * FT_TO_M) for elevation, name in source_levels),
                    ))
                except Exception:
                    pass
                if copy_mode != _PASTE_WAYS[-1]:
                    continue       # the next way of pasting THE GROUP
                try:
                    _diagnose_paste_failure(document, src_doc, src_group, delta)
                except Exception as diagnosis_error:
                    _log("[NOTE] Diagnosis failed: {}".format(diagnosis_error))
                return None
        if not tx1_done:
            return None

        _pump_ui_messages()

        # --- wall mapping + dimension recreation ---
        # (no document.Regenerate() needed here - t1.Commit() above
        # already regenerated; calling it again with no transaction open
        # is illegal and throws "Modification of the document is
        # forbidden ... no open transaction.")
        grp_host = document.GetElement(grp_id)

        if grp_host is None or not grp_host.IsValidObject:
            _log("[PLACEMENT ERROR] Host group invalid after placement.")
            return None

        host_group_origin_ft = get_group_center_at_lowest_wall_ft(grp_host)

        # Free-standing level-based members (furniture, plumbing, casework,
        # generic models...) come out raised by the height shift of the copy,
        # while the walls (re-hosted on the host level) do not: they float above
        # the floor plan. Bring each back by that shift.
        try:
            _lower_floating_members(document, grp_host, placement_dz)
        except Exception as error:
            _log("[PLACEMENT WARN] Furniture heights could not be corrected: {}".format(error))

        # The apartment's attached detail groups ("D" dimensions, "T" tags with
        # the apartment tag's frame and leader) are SHOWN in the plan view by
        # Revit itself (like "Show attached detail groups"): they are attached to
        # the placed group, so they sit exactly as in the apartment's file and
        # move, turn and mirror with it, dimensions still tied to its walls.
        # Nothing is rebuilt when that works.
        attached_shown = attached_copied_count
        try:
            if attached_copied_count:
                raise _AttachedGroupsShown()
            attach_view = get_dimension_view(document, level)
            attached_type_ids = list(grp_host.GetAvailableAttachedDetailGroupTypeIds()) if attach_view is not None else []
            if attach_view is not None and attached_type_ids:
                show_transaction = Transaction(document, "Show attached detail groups")
                show_transaction.Start()
                apply_silent_failure_handling(show_transaction)
                try:
                    for attached_type_id in attached_type_ids:
                        try:
                            grp_host.ShowAttachedDetailGroups(attach_view, attached_type_id)
                            attached_shown += 1
                        except Exception as error:
                            if "already" in str(error).lower():
                                attached_shown += 1    # it is there (came along with the copy)
                            else:
                                _log("[PLACEMENT WARN] Attached detail group could not be shown: {}".format(error))
                    show_transaction.Commit()
                except Exception:
                    if show_transaction.HasStarted() and not show_transaction.HasEnded():
                        show_transaction.RollBack()
                    attached_shown = 0
                    raise
            _log("Attached detail groups shown in the plan view: {} of {}.".format(
                attached_shown, len(attached_type_ids)))
        except _AttachedGroupsShown:
            pass    # copied together with the apartment already
        except Exception as error:
            _log("[PLACEMENT WARN] Attached detail groups: {}".format(error))
            attached_shown = 0

        # Found unconditionally (not just when used_entrance_wall_alignment)
        # - the R+AP debug-line visual below needs it (door width, and as
        # a stand-in centroid reference) regardless of whether this
        # apartment was placed via a real footprint alignment or the
        # Банк-квартир-opened-directly (0, 0, 0) fallback - it's a
        # property of the apartment FILE itself (a "Вхід"-named door),
        # not of the placement mode.
        host_entrance_wall = find_entrance_wall(grp_host)
        if host_entrance_wall is None:
            # Same wall as in the apartment file: the k-th door of the copy is the
            # k-th door of the original (see _group_doors_sorted).
            by_rooms = _memo(source_cache, ("doorindex", cache_key), lambda: _entrance_door_by_rooms_ft(src_group))
            if by_rooms is not None:
                host_entrance_wall = _entrance_door_wall_by_index_ft(grp_host, by_rooms[0], by_rooms[1])
            if host_entrance_wall is None:
                host_entrance_wall = _thickest_wall_door_ft(grp_host)

        if used_entrance_wall_alignment and entrance_alignment is not None:
            # Must match src_group_origin_ft's own reference point exactly
            # (entrance wall midpoint X/Y, lowest-wall Z), or every wall/
            # dimension "position relative to group origin" computed from
            # the two won't line up - see wall_info_src's usage below.
            # (C+NP's own reference point is a bounding-box corner, not
            # this wall - see the corner_alignment branch just below.)
            if door_wall_overridden:
                # A "Коридор" wall stood in for the door wall: the reference
                # point landed exactly on the target.
                host_group_origin_ft = XYZ(target_x_ft, target_y_ft, host_group_origin_ft.Z)
            elif host_entrance_wall is not None:
                host_entrance_wall_mid_ft = get_wall_midpoint_ft(host_entrance_wall)
                host_group_origin_ft = XYZ(
                    host_entrance_wall_mid_ft.X, host_entrance_wall_mid_ft.Y,
                    host_group_origin_ft.Z,
                )
            else:
                _log(
                    "[PLACEMENT WARN] Entrance wall not found in placed host "
                    "group; wall/dimension mapping may be offset."
                )
        elif used_entrance_wall_alignment and through_alignment is not None:
            # Наскрізне + Присутня: the reference point (the middle of the window
            # side) landed exactly on the target - also after the mirror, which
            # passes through it.
            host_group_origin_ft = XYZ(target_x_ft, target_y_ft, host_group_origin_ft.Z)
        elif used_entrance_wall_alignment and corner_alignment is not None and c_np_used_door_alignment:
            # C+NP Step 5 anchored the translation on the entrance door
            # instead of the window corner - re-find that SAME door wall
            # on the PLACED (host) group (same convention as the
            # entrance_alignment branch above) so wall/dimension
            # mapping's own "position relative to group origin" stays
            # consistent between source and host.
            if door_wall_overridden:
                host_group_origin_ft = XYZ(target_x_ft, target_y_ft, host_group_origin_ft.Z)
            elif host_entrance_wall is not None:
                host_entrance_wall_mid_ft = get_wall_midpoint_ft(host_entrance_wall)
                host_group_origin_ft = XYZ(
                    host_entrance_wall_mid_ft.X, host_entrance_wall_mid_ft.Y,
                    host_group_origin_ft.Z,
                )
            else:
                _log(
                    "[PLACEMENT WARN] C+NP: entrance wall not found in "
                    "placed host group; wall/dimension mapping may be "
                    "offset."
                )
        elif used_entrance_wall_alignment and corner_alignment is not None:
            # C+NP: src_group_origin_ft is where the apartment's own 2
            # window-bearing walls' lines cross, not an entrance wall's
            # midpoint - re-find that SAME crossing point on the PLACED
            # (host) group (same detection logic as the source-side
            # solve, just run again after rotation/mirroring) so wall/
            # dimension mapping's own "position relative to group origin"
            # stays consistent between source and host.
            host_window_sides = _c_np_window_bearing_sides_ft(grp_host)

            if len(host_window_sides) >= 2:
                host_sorted = sorted(
                    host_window_sides.items(), key=lambda item: item[1]["lengthFt"], reverse=True
                )
                host_long_info = host_sorted[0][1]
                host_short_info = host_sorted[1][1]
                host_corner_xy = _line_intersection_2d_ft(
                    (host_long_info["pointXFt"], host_long_info["pointYFt"]),
                    (host_long_info["dirXFt"], host_long_info["dirYFt"]),
                    (host_short_info["pointXFt"], host_short_info["pointYFt"]),
                    (host_short_info["dirXFt"], host_short_info["dirYFt"]),
                )
            else:
                host_corner_xy = None

            if host_corner_xy is not None:
                host_group_origin_ft = XYZ(host_corner_xy[0], host_corner_xy[1], host_group_origin_ft.Z)
            else:
                _log(
                    "[PLACEMENT WARN] C+NP: could not re-find the window "
                    "corner on the placed group; wall/dimension mapping "
                    "may be offset."
                )

        # R+AP debug aid ONLY (a re-measurement for drawing, not part of
        # the actual alignment itself, which already ran its own depth
        # measurement earlier - see _solve_r_ap_alignment_ft's own
        # docstring): re-run _r_ap_apartment_depth_extremes_ft on the
        # PLACED (host-document) group, and draw its own two walls
        # together with the footprint-side measurement "Вибір плями
        # квартири.pushbutton"\\script.py already computed, if any - see
        # _draw_r_ap_depth_debug_lines's own docstring for why both only
        # appear now, after placement, not at pick time. Gated on the
        # apartment FILE's own type (is_r_ap_apartment - see
        # _apartment_type_codes_from_filename), not on entrance_alignment,
        # so this still draws even when there's no footprint at all
        # (Банк квартир opened directly): the footprint-side half of the
        # visual simply won't have anything to draw in that case
        # (_draw_r_ap_depth_debug_lines tolerates entrance_alignment
        # being None/empty), but the apartment-side half still can.
        if False and is_r_ap_apartment and source_cache is None:
            # R+AP depth debug lines (brown/purple) are switched off: no
            # extra lines are drawn for Рядове + Присутня any more.
            try:
                host_reference_window = _r_ap_reference_window_wall_ft(
                    grp_host, (host_group_origin_ft.X, host_group_origin_ft.Y)
                )
                host_door_width_ft = host_entrance_wall.Width if host_entrance_wall is not None else None

                if host_reference_window is not None and host_door_width_ft is not None:
                    host_window_face_point_ft, host_window_normal = host_reference_window
                    host_apt_near, host_apt_far = _r_ap_apartment_depth_extremes_ft(
                        grp_host, host_door_width_ft, host_window_face_point_ft, host_window_normal
                    )
                    _draw_r_ap_depth_debug_lines(
                        document, get_dimension_view(document, level),
                        host_apt_near, host_apt_far, entrance_alignment, target_z_ft,
                    )
                else:
                    _log(
                        "R+AP: no reference window wall or door width "
                        "found on the placed apartment - skipping the "
                        "depth debug lines."
                    )
            except Exception as error:
                _log("[PLACEMENT WARN] R+AP: could not draw depth debug lines: {}".format(error))

        host_walls = []
        for eid in grp_host.GetMemberIds():
            el = document.GetElement(eid)
            if isinstance(el, Wall):
                host_walls.append(el)

        dim_rotation, dim_mirror = angle_rad, None
        if host_walls:
            # Turned AND mirrored like the apartment itself (a mirrored apartment's
            # walls would otherwise be paired with the wrong ones).
            dim_rotation, dim_mirror = _tag_transform(
                angle_rad, needs_mirror, mirror_check_side, room_mirror_plane)
            wall_map = map_walls_by_relative_position(
                src_group_origin_ft, host_group_origin_ft, wall_info_src, host_walls,
                rotation_rad=dim_rotation, mirror_normal=dim_mirror,
            )

        if dim_metas_all and wall_map and not attached_shown:
            if source_cache is None:
                # Log-only helper - nothing to do in batch placement.
                predict_dims_for_host(dim_metas_all, wall_map, host_group_origin_ft, rotation_rad=angle_rad)

            dim_view = get_dimension_view(document, level)
            if dim_view is None:
                _log("[PLACEMENT WARN] No suitable plan view found; dimensions skipped.")
            else:
                create_host_dimensions(
                    document, dim_view, dim_metas_all, wall_map,
                    host_dim_type_by_name, host_dim_type_default, host_group_origin_ft,
                    rotation_rad=dim_rotation, mirror_normal=dim_mirror,
                )

        # Room tags, copied the same way as the dimensions (best effort: a
        # problem here never fails the placement itself). Skipped when the attached
        # "T" group was shown above.
        try:
            def _analyze_tags():
                tag_group_type = src_doc.GetElement(src_group.GetTypeId())
                tag_detail_groups = (
                    find_detail_groups_for_model_group_type(src_doc, tag_group_type)
                    if tag_group_type else []
                )
                return analyze_room_tags(src_doc, src_group, tag_detail_groups)

            if attached_shown:
                # The dimensions and tags came natively, attached to the group.
                # Ungrouping the apartment later (to move or delete walls) loses
                # them - so remember how to make them again (see
                # rebuild_annotations).
                try:
                    snapshot_rotation, snapshot_mirror = _tag_transform(
                        angle_rad, needs_mirror, mirror_check_side, room_mirror_plane)
                    keep_rebuild_data(grp_host.UniqueId, {
                        "dim_metas": dim_metas_all,
                        "wall_map": dict(
                            (src_id, {"host_id": info["host_id"]}) for src_id, info in (wall_map or {}).items()
                        ),
                        "dim_types": (host_dim_type_by_name, host_dim_type_default),
                        "host_origin": host_group_origin_ft,
                        "rotation": dim_rotation,
                        "mirror": dim_mirror,
                        "tag_metas": _memo(source_cache, ("tags", cache_key), _analyze_tags),
                        "tag_rotation": snapshot_rotation,
                        "tag_mirror": snapshot_mirror,
                        "level_id": level.Id.IntegerValue,
                    })
                except Exception as error:
                    _log("[PLACEMENT WARN] Could not remember how to rebuild the dimensions/tags: {}".format(error))
                raise _AttachedGroupsShown()

            tag_view = get_dimension_view(document, level)

            # Tags that came along in their own attached detail group (kept
            # above) are the exact originals. Only if none of them landed in
            # the plan view we use, they are removed and the tags are rebuilt
            # one by one.
            natively_tagged = False
            if kept_tag_groups:
                stray_ids = List[ElementId]()
                for tag_group in kept_tag_groups:
                    try:
                        if not tag_group.IsValidObject:
                            continue
                        owner_id = tag_group.OwnerViewId
                        _log("  Room tags: kept detail group '{}' sits in view '{}'.".format(
                            _element_name(tag_group.GroupType),
                            _element_name(document.GetElement(owner_id)),
                        ))
                        if tag_view is not None and owner_id == tag_view.Id:
                            natively_tagged = True
                        else:
                            stray_ids.Add(tag_group.Id)
                    except Exception:
                        continue

                if stray_ids.Count:
                    stray_transaction = Transaction(document, "Remove misplaced tag groups")
                    stray_transaction.Start()
                    try:
                        for stray_id in stray_ids:
                            try:
                                document.Delete(stray_id)
                            except Exception:
                                pass
                        stray_transaction.Commit()
                    except Exception:
                        stray_transaction.RollBack()

            tag_metas = _memo(source_cache, ("tags", cache_key), _analyze_tags)

            # (All tags incl. the framed apartment tag are rebuilt from the
            # source: the attached detail groups do not come along on their own.)
            if tag_metas:
                # Tags that came along natively are skipped (same room + same
                # type already tagged); only the missing ones are created,
                # e.g. the framed apartment tag that isn't in the "T" group.
                tag_rotation, tag_mirror = _tag_transform(
                    angle_rad, needs_mirror, mirror_check_side, room_mirror_plane)
                _created_tags, failed_tags = create_host_room_tags(
                    document, tag_view, grp_host, tag_metas, tag_rotation, tag_mirror, src_doc,
                )
                if failed_tags:
                    # Rooms that are not enclosed yet cannot be tagged right
                    # now; try again once this placement is over (see
                    # flush_deferred_room_tags).
                    # Kept with the apartment's record too: until the walls
                    # that overlap neighbours' are gone the rooms are not
                    # enclosed and cannot be tagged - see flush_pending_room_tags.
                    last_pending_tags["metas"] = list(failed_tags)
                    last_pending_tags["rotation"] = tag_rotation
                    last_pending_tags["mirror"] = list(tag_mirror) if tag_mirror else None
                    last_pending_tags["levelId"] = level.Id.IntegerValue
                    _deferred_room_tags.append({
                        "group_id": grp_id.IntegerValue,
                        "view_id": tag_view.Id.IntegerValue,
                        "metas": failed_tags,
                        "rotation": tag_rotation,
                        "mirror": tag_mirror,
                    })
        except _AttachedGroupsShown:
            pass    # the "T" group came natively - nothing to create
        except Exception as error:
            _log("[PLACEMENT WARN] Room tags could not be copied: {}".format(error))

        _log("=== Placement finished: group Id {} ===".format(grp_id.IntegerValue))
        return grp_host

    finally:
        if src_doc is not None and src_doc != document and source_cache is None:
            try:
                src_title = src_doc.Title
            except Exception:
                src_title = "<source>"
            try:
                src_doc.Close(False)
                _log("Closed source document '{}' without saving.".format(src_title))
            except Exception as error:
                _log("[PLACEMENT WARN] Could not close source document '{}': {}".format(src_title, error))

        _quiet["on"] = False

        if failures_processing_subscribed:
            try:
                application.FailuresProcessing -= _on_application_failures_processing
            except Exception as error:
                _log("[PLACEMENT WARN] Could not unsubscribe FailuresProcessing: {}".format(error))
