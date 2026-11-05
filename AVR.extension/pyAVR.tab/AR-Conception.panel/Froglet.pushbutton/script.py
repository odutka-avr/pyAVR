# -*- coding: utf-8 -*-
"""
Froglet сервер - the ribbon button.

It opens the Froglet window (ui.xaml of this folder, apartment_bank_window.py of lib) when the backend answers and
takes the token (settings.txt); otherwise it says so and stops. Shift+click saves the chosen floor-plan view
template to lib/froglet_template.json (capture_template_snapshot), so a missing "Froglet" template can be re-created
exactly in another project.
"""

import os
import sys

# The window is reused across invocations (moved off-screen and back, never destroyed) with live Python-bound
# event handlers, so this stays a persistent engine rather than a fresh one per click.
__persistentengine__ = True

BUNDLE_DIR = os.path.dirname(__file__)
PANEL_DIR = os.path.dirname(BUNDLE_DIR)
EXTENSION_DIR = os.path.dirname(os.path.dirname(PANEL_DIR))
EXTENSION_LIB_DIR = os.path.join(EXTENSION_DIR, "lib")
FROGLET_LIB_DIR = os.path.join(EXTENSION_LIB_DIR, "froglet")

if FROGLET_LIB_DIR not in sys.path:
    sys.path.append(FROGLET_LIB_DIR)


import backend_api  # noqa: E402
import dkui_bridge  # noqa: E402

# The window and its events are kept in the AppDomain by name, shared by every pyRevit engine of this Revit: another
# Froglet in the same session (the developer plugin) must not find this one's window, nor this one its.
dkui_bridge.set_slot_suffix("_user")

import apartment_bank_window  # noqa: E402

from pyrevit import forms  # noqa: E402


def capture_template_snapshot():
    """Shift+click: save the chosen floor-plan view template (all its
    settings) to lib/froglet_template.json, so a missing "Froglet" template
    can later be re-created exactly in any project."""
    from Autodesk.Revit.DB import FilteredElementCollector, View, ViewType
    from pyrevit import revit

    import template_snapshot

    document = revit.doc
    templates = {}
    for view in FilteredElementCollector(document).OfClass(View):
        try:
            if view.IsTemplate and view.ViewType == ViewType.FloorPlan:
                templates[view.Name] = view
        except Exception:
            continue

    if not templates:
        forms.alert(u"У файлі немає шаблонів планів поверхів.", title="Froglet")
        return

    names = sorted(templates.keys())
    choice = forms.SelectFromList.show(
        names,
        title=u"Froglet: який шаблон зберегти як еталон?",
        button_name=u"Зберегти",
        multiselect=False,
    )
    if not choice:
        return

    snapshot = template_snapshot.capture_template(document, templates[choice])
    path = template_snapshot.save_snapshot(snapshot)
    forms.alert(
        u"Збережено шаблон «{}»:\n{} категорій, {} фільтрів, {} параметрів.\n\n{}".format(
            choice,
            len(snapshot["categories"]),
            len(snapshot["filters"]),
            len(snapshot["parameters"]),
            path,
        ),
        title="Froglet",
    )


if __name__ == "__main__":
    from pyrevit import EXEC_PARAMS

    if EXEC_PARAMS.config_mode:
        capture_template_snapshot()
    else:
        try:
            backend_api.check()
        except Exception as error:
            text = backend_api.describe(error)
            if not isinstance(error, backend_api.BackendUnavailable):
                # (not a network answer: a bug or a broken file - where it happened, so that it can be found; the frames only,
                # the text of such an error may be the very thing that cannot be turned into text)
                try:
                    import os
                    import sys
                    import traceback
                    frames = traceback.extract_tb(sys.exc_info()[2])[-4:]
                    text += u"\n\n" + u"\n".join(
                        u"{}:{} {}".format(os.path.basename(frame[0]), frame[1], frame[2]) for frame in frames)
                except Exception:
                    pass
            forms.alert(
                u"Сервер Froglet недоступний, а без нього ця версія не працює.\n\n{}".format(text),
                title=u"Froglet",
            )
        else:
            window = apartment_bank_window.show_apartment_selector()
            try:
                if window is not None:
                    window.Title = u"Froglet"
            except Exception:
                pass
