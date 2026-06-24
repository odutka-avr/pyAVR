# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

from pyrevit import forms
from System.Windows import Visibility
from System.Collections.ObjectModel import ObservableCollection
from System.ComponentModel import INotifyPropertyChanged

# local custom imports
from rooming_cache import (prefill_building_sections,
                   prefill_global_width,
                   prefill_omitted_categories,
                   prefill_room_types)

# ========================================================================


class Entry(INotifyPropertyChanged):
    """
    Base class for form row data items. Implements INotifyPropertyChanged
    to satisfy WPF data binding requirements in IronPython.

    Args:
        key   (any): The underlying data key (e.g. room type int, building str).
        label (str): Display string shown in the form row.
    """
    def __init__(self, key, label):
        self.key = key
        self._label = label
    
    def get_Label(self):
        return self._label

    Label = property(get_Label)

    def add_PropertyChanged(self, handler):
        pass

    def remove_PropertyChanged(self, handler):
        pass


class EntryRow(Entry):
    """
    Form row representing a labeled input field with a float value.
    Used for room type coefficient rows and building section width rows.

    Args:
        key     (any): Underlying data key.
        label   (str): Display label.
        default (str): Default text value for the input field. Defaults to "1.0".
    """
    def __init__(self, key, label, default="1.0"):
        super(EntryRow, self).__init__(key, label)
        self._value = default
    
    def set_Value(self, value):
        self._value = value
    
    def get_Value(self):
        return self._value
    
    Value = property(get_Value, set_Value)


class CheckRow(Entry):
    """
    Form row representing a labeled checkbox.
    Used for room category omission selection.

    Args:
        key   (any):  Underlying data key.
        label (str):  Display label.
        check (bool): Initial checked state. Defaults to False.
    """
    def __init__(self, key, label, check=False):
        super(CheckRow, self).__init__(key, label)
        self.IsChecked = check


def _parse_float(text):
    """
    Safely parses a string to float, accepting both dot and comma
    as decimal separators.

    Args:
        text (str): Input string from a form TextBox.

    Returns:
        float: Parsed value, or None if parsing fails.
    """
    try:
        return float(text.replace(",", "."))
    except (ValueError, AttributeError) as e:
        return None



class RoomDataForm(forms.WPFWindow):
    """
    WPF form for collecting user inputs required for room area calculation.

    Sections are revealed progressively after the user selects a Design Option:
        1. Rounding slider        — always visible
        2. Design Option picker   — always visible
        3. Room type coefficients — shown after DO is parsed
        4. Finish layer width     — global or per building section
        5. Room categories        — omit from finish layer calculation
        6. Confirm button         — shown when all sections are populated

    Previous user inputs are pre-filled from cache via RoomingCacheManager.

    After Close(), results are available as instance attributes:
        round_by                        (int)
        design_option                   (tuple[DO_set_wrapper, DO_wrapper])
        chosen_do_name                  (str)
        type_coefficients               (dict[int, float])
        building_section_finish_lr_width (dict[str, float] or None)
        global_finish_lr_width          (float or None)
        omitted_categories              (list[str] or None)

    Args:
        xaml_file   (str):               Path to the XAML layout file.
        doc:                             Revit DBDocument.
        do_data     (dict):              {do_name: (DO_set_wrapper, DO_wrapper)}
        room_parser (Room_parser):       Parser instance for model room data.
        cache       (RoomingCacheManager): Cache instance for persistent inputs.
    """

    def __init__(self, xaml_file, doc, do_data, room_parser, cache):
        """
        doc           — Revit document
        do_data       — dict: {do_display_name: do_element}
        room_parser   — Room_parser instance
        """
        forms.WPFWindow.__init__(self, xaml_file)
        self._doc          = doc
        self._do_data      = do_data
        self._room_parser  = room_parser
        self._cache        = cache

        # populate design options combobox
        self.combo_do.ItemsSource = list(do_data.keys())
        # set default slider value
        self.__set_default_slider_val(2)
        

        # hide everything except section 1
        self._hide(self.panel_room_types)
        self._hide(self.panel_finish_width)
        self._hide(self.panel_global_width)
        self._hide(self.panel_categories)
        self._hide(self.panel_confirm)
        self._hide(self.lbl_error)

        # result from user input
        self.round_by = None
        self.design_option = None          # tuple: [DO_set_wrapper, DO_wrapper]
        self.chosen_do_name = None         # str name of selected DO
        self.type_coefficients = None
        self.building_section_finish_lr_width = None
        self.global_finish_lr_width = None
        self.omitted_categories = None


    # ── helpers ──────────────────────────────────────────────────
    def __update_form(self):
        """
        Resets the form to its initial state — hides and clears all dynamically
        populated sections. Called at the start of on_do_selected() when the
        user picks a different Design Option.
        """
        # hide everything except section 1
        self._hide(self.panel_room_types)
        self._hide(self.panel_finish_width)
        self._hide(self.panel_global_width)
        self._hide(self.choice_building_section)
        self._hide(self.items_building_sections)
        self._hide(self.panel_categories)
        self._hide(self.panel_confirm)
        self.hide_error()

        self.chosen_do_name = None

        # clear all previously populated panels
        self.items_room_types.ItemsSource = ObservableCollection[object]()
        self.items_building_sections.ItemsSource = ObservableCollection[object]()
        self.items_categories.ItemsSource = ObservableCollection[object]()

        self.txt_global_width.Text = "0"

        self.radio_buildings_no.IsChecked = False
        self.radio_buildings_yes.IsChecked = False
    

    def __set_default_slider_val(self, default_val):
        """
        Sets the rounding slider to a default value and syncs the label.

        Args:
            default_val (int): Default number of decimal places.
        """
        self.slider_rounding.Value = default_val
        self.lbl_rounding_value.Text = str(default_val)


    # ── visibility helpers ───────────────────────────────────────
    def _show(self, el): 
        el.Visibility = Visibility.Visible
    
    def _hide(self, el): 
        el.Visibility = Visibility.Collapsed

    def show_error(self, msg):
        self.lbl_error.Text = msg
        self._show(self.lbl_error)

    def hide_error(self):
        self._hide(self.lbl_error)

    # ── events ───────────────────────────────────────────────────
    def on_rounding_changed(self, sender, args):
        """
        Fires when the rounding slider value changes.
        Updates the adjacent label to show the current integer value.
        """
        usr_round_val = int(self.slider_rounding.Value)
        self.lbl_rounding_value.Text = str(usr_round_val)


    def on_do_selected(self, sender, args):
        """
        Fires when the user selects a Design Option from the combobox.
        Resets the form, parses rooms for the chosen DO, pre-fills fields
        from cache, and progressively reveals form sections.

        Shows an error and halts if no rooms are found for the selected DO.
        """
        # if new DO chosen, clear every field in the form
        self.__update_form()

        # get chosen DO name from the form input
        self.chosen_do_name = self.combo_do.SelectedItem
        if not self.chosen_do_name:
            return

        self.hide_error()

        # show loading indicator
        self.lbl_loading.Text = "Parsing rooms..."
        self._show(self.lbl_loading)

        # parse rooms via injected function
        room_types, buildings, categories = self._room_parser.parse_rooms(self.chosen_do_name)

        self._hide(self.lbl_loading)

        if not room_types:
            self.show_error("No rooms found in selected Design Option.")
            return


        # get cached data from previous runs
        previous_inputs = self._cache.get_for_design_option(self.chosen_do_name)

        # get cached coefs
        prefilled_coefs = prefill_room_types(previous_inputs, room_types)


        # --- populate + show section 2: room types ---
        self.items_room_types.ItemsSource = ObservableCollection[EntryRow](
                [EntryRow(rt, "Room Type: {}".format(rt), str(prefilled_coefs[rt])) for rt in sorted(list(prefilled_coefs.keys()))]
            )
        self._show(self.panel_room_types)


        # --- populate + show section 3: finish layer width options ---
        self._show(self.panel_finish_width)
        if buildings:

            # get cached per building finish widths
            prefilled_widths, global_mode, b_section_mode = prefill_building_sections(previous_inputs, buildings)
            
            self.items_building_sections.ItemsSource = ObservableCollection[EntryRow](
                [EntryRow(b, "Building {}".format(b), str(prefilled_widths[b])) for b in sorted(list(prefilled_widths.keys()))]
            )
            # choose between global or per building-section finish layer width
            self._show(self.choice_building_section)

            # auto-choose which radio button will be enabled and which panel will be autodisplayed
            if b_section_mode:
                self.radio_buildings_yes.IsChecked = b_section_mode
                self._show(self.items_building_sections)
            elif global_mode:
                self.radio_buildings_no.IsChecked = global_mode
                self._show(self.panel_global_width)
        
        else:
            # display only global finish layer width input
            self._show(self.panel_global_width)
        
        # get cached global width
        global_width = prefill_global_width(previous_inputs)
        self.txt_global_width.Text = str(global_width)


        # --- populate + show section 4: categories ---
        if categories:
            # get cached chosen categories
            prefilled_omitted = prefill_omitted_categories(previous_inputs, categories)

            self.items_categories.ItemsSource = ObservableCollection[CheckRow](
                [CheckRow(c, c, c in prefilled_omitted) for c in sorted(list(categories)) if c]
            )
            self._show(self.panel_categories)

        # show confirm button
        self._show(self.panel_confirm)


    def on_buildings_yes(self, sender, args):
        """
        Fires when the user selects 'Yes' for per-building finish width.
        Shows the building section input list and hides the global width field.
        """
        self._show(self.items_building_sections)
        self._hide(self.panel_global_width)

    def on_buildings_no(self, sender, args):
        """
        Fires when the user selects 'No' for per-building finish width.
        Hides the building section input list and shows the global width field.
        """
        self._hide(self.items_building_sections)
        self._show(self.panel_global_width)

    def on_confirm(self, sender, args):
        """
        Fires when the user clicks Confirm.
        Validates all inputs, sets result attributes, saves inputs to cache,
        and closes the form. Stays open and shows an error message if any
        validation fails.

        Global finish width is set to 0 if neither radio button is selected
        and no building sections are present.
        """
        self.hide_error()

        # set round-by value
        self.round_by = int(self.slider_rounding.Value)
        
        # get tuple with do_set and do
        self.design_option = self._do_data[self.combo_do.SelectedItem]

        # ====== validate coefficients ======
        # dict -> {room_type: coef}
        t_coefficients = {}
        for row in self.items_room_types.ItemsSource:
            val = _parse_float(row.Value)
            if val is None:
                self.show_error("Invalid coefficient for '{}'.".format(row.Label))
                return
            t_coefficients[row.key] = val
        
        self.type_coefficients = t_coefficients

        # ====== collect building widths ======
        # dict -> {building-section: width}
        if (self.items_building_sections.Visibility == Visibility.Visible) and self.radio_buildings_yes.IsChecked:
            building_fn_lr_widths = {}

            for row in self.items_building_sections.ItemsSource:
                val = _parse_float(row.Value)
                if val is None:
                    self.show_error("Invalid width for '{}'.".format(row.Label))
                    return
                building_fn_lr_widths[row.key] = val
        
            self.building_section_finish_lr_width = building_fn_lr_widths

        # ====== collect global width ======
        global_panel_is_visible = self.panel_global_width.Visibility == Visibility.Visible
        b_section_is_collapsed = self.items_building_sections.Visibility == Visibility.Collapsed
        global_is_checked = self.radio_buildings_no.IsChecked

        # global panel must be visible
        # items-building_sections must be collapsed (if DO doesn't have any sections, then all radio buttons are unselected 
        #                                                                             -> cant't check by nutton selection)
        # radio_buildings_no is checked - if there are multiple building sections
        if global_panel_is_visible and (b_section_is_collapsed or global_is_checked):
            global_width = _parse_float(self.txt_global_width.Text)
            if global_width is None:
                self.show_error("Invalid global finish layer width.")
                return
            
            self.global_finish_lr_width = global_width
        
        # if user didnt choose any radio button for finish layer -> set global fn lr to 0
        if (self.panel_global_width.Visibility == Visibility.Collapsed) and (self.items_building_sections.Visibility == Visibility.Collapsed):
            self.global_finish_lr_width = 0
        

        # ====== omitted categories ======
        # list -> [category names,]
        if (self.panel_categories.Visibility == Visibility.Visible):
            self.omitted_categories = [
                row.key for row in self.items_categories.ItemsSource
                if row.IsChecked
            ]


        # ====== set up of cached data ======
        data_to_be_cached = {
            "room_types": {
                str(r_type): coef for r_type, coef in self.type_coefficients.items()
            },
            "omitted_categories": self.omitted_categories
        }

        if self.building_section_finish_lr_width:
            data_to_be_cached["building_sections"] = {str(b_section): lr_width for b_section, lr_width in self.building_section_finish_lr_width.items()}
            data_to_be_cached["global_fn_lr_width_option"] = False
            data_to_be_cached["b_section_fn_lr_width_option"] = True

        if self.global_finish_lr_width:
            data_to_be_cached["global_fn_lr_width"] = self.global_finish_lr_width
            data_to_be_cached["global_fn_lr_width_option"] = True
            data_to_be_cached["b_section_fn_lr_width_option"] = False
        
        self._cache.update_for_design_option(self.chosen_do_name, data_to_be_cached)

        self.Close()

        
