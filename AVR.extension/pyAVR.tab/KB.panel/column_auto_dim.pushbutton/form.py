# -*- coding: utf-8 -*-
"""
ViewDimPickerForm
-----------------
A WPF dialog (XAML-backed) that lets the user pick:
  - one or more floor plan views   (multi-select ListBox)
  - exactly one dimension type     (ComboBox)

Usage
-----
    from ViewDimPickerForm import ViewDimPickerForm

    form = ViewDimPickerForm(doc)
    views, dim_type = form.show()

    if views:                      # None / empty list → user cancelled
        for v in views:
            print(v.Name)
        print(dim_type.Name)
"""

import os
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import Application
from System.Windows.Markup import XamlReader
from System.Windows.Controls import ListBoxItem, ComboBoxItem
from System.IO import StringReader

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewPlan, ViewType,
    DimensionType,
    BuiltInParameter
)

from pyrevit import forms

XAML_PATH = os.path.join(os.path.dirname(__file__), "form.xaml")

# ---------------------------------------------------------------------------
# Form class
# ---------------------------------------------------------------------------

class ViewDimPickerForm(forms.WPFWindow):
    """
    Parameters
    ----------
    doc : Autodesk.Revit.DB.Document
        The active Revit document.

    Returns (via show())
    --------------------
    (list[ViewPlan], DimensionType)
        Selected views and the chosen dimension type.
    ([], None)
        When the user closes the window or clicks Cancel.
    """

    def __init__(self, doc, views, dimension_types):
        forms.WPFWindow.__init__(self, XAML_PATH)
        
        self._doc = doc
        self._views    = []          # result: list[ViewPlan]
        self._dim_type = None        # result: DimensionType

        self.parsed_floor_plans = sorted(views, key=lambda v: v.Name)
        self.parsed_dimension_types = sorted(dimension_types, key=lambda v: v.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsValueString())

        # Named controls
        self._lb_views   = self.FindName("LbViews")
        self._cb_dim     = self.FindName("CbDimType")
        self._btn_confirm = self.FindName("BtnConfirm")
        self._btn_cancel  = self.FindName("BtnCancel")

        self._populate()
        self._bind_events()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(self):
        """
        Show the dialog modally.

        Returns
        -------
        tuple(list[ViewPlan], DimensionType | None)
            Empty list + None when cancelled / closed without confirming.
        """
        self.ShowDialog()
        return self._views, self._dim_type

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _populate(self):
        """Fill the ListBox and ComboBox with Revit data."""

        # Floor plans → ListBox
        for vp in self.parsed_floor_plans:
            item = ListBoxItem()
            item.Content = vp.Name
            item.Tag     = vp
            self._lb_views.Items.Add(item)

        # Dimension types → ComboBox
        for dt in self.parsed_dimension_types:
            dim_name = dt.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsValueString()
            if dim_name:
                item = ComboBoxItem()
                item.Content = dim_name
                item.Tag     = dt
                self._cb_dim.Items.Add(item)

        if self.parsed_dimension_types:
            self._cb_dim.SelectedIndex = 0

    def _bind_events(self):
        self._lb_views.SelectionChanged   += self._on_selection_changed
        self._btn_confirm.Click           += self._on_confirm
        self._btn_cancel.Click            += self._on_cancel
        # Closing the window (X button) → treat as cancel (results stay empty)

    # -- event handlers ----------------------------------------------------

    def _on_selection_changed(self, sender, args):
        """Enable Confirm only when at least one view is selected."""
        self._btn_confirm.IsEnabled = self._lb_views.SelectedItems.Count > 0

    def _on_confirm(self, sender, args):
        self._views = [
            item.Tag
            for item in self._lb_views.Items
            if isinstance(item, ListBoxItem) and item.IsSelected
        ]
        chosen = self._cb_dim.SelectedItem
        self._dim_type = chosen.Tag if chosen else None
        self.DialogResult = True
        self.Close()

    def _on_cancel(self, sender, args):
        # _views and _dim_type stay at their empty defaults
        self.DialogResult = False
        self.Close()
