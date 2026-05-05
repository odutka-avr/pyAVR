# -*- coding: utf-8 -*-
"""
form.py
=======
WPF form controller for Apartment Numbering.
Loads the XAML, populates the building list, handles events,
and returns a FormResult to script.py.
"""

import os
import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows.Media import SolidColorBrush, Color
from System.Windows.Media import VisualTreeHelper
from System.ComponentModel import INotifyPropertyChanged


from System.IO import File
from pyrevit import forms

from pyrevit import script
logger = script.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
#  VIEW MODEL  –  one row in the building list
# ═══════════════════════════════════════════════════════════════════════════

class BuildingRowViewModel(INotifyPropertyChanged):
    """Data context for one building row in the ListBox."""

    def __init__(self, building_wrapper):
        self.building_wrapper     = building_wrapper
        self.DisplayName          = building_wrapper.display_name
        self.DesignOptions        = building_wrapper.design_options        # list[DesignOptionWrapper]
        logger.debug("++++ VM {}, {}".format(self.DisplayName, self.DesignOptions))
        self.SelectedDesignOption = building_wrapper.design_options[0] if building_wrapper.design_options else None
        self._is_selected         = False

        self._property_changed_handler = None

    def add_PropertyChanged(self, handler):
        """Store the WPF binding change handler (INotifyPropertyChanged contract)."""
        self._property_changed_handler = handler

    def remove_PropertyChanged(self, handler):
        """Clear the WPF binding change handler."""
        self._property_changed_handler = None

    @property
    def IsSelected(self):
        """True if the user checked this document for parsing."""
        return self._is_selected

    @IsSelected.setter
    def IsSelected(self, value):
        """
        Set selection state and notify WPF to re-read the property.
 
        The notification triggers WPF to update any bound controls —
        specifically the IsEnabled binding on the sibling ComboBox.
        """
        self._is_selected = value
        self._notify("IsSelected")
    
    def _notify(self, prop_name):
        """
        Fire the PropertyChanged event for the given property name.
 
        Args:
            prop_name (str): Name of the property that changed.
        """
        from System.ComponentModel import PropertyChangedEventArgs
        self._property_changed_handler(self, PropertyChangedEventArgs(prop_name))

    def __str__(self):
        return "BuildingRowViewModel({})".format(self.DisplayName)


# ═══════════════════════════════════════════════════════════════════════════
#  FORM RESULT
# ═══════════════════════════════════════════════════════════════════════════

class FormResult(object):
    """Returned by ApartmentNumberingForm.show()."""

    def __init__(self):
        self.selected_buildings = []   # list[BuildingWrapper], ordered, design_option set
        self.start_number       = 1
        self.cancelled          = True


# ═══════════════════════════════════════════════════════════════════════════
#  FORM
# ═══════════════════════════════════════════════════════════════════════════

class ApartmentNumberingForm(forms.WPFWindow):
    """Thin controller that wires the XAML to Python logic."""

    XAML = os.path.join(os.path.dirname(__file__), "Form.xaml")

    def __init__(self, buildings):
        self._vms    = [BuildingRowViewModel(b) for b in buildings]
        self._result = FormResult()
        
        forms.WPFWindow.__init__(self, self.XAML)

        # 2. Cache the controls we need — they are available immediately
        #    because WPFWindow.__init__ already parsed the XAML tree.
        self._list    = self.BuildingList       # ListBox
        self._txt     = self.TxtStartNumber     # TextBox
        self._btn_run = self.BtnRun             # Button
 
        # 3. Populate list
        for vm in self._vms:
            self._list.Items.Add(vm)
 
        # 4. Wire window-level buttons (always in tree, no need to wait)
        self.BtnRun.Click    += self._on_run
        self.BtnCancel.Click += self._on_cancel
 
        # 5. Wire per-row controls after layout pass (containers exist then)
        self.Loaded += self._on_loaded

        # self._setup()

    # ── setup ────────────────────────────────────────────────────────────
    def _on_loaded(self, sender, event_args):
        """Called once the window has finished its first layout pass."""
        self._wire_row_events()

    def _wire_row_events(self):
        """
        Attach Click / Checked / Unchecked to every rendered ListBoxItem.
        Safe to call multiple times — we use += so duplicates are possible
        after a reorder; that is harmless (same vm captured, same action).
        A full re-build of Items clears old containers so old handlers are
        gone anyway.
        """
        gen = self._list.ItemContainerGenerator
        for i, vm in enumerate(self._vms):
            lbi = gen.ContainerFromIndex(i)
            if lbi is None:
                continue
 
            cb = _find_by_name(lbi, "RowCheck")
            if cb:
                # Use default-argument capture to freeze vm in the closure
                cb.Checked   += lambda s, ev, v=vm: self._on_check(v, True)
                cb.Unchecked += lambda s, ev, v=vm: self._on_check(v, False)
 
            btn_up = _find_by_name(lbi, "BtnUp")
            if btn_up:
                btn_up.Click += lambda s, ev, v=vm: self._move(v, -1)
 
            btn_down = _find_by_name(lbi, "BtnDown")
            if btn_down:
                btn_down.Click += lambda s, ev, v=vm: self._move(v, +1)

    # ── checkbox ──────────────────────────────────────────────────────────

    def _on_check(self, vm, checked):
        vm.IsSelected = checked
        self._btn_run.IsEnabled = any(v.IsSelected for v in self._vms)

    def _move(self, vm, delta):
        idx = self._vms.index(vm)
        new = idx + delta
        if new < 0 or new >= len(self._vms):
            return
        self._vms.pop(idx)
        self._vms.insert(new, vm)
        self._list.Items.Clear()
        for v in self._vms:
            self._list.Items.Add(v)
        
        # Force WPF to create new containers synchronously before we wire them
        self._list.UpdateLayout()
        self._wire_row_events()


    # ── run / cancel ──────────────────────────────────────────────────────

    def _on_run(self, sender, e):
        try:
            start = int(self._txt.Text.strip())
            assert start >= 1
        except (ValueError, AssertionError):
            self._txt.BorderBrush = _red_brush()
            return

        r = self._result
        r.cancelled = False
        r.start_number = start

        for vm in self._vms:
            if vm.IsSelected:
                bw = vm.building_wrapper
                bw.design_option = vm.SelectedDesignOption
                r.selected_buildings.append(bw)

        self.DialogResult = True
        self.Close()

    def _on_cancel(self, sender, e):
        self.DialogResult = False
        self.Close()

    # ── show ──────────────────────────────────────────────────────────────

    def show(self):
        """
        Show modal dialog.
        Returns FormResult, or None if cancelled.
        """
        self.ShowDialog()

        if self._result.cancelled:
            return None
        return self._result


# ═══════════════════════════════════════════════════════════════════════════
#  VISUAL TREE HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _find_by_name(parent, name):
    """
    Walk the visual tree of ``parent`` and return the first element
    whose x:Name matches ``name``, or None.

    Using FrameworkElement.FindName() directly is simpler and faster
    than a manual tree walk, and works correctly inside DataTemplates
    because each ListBoxItem is its own name scope.
    """
    try:
        # FrameworkElement.FindName searches the local name scope
        result = parent.FindName(name)
        if result is not None:
            return result
    except Exception:
        pass

    # Fallback: manual breadth-first walk (handles edge cases)
    queue = [parent]
    while queue:
        node = queue.pop(0)
        try:
            n = getattr(node, "Name", None)
            if n == name:
                return node
        except Exception:
            pass
        count = VisualTreeHelper.GetChildrenCount(node)
        for i in range(count):
            queue.append(VisualTreeHelper.GetChild(node, i))
    return None


def _red_brush():
    return SolidColorBrush(Color.FromRgb(220, 80, 80))
