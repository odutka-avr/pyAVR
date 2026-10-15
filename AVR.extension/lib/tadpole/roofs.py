# -*- coding: utf-8 -*-
"""Button 2 — place a colour-coded roof on the lower face of each floor.

The roof is a diagnostic: it makes the face classification visible in the
model, one colour per use class. It is not part of the stage 1 output.

SAFETY RULES THIS MODULE FOLLOWS, learned the hard way:

  1. Never continue using a transaction after an API call threw inside
     it. Roll that transaction back. Retrying a failed creation call on a
     live transaction can corrupt the document and take Revit down.
  2. ONE Transaction for the run, with a SubTransaction per element. One
     commit means one regeneration, so a few thousand creations are fast;
     the SubTransaction still isolates a failure to its own element.
  3. Decide how to call the API ONCE, by reflection, before the run. Do
     not guess call signatures element by element.
  4. Stop after a run of consecutive failures rather than grinding
     through every element repeating the same error.
"""

import sys

from Autodesk.Revit.DB import (
    FilteredElementCollector, RoofType, CurveArray, ModelCurveArray,
    BuiltInParameter, BuiltInCategory, Transaction,
    Element, ElementId,
)

from tadpole import geom, tx
from tadpole.config import ROOF_TYPES, ROOF_CLASSES, ROOF_Z_NUDGE_MM


IS_CPYTHON = sys.version_info[0] >= 3

# Give up after this many failures in a row — they will all be the same
# failure, and hammering the API is what crashed Revit before.
MAX_CONSECUTIVE_FAILURES = 5

CALL_MODE = None


class Placement(object):
    def __init__(self, record):
        self.record = record
        self.roof = None
        self.ok = False
        self.reason = None
        self.loops_found = 0
        self.loops_used = 0


# ----------------------------------------------------------------------
# roof type lookup
# ----------------------------------------------------------------------

def _elem_id_value(element_id):
    try:
        return element_id.Value           # 2025+
    except AttributeError:
        return element_id.IntegerValue    # 2024 and earlier


def type_name(element):
    """Read an ElementType's name robustly.

    `element.Name` is the obvious route and also the one that
    intermittently throws. Try the name parameter, then the static
    getter, then the property — and return None rather than hiding it.
    """
    try:
        param = element.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if param is not None:
            value = param.AsString()
            if value:
                return value
    except Exception:
        pass
    try:
        value = Element.Name.GetValue(element)
        if value:
            return value
    except Exception:
        pass
    try:
        return element.Name
    except Exception:
        return None


def _norm(text):
    if text is None:
        return None
    try:
        return u' '.join(u'{0}'.format(text).split()).lower()
    except Exception:
        return None


def available_roof_types(doc):
    """{name: RoofType}, plus scan stats so a miss can be diagnosed."""
    found = {}
    stats = {'scanned': 0, 'named': 0, 'unnamed': 0, 'errors': []}
    seen = set()

    collectors = []
    try:
        collectors.append(FilteredElementCollector(doc).OfClass(RoofType))
    except Exception as exc:
        stats['errors'].append('OfClass(RoofType): {0}'.format(exc))
    try:
        collectors.append(FilteredElementCollector(doc)
                          .OfCategory(BuiltInCategory.OST_Roofs)
                          .WhereElementIsElementType())
    except Exception as exc:
        stats['errors'].append('OfCategory(OST_Roofs): {0}'.format(exc))

    for collector in collectors:
        try:
            elements = collector.ToElements()
        except Exception as exc:
            stats['errors'].append('ToElements: {0}'.format(exc))
            continue
        for element in elements:
            key = _elem_id_value(element.Id)
            if key in seen:
                continue
            seen.add(key)
            stats['scanned'] += 1
            name = type_name(element)
            if name:
                stats['named'] += 1
                found[name] = element
            else:
                stats['unnamed'] += 1

    return found, stats


def resolve_types(doc):
    """use class -> RoofType. Exact name, then folded name, then id."""
    available, stats = available_roof_types(doc)
    normalised = {}
    for name, element in available.items():
        normalised[_norm(name)] = element

    overrides = {}
    try:
        from tadpole.config import ROOF_TYPE_IDS
        overrides = ROOF_TYPE_IDS or {}
    except ImportError:
        pass

    resolved = {}
    missing = {}
    for use_class in ROOF_CLASSES:
        wanted = ROOF_TYPES.get(use_class)

        if wanted and wanted in available:
            resolved[use_class] = available[wanted]
            continue
        if wanted and _norm(wanted) in normalised:
            resolved[use_class] = normalised[_norm(wanted)]
            continue

        override_id = overrides.get(use_class)
        if override_id:
            element = doc.GetElement(ElementId(int(override_id)))
            if element is not None:
                resolved[use_class] = element
                continue

        missing[use_class] = wanted or '(no name configured)'

    return resolved, missing, available, stats


# ----------------------------------------------------------------------
# calling NewFootPrintRoof
# ----------------------------------------------------------------------

def describe_exception(exc, depth=6):
    """Walk the whole InnerException chain and name every link.

    Reflection wraps the real error in TargetInvocationException, and
    IronPython sometimes hides the CLR exception behind `clsException`,
    so a single getattr is not enough to see what actually went wrong.
    """
    parts = []
    current = exc
    count = 0
    while current is not None and count < depth:
        count += 1
        try:
            name = type(current).__name__
        except Exception:
            name = 'Exception'
        message = None
        try:
            value = getattr(current, 'Message', None)
            if value:
                message = '{0}'.format(value)
        except Exception:
            pass
        if message is None:
            try:
                message = '{0}'.format(current)
            except Exception:
                message = '<unprintable>'
        parts.append('{0}: {1}'.format(name, message))

        nxt = None
        try:
            nxt = getattr(current, 'InnerException', None)
        except Exception:
            nxt = None
        if nxt is None:
            try:
                cls_exc = getattr(current, 'clsException', None)
                if cls_exc is not None:
                    nxt = (getattr(cls_exc, 'InnerException', None)
                           or cls_exc)
            except Exception:
                nxt = None
        if nxt is None or nxt is current:
            break
        current = nxt

    return '  <-  '.join(parts)


def footprint_diagnostics(loop, face):
    """Read-only facts about a footprint, for when creation fails.

    Revit refuses footprints for reasons that are invisible in a count
    of curves: a sliver edge below tolerance, a spline where it wants a
    line or arc, an unclosed or non-planar loop.
    """
    info = {}
    try:
        curves = [c for c in loop]
    except Exception:
        return {'error': 'loop is not iterable'}

    info['curves'] = len(curves)

    kinds = {}
    shortest = None
    total = 0.0
    for crv in curves:
        try:
            kind = type(crv).__name__
        except Exception:
            kind = '?'
        kinds[kind] = kinds.get(kind, 0) + 1
        try:
            length = crv.Length
            total += length
            if shortest is None or length < shortest:
                shortest = length
        except Exception:
            pass

    info['kinds'] = kinds
    info['shortest_mm'] = (shortest * 304.8) if shortest is not None else None
    info['total_mm'] = total * 304.8

    try:
        info['is_open'] = loop.IsOpen()
    except Exception:
        info['is_open'] = None
    try:
        info['has_plane'] = loop.HasPlane()
    except Exception:
        info['has_plane'] = None
    try:
        info['face_normal_z'] = face.FaceNormal.Z
        info['face_z_mm'] = face.Origin.Z * 304.8
    except Exception:
        pass

    return info


def find_creation_method(doc):
    """The MethodInfo for NewFootPrintRoof, found by reflection.

    Reflection sidesteps the whole question of how each Python engine
    marshals an `out` parameter: MethodInfo.Invoke takes an object[] and
    writes out-values straight back into it, identically everywhere.
    """
    try:
        creation = doc.Create
        for method in creation.GetType().GetMethods():
            if method.Name != 'NewFootPrintRoof':
                continue
            if len(method.GetParameters()) == 4:
                return method
    except Exception:
        pass
    return None


def _create_roof(method, doc, curve_array, level, roof_type):
    """Create one roof. Raises on failure; never retries."""
    global CALL_MODE

    if curve_array is None:
        raise ValueError('footprint CurveArray is None')
    if curve_array.Size < 3:
        raise ValueError('footprint has only {0} curves'.format(
            curve_array.Size))
    if level is None:
        raise ValueError('Level is None')
    if roof_type is None:
        raise ValueError('RoofType is None')

    if method is not None:
        from System import Array, Object
        args = Array[Object]([curve_array, level, roof_type, None])
        try:
            roof = method.Invoke(doc.Create, args)
        except Exception as exc:
            raise RuntimeError(describe_exception(exc))
        CALL_MODE = 'reflection'
        return roof, args[3]

    # Reflection unavailable — fall back to letting the engine bind the
    # out parameter itself, which both IronPython and Python.NET do by
    # returning it as an extra value.
    out = doc.Create.NewFootPrintRoof(curve_array, level, roof_type)
    CALL_MODE = 'omit'
    if isinstance(out, tuple):
        return out[0], (out[1] if len(out) > 1 else None)
    return out, None


def _call_direct(doc, curve_array, level, roof_type):
    """The non-reflection path, for the probe to try as an alternative."""
    out = doc.Create.NewFootPrintRoof(curve_array, level, roof_type)
    if isinstance(out, tuple):
        return out[0], (out[1] if len(out) > 1 else None)
    return out, None


# ----------------------------------------------------------------------
# call variants
#
# Every attempt so far passed NULL for the fourth argument — a null
# clr.Reference box, an explicit None, or nothing at all. All of them
# raised ArgumentNullException with no parameter name. The Revit API is a
# C++/CLI wrapper, so if that parameter is `ref` in IL rather than a true
# `out`, a null there is an invalid ARGUMENT, not an empty output slot.
# These variants pass a real ModelCurveArray instead.
# ----------------------------------------------------------------------

VARIANTS = [
    'direct4_instance',      # 4 args, a constructed ModelCurveArray
    'clr_ref_instance',      # clr.Reference initialised with an instance
    'reflection_instance',   # reflection, instance in the out slot
    'direct3',               # 3 args, engine binds the out param
]


def call_variant(name, doc, curve_array, level, roof_type, method=None):
    """Invoke NewFootPrintRoof one specific way. Raises on failure."""
    global CALL_MODE

    if name == 'direct4_instance':
        mapper = ModelCurveArray()
        roof = doc.Create.NewFootPrintRoof(
            curve_array, level, roof_type, mapper)
        CALL_MODE = name
        return roof, mapper

    if name == 'clr_ref_instance':
        import clr
        ref = clr.Reference[ModelCurveArray](ModelCurveArray())
        roof = doc.Create.NewFootPrintRoof(
            curve_array, level, roof_type, ref)
        CALL_MODE = name
        return roof, ref.Value

    if name == 'reflection_instance':
        if method is None:
            raise RuntimeError('reflection method not available')
        from System import Array, Object
        args = Array[Object](
            [curve_array, level, roof_type, ModelCurveArray()])
        roof = method.Invoke(doc.Create, args)
        CALL_MODE = name
        return roof, args[3]

    if name == 'direct3':
        return _call_direct(doc, curve_array, level, roof_type)

    raise ValueError('unknown call variant "{0}"'.format(name))


# ----------------------------------------------------------------------
# footprint recipes
#
# Curves taken straight off a solid's face are owned by that element's
# geometry. Revit's sketch builder does not always accept them, so the
# probe tries progressively more detached copies.
# ----------------------------------------------------------------------

def ca_original(loop):
    array = CurveArray()
    for crv in loop:
        array.Append(crv)
    return array


def ca_clone(loop):
    array = CurveArray()
    for crv in loop:
        array.Append(crv.Clone())
    return array


def ca_lines(loop):
    """Rebuild as fresh bound lines from the endpoints.

    Only valid when every curve is already a Line — otherwise it would
    chord away arcs.
    """
    from Autodesk.Revit.DB import Line
    array = CurveArray()
    for crv in loop:
        array.Append(Line.CreateBound(crv.GetEndPoint(0),
                                      crv.GetEndPoint(1)))
    return array


def ca_lines_at_z(loop, z):
    """Fresh lines, flattened onto a given elevation."""
    from Autodesk.Revit.DB import Line, XYZ
    array = CurveArray()
    for crv in loop:
        p0 = crv.GetEndPoint(0)
        p1 = crv.GetEndPoint(1)
        array.Append(Line.CreateBound(XYZ(p0.X, p0.Y, z),
                                      XYZ(p1.X, p1.Y, z)))
    return array


def all_lines(loop):
    try:
        for crv in loop:
            if type(crv).__name__ != 'Line':
                return False
        return True
    except Exception:
        return False


def build_footprint(loop, level_elev, recipe=None):
    """Build the CurveArray using whichever recipe the probe proved."""
    if recipe == 'curves cloned':
        return ca_clone(loop)
    if recipe == 'rebuilt as lines' and all_lines(loop):
        return ca_lines(loop)
    if recipe == 'rebuilt at level Z' and all_lines(loop):
        return ca_lines_at_z(loop, level_elev)
    return ca_original(loop)


def _flatten(roof, mapper):
    """A footprint roof slopes by default. Turn that off on every edge."""
    if mapper is None:
        return
    for model_curve in mapper:
        try:
            roof.set_DefinesSlope(model_curve, False)
        except Exception:
            pass


def _set_offset(roof, face_z, level_elev, nudge):
    offset = (face_z - level_elev) + nudge
    param = roof.get_Parameter(BuiltInParameter.ROOF_LEVEL_OFFSET_PARAM)
    if param is not None and not param.IsReadOnly:
        param.Set(offset)


# ----------------------------------------------------------------------
# the run
# ----------------------------------------------------------------------

def probe(doc, result, resolved_types):
    """Try ONE roof, by each call path, rolling every attempt back.

    Returns (ok, report_dict). Nothing is left in the model either way —
    each attempt gets its own transaction and that transaction is always
    rolled back, so no transaction is ever reused after an exception.

    At most two attempts, then it stops. This is how the call path gets
    validated without risking a run of failures against a live document.
    """
    info = {'attempts': [], 'footprint': None, 'element': None}
    method = find_creation_method(doc)
    info['reflection_found'] = method is not None
    info['candidates'] = 0

    for use_class in ROOF_CLASSES:
        roof_type = resolved_types.get(use_class)
        if roof_type is None:
            continue
        for rec in result.of_class(use_class):
            info['candidates'] += 1
            if rec.level_id is None or not rec.has_geometry:
                continue
            face = geom.lowest_down_face(rec.element)
            if face is None:
                continue
            loop, _total = geom.outer_loop(face)
            if loop is None:
                continue

            curve_array = CurveArray()
            for crv in loop:
                curve_array.Append(crv)
            if curve_array.Size < 3:
                continue

            info['element'] = _elem_id_value(rec.Id) if hasattr(rec, 'Id') \
                else _elem_id_value(rec.id)
            info['use_class'] = use_class
            info['footprint'] = footprint_diagnostics(loop, face)
            info['roof_type'] = type_name(roof_type)

            level = doc.GetElement(rec.level_id)
            info['level'] = getattr(level, 'Name', None) if level else None

            # The footprint is proven clean, so hold it fixed and sweep
            # the CALL VARIANTS instead. Each runs in its own
            # transaction and every transaction is rolled back.
            recipe = 'curves cloned'
            info['recipe'] = recipe

            for variant in VARIANTS:
                try:
                    array = build_footprint(loop, rec.level_elev, recipe)
                except Exception as exc:
                    info['attempts'].append(
                        (variant, False,
                         'building the footprint failed: {0}'.format(
                             describe_exception(exc))))
                    continue

                trans = Transaction(doc, 'Tadpole probe')
                trans.Start()
                try:
                    roof, _mapper = call_variant(
                        variant, doc, array, level, roof_type, method)
                    ok = roof is not None
                    message = 'ok' if ok else 'returned None'
                except Exception as exc:
                    ok = False
                    message = describe_exception(exc)
                finally:
                    # ALWAYS roll back. The probe leaves no trace, and a
                    # transaction that saw an exception is never reused.
                    if trans.HasStarted() and not trans.HasEnded():
                        trans.RollBack()

                info['attempts'].append((variant, ok, message))
                if ok:
                    info['variant'] = variant
                    return True, info

            return False, info

    info['attempts'].append(('none', False, 'no element was eligible'))
    return False, info


def place(doc, result, resolved_types, limit=None, recipe=None,
          variant=None, sections=None, progress=None):
    """Place roofs. One transaction per element, one undo overall.

    `progress` is optional and called as progress(index, total) before
    each roof; returning True stops the run cleanly, committing what has
    been placed so far.
    """
    placements = []
    nudge = geom.mm_to_internal(ROOF_Z_NUDGE_MM) if ROOF_Z_NUDGE_MM else 0.0
    method = find_creation_method(doc)

    from tadpole import ui
    targets = []
    for use_class in ROOF_CLASSES:
        if resolved_types.get(use_class) is None:
            continue
        for rec in result.of_class(use_class):
            if not ui.keep(rec, sections):
                continue
            targets.append((use_class, rec))
    # bottom to top, so a partial run builds from the ground up
    targets.sort(key=lambda pair: (
        pair[1].level_elev if pair[1].level_elev is not None else 0.0))
    if limit:
        targets = targets[:limit]

    consecutive = 0
    aborted = None

    transaction = tx.start(doc, 'Tadpole — classification roofs')
    try:
        for index, (use_class, rec) in enumerate(targets):
            if progress is not None and progress(index, len(targets)):
                aborted = 'cancelled — {0} of {1} attempted'.format(
                    index, len(targets))
                break
            item = Placement(rec)
            placements.append(item)

            # --- read-only preparation, outside any sub-transaction ---
            roof_type = resolved_types.get(use_class)
            if rec.level_id is None:
                item.reason = 'element has no Level'
                continue
            if not rec.has_geometry:
                item.reason = 'element has no solid geometry'
                continue

            face = geom.lowest_down_face(rec.element)
            if face is None:
                item.reason = 'no down-facing horizontal face'
                continue

            loop, total_loops = geom.outer_loop(face)
            item.loops_found = total_loops
            if loop is None:
                item.reason = 'face has no closed boundary'
                continue

            try:
                curve_array = build_footprint(loop, rec.level_elev, recipe)
            except Exception as exc:
                item.reason = 'footprint build failed: {0}'.format(
                    describe_exception(exc))
                continue
            if curve_array is None or curve_array.Size < 3:
                item.reason = 'boundary has fewer than 3 curves'
                continue
            item.loops_used = 1

            face_z = face.Origin.Z
            level = doc.GetElement(rec.level_id)

            # --- one sub-transaction, this element only ---------------
            with tx.Attempt(doc) as attempt:
                if variant:
                    roof, mapper = call_variant(
                        variant, doc, curve_array, level, roof_type, method)
                else:
                    roof, mapper = _create_roof(
                        method, doc, curve_array, level, roof_type)
                if roof is None:
                    raise RuntimeError('NewFootPrintRoof returned None')
                _flatten(roof, mapper)
                _set_offset(roof, face_z, rec.level_elev, nudge)
                attempt.result = roof

            if attempt.ok:
                item.roof = attempt.result
                item.ok = True
                consecutive = 0
            else:
                item.reason = describe_exception(attempt.error)
                consecutive += 1
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    aborted = (
                        'stopped after {0} failures in a row — the rest '
                        'would fail the same way'.format(consecutive))
                    break

        tx.finish(transaction)
    except Exception:
        tx.abort(transaction)
        raise

    return placements, aborted
