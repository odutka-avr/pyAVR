# -*- coding: utf-8 -*-
"""
form.py
=======
WPF form controller for Apartment Numbering.
Single-building mode: choose design option + start number.
"""

import os
import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows.Media import SolidColorBrush, Color
from pyrevit import forms
from pyrevit import script

logger = script.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
#  FORM RESULT
# ═══════════════════════════════════════════════════════════════════════════

class FormResult(object):
    """Returned by ApartmentNumberingForm.show()."""

    def __init__(self):
        self.building        = None   # BuildingWrapper with .design_option set
        self.start_number_apts    = 1
        self.clockwise       = None
        self.cancelled       = True
        self.start_number_commerce = 1000
        self.start_number_storage = 100


# ═══════════════════════════════════════════════════════════════════════════
#  FORM
# ═══════════════════════════════════════════════════════════════════════════

class ApartmentNumberingForm(forms.WPFWindow):
    """Single-building: pick design option and start number."""

    XAML = os.path.join(os.path.dirname(__file__), "Form.xaml")

    def __init__(self, building):
        self._building = building
        self._result   = FormResult()

        forms.WPFWindow.__init__(self, self.XAML)

        # ── populate building label ───────────────────────────────────────
        self.TxtBuildingName.Text = building.display_name

        # ── populate design option combobox ──────────────────────────────
        self.DoCombo.ItemsSource      = building.design_options
        self.DoCombo.DisplayMemberPath = "name"
        if building.design_options:
            self.DoCombo.SelectedIndex = 0

        # ── footer buttons ────────────────────────────────────────────────
        self.BtnRun.Click    += self._on_run

    # ── run / cancel ──────────────────────────────────────────────────────

    def _on_run(self, sender, e):
        # Validate apt start number
        try:
            apt_start = int(self.TxtStartNumberApts.Text.strip())
            if apt_start < 1:
                raise ValueError
        except (ValueError, AttributeError):
            self.TxtStartNumberApts.BorderBrush = _red_brush()
            return

        # Validate commerce start number
        try:
            com_start = int(self.TxtStartNumberCommerce.Text.strip())
            if com_start < 1:
                raise ValueError
        except (ValueError, AttributeError):
            self.TxtStartNumberCommerce.BorderBrush = _red_brush()
            return
        
        # Validate storage start number
        try:
            storage_start = int(self.TxtStartNumberStorage.Text.strip())
            if com_start < 1:
                raise ValueError
        except (ValueError, AttributeError):
            self.TxtStartNumberStorage.BorderBrush = _red_brush()
            return

        # Validate design option selection
        selected_option = self.DoCombo.SelectedItem
        if selected_option is None:
            self.DoCombo.BorderBrush = _red_brush()
            return

        self._building.design_option = selected_option

        r            = self._result
        r.cancelled  = False
        r.start_number_apts = apt_start
        r.start_number_commerce = com_start
        r.start_number_storage = storage_start
        r.building   = self._building
        r.clockwise  = self.CheckDirection.IsChecked

        self.DialogResult = True
        self.Close()

    # ── public ────────────────────────────────────────────────────────────

    def show(self):
        """Show modal dialog. Returns FormResult, or None if cancelled."""
        dialog_result = self.ShowDialog()
        if dialog_result:
            return self._result
        
        # user closed the window
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _red_brush():
    return SolidColorBrush(Color.FromRgb(220, 80, 80))