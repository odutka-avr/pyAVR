# -*- coding: utf-8 -*-
"""Select elements in the active view and list their ElementIds in a
small window that can be copied straight to the clipboard.

Click the button, select one or more elements in the model, then press
Finish on the Revit selection bar (or Esc to cancel). Nothing in the
model is read-write modified - this command is purely informational.
"""

import os.path as op

from pyrevit import revit, forms

from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

__title__ = 'Get\nElement IDs'
__author__ = 'BIM Team'
__doc__ = (
    'Select elements in the active view and list their ElementIds in '
    'a copy-ready window. Does not modify the model.'
)

XAML_FILE = op.join(op.dirname(__file__), 'ui.xaml')


class SelectedIdsWindow(forms.WPFWindow):
    """Small read-only window showing the picked ElementIds."""

    def __init__(self, xaml_file, id_values):
        forms.WPFWindow.__init__(self, xaml_file)
        self.lbl_count.Text = 'Selected elements: {}'.format(len(id_values))
        self.tb_ids.Text = '\n'.join(str(v) for v in id_values)
        # pre-select all text so the user can Ctrl+C immediately too
        self.tb_ids.Focus()
        self.tb_ids.SelectAll()

    def btn_copy_click(self, sender, args):
        try:
            import clr
            clr.AddReference('System')
            from System.Windows import Clipboard
            Clipboard.SetText(self.tb_ids.Text)
        except Exception as ex:
            forms.alert('Could not copy to clipboard:\n{}'.format(ex))

    def btn_close_click(self, sender, args):
        self.Close()


def element_id_to_int(element_id):
    """Return a plain int for an ElementId, across Revit API versions.

    Revit 2024+ deprecated .IntegerValue in favor of .Value; older
    versions do not have .Value at all, so try both.
    """
    try:
        return int(element_id.Value)
    except AttributeError:
        return int(element_id.IntegerValue)


def pick_elements():
    """Prompt the user to pick elements in the active view.

    Returns a list of ElementIds in click order, an empty list if the
    user finished with nothing picked, or None if the user cancelled.
    """
    uidoc = revit.uidoc
    doc = revit.doc

    try:
        picked_refs = uidoc.Selection.PickObjects(
            ObjectType.Element,
            'Select elements, then press Finish (Esc to cancel)'
        )
    except OperationCanceledException:
        return None

    if not picked_refs:
        return []

    # keep pick order (meaningful), drop anything that resolves to
    # nothing (e.g. became invalid between pick and lookup)
    ids = []
    for ref in picked_refs:
        el = doc.GetElement(ref.ElementId)
        if el is not None:
            ids.append(ref.ElementId)

    return ids


def main():
    picked_ids = pick_elements()

    if picked_ids is None:
        # user pressed Esc / cancelled the picking operation
        return

    if not picked_ids:
        forms.alert(
            'No elements were selected.',
            title='Get Element IDs',
            warn_icon=False
        )
        return

    id_values = [element_id_to_int(eid) for eid in picked_ids]

    window = SelectedIdsWindow(XAML_FILE, id_values)
    window.ShowDialog()


if __name__ == '__main__':
    main()
