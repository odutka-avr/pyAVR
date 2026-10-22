# -*- coding: utf-8 -*-
"""The flats beyond their area bands, shown in a 3D view.

THE BUTTON IN THE SUMMARY (user, 2026-10-19: "у вікні «підсумок прогону» з'явилась кнопка, яка в 3д вигляді виділить
ті квартири, які поза діапазоном"). The window after the run is open while Revit is free, so it may not touch the
model itself: the button raises an ExternalEvent, and in Revit's own time the handler
  1. finds every flat Tadpole placed (the 1k / 2k / 3k roof types) whose area - AVR_Площа Квартири, written by
     `apartments.place` - lies beyond its band (`APARTMENT_BANDS`, the settings window's, with APARTMENT_BAND_EPS of
     slack, as the report counts them);
  2. makes a 3D view active (the active one if it is 3D, else the default {3D}, else the first 3D view);
  3. SELECTS those flats on the site as it stands - nothing isolated or hidden (user, 2026-10-19: "має виділяти
     квартири, але не ізолювати їх у виді, а просто виділяти на посадці").
A second press clears the selection.

The script runs on a persistent engine (bundle.yaml), so this module - and the event - outlive it.
"""

_EVENTS = []                    # the ExternalEvent and its handler, kept alive while the window is open
REQUEST = [None]                # 'show' or 'hide' for the handler
last_error = None


def _id_value(element_id):
    value = getattr(element_id, 'Value', None)
    return value if value is not None else element_id.IntegerValue


def out_of_band(doc):
    """[ElementId] of Tadpole's flats beyond their bands, and how many flats were read."""
    from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory
    # the bands of this run: the settings window's, put into config's own dict (`apartments.adopt_bands`, or the
    # kitchen's answer in the Tadpole-split button) - `apartments.APARTMENT_BANDS` is that very dict
    from tadpole.config import APARTMENT_ROOF_TYPES, APARTMENT_AREA_PARAM, APARTMENT_BAND_EPS, APARTMENT_BANDS
    from tadpole.roofs import type_name
    rooms_of = dict((name.strip().lower(), rooms) for rooms, name in APARTMENT_ROOF_TYPES.items() if name)
    names = {}
    out, read = [], 0
    for roof in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Roofs).WhereElementIsNotElementType():
        type_id = roof.GetTypeId()
        key = _id_value(type_id)
        if key not in names:
            names[key] = (type_name(doc.GetElement(type_id)) or u'').strip().lower()
        rooms = rooms_of.get(names[key])
        if not rooms:
            continue
        parameter = roof.LookupParameter(APARTMENT_AREA_PARAM)
        if parameter is None or not parameter.HasValue:
            continue
        area = parameter.AsDouble()
        read += 1
        lo, hi = APARTMENT_BANDS[rooms]
        if not (lo - APARTMENT_BAND_EPS <= area <= hi + APARTMENT_BAND_EPS):
            out.append(roof.Id)
    return out, read


def _view_3d(doc, uidoc):
    from Autodesk.Revit.DB import FilteredElementCollector, View3D
    active = uidoc.ActiveView
    if isinstance(active, View3D) and not active.IsTemplate:
        return active
    views = [v for v in FilteredElementCollector(doc).OfClass(View3D) if not v.IsTemplate]
    for view in views:
        if view.Name in (u'{3D}', u'{3D - ' + u'{0}'.format(doc.Application.Username) + u'}'):
            return view
    return views[0] if views else None


def arm(on_done=None):
    """An ExternalEvent that shows (REQUEST 'show') or resets ('hide') the flats beyond their bands. `on_done(text)`
    is told what happened, for the window's status line. Returns the event, or None (see `last_error`)."""
    global last_error
    try:
        from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent

        class _Show(IExternalEventHandler):
            def Execute(self, uiapp):
                global last_error
                try:
                    from System.Collections.Generic import List
                    from Autodesk.Revit.DB import ElementId
                    uidoc = uiapp.ActiveUIDocument
                    doc = uidoc.Document
                    view = _view_3d(doc, uidoc)
                    if view is None:
                        text = u'У файлі немає 3D-виду'
                    elif REQUEST[0] == 'hide':
                        uidoc.Selection.SetElementIds(List[ElementId]())
                        text = u'Виділення знято'
                    else:
                        ids, read = out_of_band(doc)
                        if uidoc.ActiveView.Id != view.Id:
                            uidoc.ActiveView = view
                        if not ids:
                            text = u'Поза діапазоном немає жодної квартири ({0} прочитано)'.format(read)
                        else:
                            uidoc.Selection.SetElementIds(List[ElementId](ids))
                            text = u'У виді «{0}» виділено {1} квартир поза діапазоном з {2}'.format(
                                view.Name, len(ids), read)
                    if on_done is not None:
                        on_done(text)
                except Exception as error:
                    last_error = u'{0}: {1}'.format(type(error).__name__, error)
                    try:
                        from pyrevit import forms
                        forms.alert(u'Tadpole не зміг показати квартири в 3D: {0}'.format(last_error), title='Tadpole')
                    except Exception:
                        pass

            def GetName(self):
                return 'Tadpole - the flats beyond their bands in 3D'

        handler = _Show()
        event = ExternalEvent.Create(handler)
        del _EVENTS[:]
        _EVENTS.extend([handler, event])
        return event
    except Exception as error:
        last_error = u'{0}: {1}'.format(type(error).__name__, error)
        return None


def requester(event):
    """What the window's button calls: show=True to show the flats, False to reset the view."""
    def request(show):
        REQUEST[0] = 'show' if show else 'hide'
        event.Raise()
    return request
