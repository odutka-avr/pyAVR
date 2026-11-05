# -*- coding: utf-8 -*-
"""Turn a plan view so that the direction most of the footprints' sides run in
is vertical (90 degrees) on screen.

Revit has no "rotate view" call: a plan view is turned by its Orientation
(Project North / True North) together with the project's Angle to True North
("Rotate True North"). So: the direction the most side length goes in is found,
the view's Orientation is set to True North, and the project's Angle to True
North is set so that this direction comes out vertical. The coordinates of the
project base point do not change, only the angle.
"""

import math
import traceback

import apartment_group_placement as agp

from Autodesk.Revit.DB import BuiltInParameter, ProjectPosition, XYZ

_ALREADY_DEG = 0.05             # a view this close to the wanted angle is left alone


def _log(message):
    agp._log(u"[VIEW ROTATION] " + message)


def _normalize(angle):
    """Angle in (-pi, pi]."""
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    return angle


def dominant_direction_rad(polygons):
    """Direction (radians, in [0, pi), counter-clockwise from +X of the project)
    most side length of `polygons` ([[(x, y), ...], ...] feet) runs in, or None.
    Opposite directions count as the same one. Decided by the backend
    (core/orientation.py)."""
    import backend_api

    return (backend_api.dominant_direction_rad(polygons))


def _displayed_angle(location):
    """Angle (radians, counter-clockwise on screen) the project's +X axis has in
    a True North view. A True North view turns the project the OPPOSITE way to
    the project-to-shared-coordinates transform (it brings True North up, so it
    undoes that rotation) - measured on a real project: an Angle that the
    transform reports as -78 degrees turned the view by +78 degrees. Hence the
    minus."""
    transform = location.GetTotalTransform()
    return -math.atan2(transform.BasisX.Y, transform.BasisX.X)


def _set_angle(location, angle):
    """Set the project's Angle to True North, keeping the coordinates of the
    project origin as they are."""
    current = location.GetProjectPosition(XYZ.Zero)
    location.SetProjectPosition(
        XYZ.Zero, ProjectPosition(current.EastWest, current.NorthSouth, current.Elevation, angle)
    )


def _set_true_north_orientation(document, view):
    """The view's Orientation -> True North. When the view's template controls
    that parameter (it is read-only on the view then), it is set on the
    template itself - so every view of that template shows True North - or, if
    that cannot be done, taken out of what the template controls."""
    from Autodesk.Revit.DB import ElementId, View

    def _set_on(element):
        parameter = element.get_Parameter(BuiltInParameter.PLAN_VIEW_NORTH)
        if parameter is None or parameter.IsReadOnly:
            return False
        if parameter.AsInteger() != 1:
            parameter.Set(1)        # True North
        return True

    if _set_on(view):
        return True

    template = None
    try:
        if view.ViewTemplateId != ElementId.InvalidElementId:
            template = document.GetElement(view.ViewTemplateId)
    except Exception:
        template = None

    if isinstance(template, View):
        try:
            if _set_on(template):
                _log(u"орієнтацію («Orientation») виду задано True North у шаблоні «{}».".format(template.Name))
                return True
        except Exception:
            pass
        try:
            # Stop the template from controlling Orientation, then set it on the view.
            parameter_id = ElementId(BuiltInParameter.PLAN_VIEW_NORTH)
            not_controlled = template.GetNonControlledParameterIds()
            if parameter_id not in list(not_controlled):
                not_controlled.Add(parameter_id)
                template.SetNonControlledParameterIds(not_controlled)
            if _set_on(view):
                _log(u"шаблон «{}» більше не керує орієнтацією виду; задано True North.".format(template.Name))
                return True
        except Exception:
            _log(u"орієнтацію шаблону не змінено: {}".format(traceback.format_exc()))

    _log(u"орієнтацію виду («Orientation») змінити не вдалося - поворот не буде видно.")
    return False


def already_vertical(document, view, direction):
    """True if `direction` (the main side direction) already shows vertically
    in `view`. (Opposite directions are the same direction.)"""
    try:
        orientation = view.get_Parameter(BuiltInParameter.PLAN_VIEW_NORTH)
        true_north_now = orientation is not None and orientation.AsInteger() == 1
        shown_now = direction + (_displayed_angle(document.ActiveProjectLocation) if true_north_now else 0.0)
        return abs(_normalize(2.0 * (shown_now - math.pi / 2.0))) / 2.0 < math.radians(_ALREADY_DEG)
    except Exception:
        return False


def apply_rotation(document, view, direction):
    """Turn `view` so that `direction` (radians, the main side direction of the
    footprints) shows vertically: the view's Orientation -> True North and the
    project's Angle to True North set to match. Must run inside an open
    Transaction. Returns (angle set, error in radians)."""
    # Wanted: this direction at 90 degrees in the (True North) view.
    wanted_x_axis = math.pi / 2.0 - direction
    location = document.ActiveProjectLocation

    # Which way the project's Angle turns the view: measured, not assumed.
    _set_angle(location, 0.0)
    base = _displayed_angle(location)
    _set_angle(location, 0.3)
    sense = 1.0 if _normalize(_displayed_angle(location) - base) > 0 else -1.0

    angle = _normalize(sense * _normalize(wanted_x_axis - base))
    _set_angle(location, angle)
    reached = _displayed_angle(location)

    _set_true_north_orientation(document, view)

    error = abs(_normalize(reached + direction - math.pi / 2.0))
    _log(u"основний напрямок сторін плям {:.2f}° - вид повернуто, кут до True North {:.2f}° "
         u"(похибка {:.2f}°).".format(math.degrees(direction), math.degrees(angle), math.degrees(error)))
    return angle, error


_SHARED_DEG = 2.0               # a plan whose direction is this close to the screen's axes needs no new angle


def off_axis_rad(shown):
    """How far (radians, signed, within +-45 degrees) a direction shown at the angle `shown` is from the nearest
    screen axis, vertical or horizontal."""
    return (shown - math.pi / 2.0 + math.pi / 4.0) % (math.pi / 2.0) - math.pi / 4.0


def fits_current_angle(document, direction, tolerance_deg=_SHARED_DEG):
    """True when `direction` stands along a screen axis (vertical or horizontal) with the project's Angle to True
    North as it is now - the plan then needs no turn of its own."""
    try:
        shown = direction + _displayed_angle(document.ActiveProjectLocation)
        return abs(off_axis_rad(shown)) < math.radians(tolerance_deg)
    except Exception:
        return False


def other_turned_plans(document, view):
    """Whether any other plan Froglet made shows in True North: the project's Angle to True North turns ALL of
    those at once, so changing it for `view` would turn (and crop away part of) them."""
    from Autodesk.Revit.DB import ElementId, ViewPlan

    import created_views

    ids = set(element_id for element_id, _name in created_views.list_views(document))
    for _sections, views, _key in created_views.list_section_views(document):
        for element_id, _name, kind in views:
            if kind == "plan":
                ids.add(element_id)
    try:
        own = int(view.Id.Value)
    except Exception:
        own = int(view.Id.IntegerValue)
    ids.discard(own)

    for element_id in ids:
        try:
            other = document.GetElement(ElementId(element_id))
            if not isinstance(other, ViewPlan):
                continue
            parameter = other.get_Parameter(BuiltInParameter.PLAN_VIEW_NORTH)
            if parameter is not None and parameter.AsInteger() == 1:
                return True
        except Exception:
            continue
    return False


def rotate_view(document, view, direction):
    """Turn `view` so that `direction` shows vertically - without turning the plans made before.

    The turn is the project's Angle to True North, which every True North plan shares. So: a plan that already
    stands right is left alone; one whose direction already lies along a screen axis with the angle as it is
    (e.g. the same building, another floor) only gets True North; a plan that would need ANOTHER angle gets it
    only when no other Froglet plan is turned by it - otherwise it is left as it is (shown with the angle the
    earlier plans have) and the reason goes to the log. Must run inside an open Transaction.
    Returns "same", "shared", "rotated" or "skipped"."""
    if already_vertical(document, view, direction):
        return "same"
    if fits_current_angle(document, direction):
        _set_true_north_orientation(document, view)
        _log(u"напрямок сторін {:.2f}° уже лежить вздовж осі екрана при поточному куті - кут не змінено.".format(
            math.degrees(direction)))
        return "shared"
    try:
        protected = other_turned_plans(document, view)
    except Exception:
        _log(u"інші плани Froglet не перевірено: {}".format(traceback.format_exc()))
        protected = False
    if protected:
        _log(u"основний напрямок сторін {:.2f}° потребує іншого кута до True North, але його змінити не можна: "
             u"він повертає всі інші плани Froglet. План лишено з поточним кутом.".format(math.degrees(direction)))
        return "skipped"
    apply_rotation(document, view, direction)
    return "rotated"


_CROP_OFFSET_FT = 3000.0 / 304.8      # the crop is the footprints' extents grown by 3000 mm

