# -*- coding: utf-8 -*-

import os
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("System")

from pyrevit import forms
from System.Windows import Visibility
from System.Collections.ObjectModel import ObservableCollection
from System.ComponentModel import INotifyPropertyChanged
from System.Windows.Media import Brushes
from System.Windows.Media import VisualTreeHelper
from System.Windows.Controls import TextBox, StackPanel, ComboBox, TextBlock

from pyrevit import forms, script
from parsers import DocumentParser, get_clean_model_filename
from wrappers import DevelopmentPhaseWrapper
from enums import FloorType
from schedule_writer import Table

# configure xaml path
XAML_PATH = os.path.join(os.path.dirname(__file__), "Form.xaml")

# configure debugging
logger = script.get_logger()

# =========================================================================
# VIEW-MODEL
# =========================================================================

class DocRowVM(INotifyPropertyChanged):
    """
    View-model for one row in the document selection list (Step 1).
 
    Holds the parser instance, the document reference, the list of
    available design options, and the currently selected design option.
    Also tracks whether the user has checked this document for inclusion.
 
    WPF binds IsSelected to the CheckBox and DesignOptions / SelectedDesignOption
    to the ComboBox. INotifyPropertyChanged is implemented manually so that
    toggling IsSelected causes WPF to re-read it and update IsEnabled on
    the sibling ComboBox.
 
    Attributes:
        Name (str):                   Document title shown in the row.
        Doc:                          Autodesk.Revit.DB.Document.
        parser (DocumentParser):      Parser configured for this document.
        DesignOptions:                ObservableCollection of DesignOptionWrapper.
        SelectedDesignOption:         Currently selected DesignOptionWrapper.
    """

    def __init__(self, parser, design_options, is_selected=False, preselected_do=None):
        """
        Args:
            parser (DocumentParser):       Pre-constructed parser for this doc.
            design_options (list):         List of DesignOptionWrapper from
                                           parser.parse_design_options().
        """
        self._property_changed_handler = None
        self._is_selected       = is_selected
        self.Name               = parser.model_name
        self.Doc                = parser.doc
        self.f_name             = get_clean_model_filename(parser.doc)
        self.parser             = parser
        self.DesignOptions      = ObservableCollection[object]()
        self.SelectedDesignOption = None

        for do in design_options:
            self.DesignOptions.Add(do)
        
        # find the wrapper whose name matches Main model and set it as selected
        if preselected_do:
            self.SelectedDesignOption = preselected_do
        else:
            preselected_do_name = "Main model"
            for do in design_options:
                if do.name == preselected_do_name:
                    self.SelectedDesignOption = do
                    break
        
        # if previously selected - autoselect
        #if is_selected:
            #self.IsSelected = True
            #self._notify("IsSelected")

        
    def add_PropertyChanged(self, handler):
        """Store the WPF binding change handler (INotifyPropertyChanged contract)."""
        self._property_changed_handler = handler
        # fire initial notification so WPF reads the current IsSelected value
        self._notify("IsSelected")

    def remove_PropertyChanged(self, handler):
        """Clear the WPF binding change handler."""
        self._property_changed_handler = None     

    # ── IsSelected with notification ──────────────────────────────────────

    @property
    def IsSelected(self):
        return self._is_selected

    @IsSelected.setter
    def IsSelected(self, value):
        self._is_selected = value
        self._notify("IsSelected")

    def _notify(self, prop_name):
        """
        Fire the PropertyChanged event for the given property name.
 
        Args:
            prop_name (str): Name of the property that changed.
        """
        if self._property_changed_handler is None:
            return
        from System.ComponentModel import PropertyChangedEventArgs
        self._property_changed_handler(self, PropertyChangedEventArgs(prop_name))


class LevelVM(INotifyPropertyChanged):
    """
    View-model for one level row inside an expanded model block (Step 3).
 
    Exposes is_podium and is_building as settable properties bound to
    the Цоколь and Будівельний поверх checkboxes respectively.
 
    The wrapped LevelWrapper.floor_type is only updated when the user
    confirms via OnConfirmFloors — the VM holds the pending state until
    then.
 
    Attributes:
        wrapper (LevelWrapper): The underlying level wrapper.
        level_name (str):       Level name shown in the row.
    """
    def __init__(self, level_wrapper, b_lvl=False, ground_lvl=False):
        """
        Args:
            level_wrapper (LevelWrapper): Level to represent.
        """
        self.wrapper    = level_wrapper
        self.level_name = level_wrapper.name
 
        self._is_podium   = ground_lvl
        self._is_building = b_lvl          # checked by default
 
        self._handler = None
 
    def add_PropertyChanged(self, handler):
        """Store WPF change handler."""
        self._handler = handler
 
    def remove_PropertyChanged(self, handler):
        """Clear WPF change handler."""
        self._handler = None
 
    @property
    def is_podium(self):
        """True if user marked this level as a podium (цокольний поверх)."""
        return self._is_podium
 
    @is_podium.setter
    def is_podium(self, value):
        self._is_podium = value
 
    @property
    def is_building(self):
        """
        True if this level should be counted as a building storey.
 
        When False, the level is removed from BuildingWrapper.levels
        during OnConfirmFloors — it will not contribute to floor counts.
        """
        return self._is_building
 
    @is_building.setter
    def is_building(self, value):
        self._is_building = value



class ModelVM(INotifyPropertyChanged):
    """
    View-model for one collapsible model block in the level-check section (Step 3).
 
    Groups all LevelVM instances for one building and tracks whether the
    block is expanded in the UI. Expand/collapse is driven imperatively
    in OnModelHeaderClick rather than via binding, so is_expanded is
    updated but does not need to notify WPF.
 
    Attributes:
        building (BuildingWrapper): The building whose levels are shown.
        model_name (str):           Document title shown as the block header.
        levels (ObservableCollection): LevelVM instances sorted by elevation.
    """
    def __init__(self, building, cached_b_lvls=None, cached_ground_lvls=None):
        """
        Args:
            building (BuildingWrapper): Source of levels and display name.
        """
        self.building   = building
        self.model_name = building.doc.Title
        self.doc        = building.doc
        self.levels     = ObservableCollection[object]()
 
        self._is_expanded = False
        self._handler     = None
 
        for lv in sorted(building.levels, key=lambda l: l.elevation):
            self.levels.Add(LevelVM(lv, 
                                    b_lvl=lv.name in cached_b_lvls,
                                    ground_lvl=lv.name in cached_ground_lvls))
 
    def add_PropertyChanged(self, handler):
        """Store WPF change handler."""
        self._handler = handler
 
    def remove_PropertyChanged(self, handler):
        """Clear WPF change handler."""
        self._handler = None
 
    def _notify(self, prop):
        if self._handler:
            self._handler(self, PropertyChangedEventArgs(prop))
 
    @property
    def is_expanded(self):
        """True if this model block is currently expanded in the UI."""
        return self._is_expanded
 
    @is_expanded.setter
    def is_expanded(self, value):
        self._is_expanded = value
        self._notify("is_expanded")



class BuildingRowVM(INotifyPropertyChanged):
    """
    View-model for one row in the building number validation list (Step 2).
 
    Pre-fills b_number and b_dev_phase from the majority-vote parsed
    values on BuildingWrapper. The user can overwrite these in the TextBox
    fields before confirming.
 
    Attributes:
        building (BuildingWrapper): The building this row represents.
        name (str):                 Document title shown in the row.
        b_number (str):             Building section ID (editable).
        b_dev_phase (str):          Development phase ID (editable).
    """
    def __init__(self, building):
        """
        Args:
            building (BuildingWrapper): Building to validate.
        """
        self.building = building
        self.name = building.doc.Title

        # prefilled from parsed param values
        self.b_number = building.parsed_building_section_id
        self.b_dev_phase = building.parsed_building_phase_id

        self._property_changed_handler = None

    def add_PropertyChanged(self, handler):
        """Store WPF change handler."""
        self._property_changed_handler = handler

    def remove_PropertyChanged(self, handler):
        """Clear WPF change handler."""
        self._property_changed_handler = None



class ManualFieldsVM(INotifyPropertyChanged):
    """
    View-model for one row in the manual parameter fill section (Step 4).
 
    Holds the pending user-entered values for height, fire rating,
    energy class, and construction duration. Values are None until the
    user types them in. all_properties_set is checked at confirm time
    to block submission if any required field is still empty.
 
    Attributes:
        building (BuildingWrapper):  The building this row represents.
        building_id (str):           Building section ID (display only).
        dev_phase_id (str):          Development phase ID (display only).
        height (float | None):       Max building height in metres.
        fire_rating (str | None):    Fire resistance rating string.
        energy_class (str | None):   Energy efficiency class string.
        duration (float | None):     Construction duration in months.
    """
    def __init__(self, building, height=None, f_rating=None, e_class=None, duration=None):
        self.building = building

        self.building_id = building.building_section_id
        self.dev_phase_id = building.building_phase_id
        
        self.height = height
        self.fire_rating = f_rating
        self.energy_class = e_class
        self.duration = duration

        self._property_changed_handler = None

    def add_PropertyChanged(self, handler):
        self._property_changed_handler = handler

    def remove_PropertyChanged(self, handler):
        self._property_changed_handler = None

    @property
    def all_properties_set(self):
        """
        True if all four manual fields have been filled by the user.
 
        Used by OnConfirmManualData to block form submission until
        complete.
        """
        return self.height and self.fire_rating and self.energy_class and self.duration


# =========================================================================
# FORM CONTROLLER
# =========================================================================

class Form(forms.WPFWindow):
    """
    Multi-step wizard form for configuring and triggering TEP generation.
 
    Steps:
        1. DOSelect    — Choose documents and design options.
        2. BValidation — Confirm building numbers and dev phase IDs.
        3. FloorCheck  — Review and classify levels per building.
        4. FieldFill   — Enter manual params (height, fire rating, etc.)
                         and choose optional table rows.
 
    Each step hides itself and shows the next step on confirm.
    On completion, DialogResult is set to True and the form closes,
    returning the populated ProjectWrapper to the caller.
 
    If the user closes the form before completing all steps, DialogResult
    remains False/None and the caller should not generate the TEP table.
 
    Attributes:
        project (ProjectWrapper):         Accumulates all parsed data.
        docs (list):                      Revit Document objects to offer.
        available_doc_parsers (set):      DocumentParser instances, one per doc.
        usr_do_choice (list):             Selected (parser, DO) tuples from Step 1.
        usr_model_choice (list):          BuildingWrapper instances after Step 1 parse.
        model_lvl_list_vms:               ObservableCollection of ModelVM for Step 3.
        project_name (str | None):        Pending project name from Step 4.
        project_address (str | None):     Pending project address from Step 4.
        type_of_construction (str | None): Pending type of construction from Step 4.
        include_* (bool | None):          Visibility flags for optional TEP rows.
    """

    def __init__(self, docs, project, cache):
        """
        Args:
            docs (list):              Revit Document objects (current + links).
            project (ProjectWrapper): Empty project to populate during the form.
        """
        forms.WPFWindow.__init__(self, XAML_PATH)

        self.project                = project
        self.docs                   = docs # list[RevitLinkInstance]
        self.cache                  = cache
        self.available_doc_parsers  = set()  # gets populated in _build_rows
        self.usr_do_choice          = None
        self.usr_model_choice       = list()    # list of BuildingWrappers

        # DO section
        self.section_do_select  = self.FindName("DOSelect")
        self._doc_list_ctrl     = self.FindName("DocList")
        self._btn_confirm       = self.FindName("BtnConfirm")
        self._rows              = self._build_rows()
        self._populate_list()

        # building validation section
        self.section_b_validation   = self.FindName("BValidation")
        self._b_list_ctrl           = self.FindName("BuildingList")
        self._btn_confirm_b         = self.FindName("BtnConfirmB")
        self._b_rows                = None

        # building level data check
        self.section_level_validation = self.FindName("FloorCheck")

        # manual parameters fill section
        self.section_manual_fill    = self.FindName("FieldFill")
        self.section_project_info   = self.FindName("ProjectInfo")
        self.project_name           = None     # gets filled in manual fill section
        self.project_address        = None     # gets filled in manual fill section
        self._manual_params_ctrl = self.FindName("ManualParamsList")
        self._manual_info_rows      = None
        # row visibility params
        self.type_of_construction   = None
        self.include_type_of_construction_row = None
        self.include_property_area_row = None
        self.include_common_rooms_area_row = None
        self.include_parking_spots_total_area_row = None
        self.include_total_volume_row = None
        # cache prep containers
        self.model_do_cache_prep    = {}
        self.lvl_cache_prep         = {}
        self.model_gen_info         = {}

        self.cache_prep             = {}

        # hide all steps until step 1 confirmed
        self._hide(self.section_b_validation)
        self._hide(self.section_level_validation)
        self._hide(self.section_manual_fill)


    # =================== HELPERS ===================
    def _show(self, el):
        """Make a XAML element visible."""
        el.Visibility = Visibility.Visible
    
    def _hide(self, el):
        """Collapse a XAML element (removes it from layout)."""
        el.Visibility = Visibility.Collapsed
    
    def _validate_fields(self, section):
        """
        Traverse all TextBox elements inside a section, highlight invalid
        ones, and return whether all fields passed validation.
 
        Validation rules:
            - All TextBoxes must be non-empty (__validate_not_empty).
            - TextBoxes with Tag="Float" must also parse as float (__validate_float).
 
        Args:
            section: WPF FrameworkElement whose visual tree to traverse.
 
        Returns:
            bool: True if every TextBox passed all applicable checks.
        """
        all_fields_set = True

        for tb in self._find_xaml_elements(section, TextBox):
            # check and highlight all emty fields, if empty - set all_fields_set to False
            is_filled = self.__validate_not_empty(tb)
            if not is_filled:
                all_fields_set = False

            # run seperate check for heights - parse float
            if tb.Tag == "Float":
                is_valid = self.__validate_float(tb)
                if not is_valid:
                    all_fields_set = False
        
        return all_fields_set
    
    def _find_xaml_elements(self, parent, xaml_el_to_find):
        """
        Recursively collect all elements of a given type in the visual tree.
 
        Uses VisualTreeHelper.GetChildrenCount / GetChild to walk the
        rendered visual tree. This works correctly after the section has
        been made visible and UpdateLayout() has been called.
 
        Args:
            parent:            Root WPF element to search from.
            xaml_el_to_find:   WPF type to collect (e.g. TextBox, ComboBox).
 
        Returns:
            list: All matching elements found in the subtree.
        """
        result = []
        count = VisualTreeHelper.GetChildrenCount(parent)
        for i in range(count):
            child = VisualTreeHelper.GetChild(parent, i)
            if isinstance(child, xaml_el_to_find):
                result.append(child)
            result.extend(self._find_xaml_elements(child, xaml_el_to_find))
        return result

    def __validate_not_empty(self, textbox):
        """
        Validate that a TextBox contains non-empty, non-whitespace text.
 
        Applies visual error styling if invalid, valid styling if valid.
 
        Args:
            textbox: WPF TextBox to check.
 
        Returns:
            bool: True if the field has content.
        """
        text = textbox.Text
        if not text or not text.strip():
            self.__field_error(textbox)
            return False
        self.__field_valid(textbox)
        return True
    
    def __convert_float(self, float_num):
        """
        Convert a string to float, accepting both comma and dot as decimal separator.
 
        Args:
            float_num (str): Numeric string from a TextBox.
 
        Returns:
            float: Parsed value.
 
        Raises:
            ValueError: If the string cannot be parsed as a float.
        """
        return float(float_num.replace(",", "."))

    def __validate_float(self, f_textbox):
        """
        Validate that a TextBox contains a valid float value.
 
        Args:
            f_textbox: WPF TextBox with Tag="Float".
 
        Returns:
            bool: True if the text parses as float.
        """
        try:
            self.__convert_float(f_textbox.Text)
            self.__field_valid(f_textbox)
            return True
        except ValueError as e:
            self.__field_error(f_textbox)
            return False

    def __field_error(self, textbox):
        """Apply error visual styling to a TextBox (red border, misty rose background)."""
        textbox.Background = Brushes.MistyRose
        textbox.BorderBrush = Brushes.Red

    def __field_valid(self, textbox):
        """Restore default visual styling to a TextBox."""
        textbox.Background = Brushes.White
        textbox.BorderBrush = Brushes.Gray


    # =================== DO SELECTOR ===================
    # ── row construction ──────────────────────────────────────────────────

    def _build_rows(self):
        """
        Create DocRowVM instances for all available documents.
 
        For each document, a DocumentParser is instantiated (and stored
        in available_doc_parsers for later reuse), design options are
        parsed, and a DocRowVM is created with "Main model" pre-selected.
 
        Returns:
            ObservableCollection[object]: Ready for ItemsControl.ItemsSource.
        """
        rows = ObservableCollection[object]()
        
        for doc in self.docs:
            parser = DocumentParser(doc, self.project)

            # cache lookup
            is_selected, cached_do = self.cache.model_lookup(get_clean_model_filename(doc))
            
            # add doc parser instance to set for future parse
            self.available_doc_parsers.add(parser)

            doc_do_list = parser.parse_design_options()

            if is_selected:
                do_names = [do.name for do in doc_do_list]
                logger.debug(do_names)
                logger.debug(cached_do)
                if cached_do in do_names:
                    preselected_do_wrapper = doc_do_list[do_names.index(cached_do)]
            else:
                preselected_do_wrapper = None

            rows.Add(DocRowVM(parser, 
                              design_options=doc_do_list, 
                              is_selected=is_selected, 
                              preselected_do=preselected_do_wrapper))

        return rows

    def _populate_list(self):
        """Bind the document row collection to the DocList ItemsControl."""
        self._doc_list_ctrl.ItemsSource = self._rows


    # ── event handlers ────────────────────────────────────────────────────

    def OnCheckChanged(self, sender, e):
        """
        Handle CheckBox Checked/Unchecked in the document list.
 
        Updates DocRowVM.IsSelected which in turn notifies WPF to
        update the IsEnabled binding on the sibling ComboBox.
 
        Args:
            sender: The CheckBox control.
            e:      RoutedEventArgs.
        """
        # sender is the CheckBox — its DataContext is the DocRowVM row
        row = sender.DataContext
        row.IsSelected = bool(sender.IsChecked)
        row._notify("IsSelected")
    
    def OnDesignOptionChanged(self, sender, e):
        """
        Handle design option selection change in the document list.
 
        Updates DocRowVM.SelectedDesignOption with the chosen wrapper.
 
        Args:
            sender: The ComboBox control.
            e:      SelectionChangedEventArgs.
        """
        row = sender.DataContext
        row.SelectedDesignOption = sender.SelectedItem

    def OnConfirmDO(self, sender, e):
        """
        Handle Step 1 confirm button click.
 
        Validates that every selected document has a design option chosen.
        Parses each selected document with the chosen option, building
        BuildingWrapper instances via their DocumentParser.
        Transitions to Step 2 (building number validation).
 
        Args:
            sender: The confirm Button.
            e:      RoutedEventArgs.
        """
        # check if selected model has DO selected:
        combo_box_is_filled = True
        do_combo_boxes = self._find_xaml_elements(self._doc_list_ctrl, ComboBox)
        for do_combo_box in do_combo_boxes:
            model_is_selected = do_combo_box.DataContext.IsSelected

            if (not do_combo_box.SelectedItem) and model_is_selected:
                combo_box_is_filled = False
        
        # if user has chosen model but no DO was chosen
        if not combo_box_is_filled:
            return

        self.usr_do_choice = [
            (row.parser, row.SelectedDesignOption)
            for row in self._rows
            if (row.IsSelected and row.SelectedDesignOption)
        ]
        
        if not self.usr_do_choice:
            return
    
        models = {row.f_name: {"DO": row.SelectedDesignOption.name, "previously_selected": bool(row.IsSelected)} for row in self._rows}
        self.cache_prep["models"] = models
        logger.debug("----- Model and DO cache collected")
        logger.debug(self.cache_prep)

        #self.cache.update_for_models()

        # parse selected files with chosen DO for room elements
        for parser, do_wrapper in self.usr_do_choice:
            parser.set_work_design_option(do_wrapper)
            building = parser.parse()
            self.usr_model_choice.append(building)
        
        # populate step 2 - building number validation
        self._build_b_validation_rows()

        # open new section
        self._hide(self.section_do_select)
        self._show(self.section_b_validation)


    # =============== BUILDING NUMBER VALIDATION ===============

    # ── row construction ──────────────────────────────────────────────────

    def _build_b_validation_rows(self):
        """
        Create BuildingRowVM instances and bind them to the BuildingList.
 
        Pre-fills b_number and b_dev_phase from the majority-vote parsed
        values so the user only needs to correct inconsistencies.
        """
        rows = ObservableCollection[object]()
        for building in self.usr_model_choice:
            rows.Add(BuildingRowVM(building))
        self._b_rows = rows
        self._b_list_ctrl.ItemsSource = rows
    
    # ── event handlers ────────────────────────────────────────────────────

    def OnBNumberChanged(self, sender, e):
        """
        Handle building number TextBox changes.
 
        Validates non-empty and writes the new value to BuildingRowVM.b_number.
 
        Args:
            sender: The TextBox control.
            e:      TextChangedEventArgs.
        """
        if self.__validate_not_empty(sender):
            row = sender.DataContext
            row.b_number = sender.Text

    def OnBDevPhaseChanged(self, sender, e):
        """
        Handle dev phase TextBox changes.
 
        Validates non-empty and writes the new value to BuildingRowVM.b_dev_phase.
 
        Args:
            sender: The TextBox control.
            e:      TextChangedEventArgs.
        """
        if self.__validate_not_empty(sender):
            row = sender.DataContext
            row.b_dev_phase = sender.Text

    def OnConfirmB(self, sender, e):
        """
        Handle Step 2 confirm button click.
 
        Validates all fields are filled. Creates DevelopmentPhaseWrapper
        instances from the user-entered phase IDs, groups buildings into
        their phases, and commits building IDs and phase IDs to each
        BuildingWrapper. Adds DevelopmentPhaseWrappers to project instance.
        Transitions to Step 3 (level check).
 
        Args:
            sender: The confirm Button.
            e:      RoutedEventArgs.
        """
        # highlight all textboxes that are empty or with wrong datatype
        if not self._validate_fields(self._b_list_ctrl):
            logger.debug("Check if all fields are filled!")
            return
        
        # key - dev_phase_if: value - dev_phase_wrapper
        dev_phases = dict()

        for row in self._b_rows:
            row.building.set_building_section_id(row.b_number)

            if not (row.b_dev_phase in dev_phases):
                dev_phase = DevelopmentPhaseWrapper(row.b_dev_phase)
                dev_phases[row.b_dev_phase] = dev_phase
                self.project.add_dev_phase(dev_phase)
            
            # automatically assign dev_phase_wrapper to building wrapper too
            dev_phases[row.b_dev_phase].add_building(row.building)
            row.building.set_building_phase_id(row.b_dev_phase)
        
        self._hide(self.section_b_validation)

        # prepare section for level check
        self._configure_level_check()
    

    # =============== LEVEL DATA CHECK ===============

    def _configure_level_check(self):
        """Populate and show the level-check section (Step 3)."""
        self._populate_level_check_fields()
        self._show(self.section_level_validation)

    def _populate_level_check_fields(self):
        """
        Create ModelVM instances for all parsed buildings and bind to ModelList.
 
        The first model is expanded by default so the user can immediately
        see the level list without an extra click.
        """
        self.model_lvl_list_vms = ObservableCollection[object]()

        for i, model in enumerate(self.usr_model_choice):

            # get cache for model
            cached_b_lvls = self.cache.b_lvl_lookup(get_clean_model_filename(model.doc))
            cached_ground_lvls = self.cache.ground_lvl_lookup(get_clean_model_filename(model.doc))

            vm = ModelVM(model, cached_b_lvls=cached_b_lvls, cached_ground_lvls=cached_ground_lvls)
            vm.is_expanded = (i == 0)   # expand first model level data by default
            self.model_lvl_list_vms.Add(vm)
        
        ctrl = self.FindName("ModelList")
        ctrl.ItemsSource = self.model_lvl_list_vms

    # ── event handlers ────────────────────────────────────────────────────

    def OnModelHeaderClick(self, sender, e):
        """
        Handle mouse click on a model block header to expand or collapse it.
 
        Walks the visual tree from the clicked Grid (sender) up to find
        the LevelBody StackPanel (sibling of the header) and toggles its
        Visibility. Also updates the Arrow TextBlock between ▼ and ▲.
 
        Args:
            sender: The header Grid element.
            e:      MouseButtonEventArgs.
        """
        border = sender.Parent.Parent
        stack = sender.Parent
        level_body = None
        arrow = None

        for child in stack.Children:
            if isinstance(child, StackPanel):
                level_body = child
            # arrow is inside the header Grid — find it by name
        
        # find Arrow TextBlock inside the header Grid (sender)
        for child in sender.Children:
            if isinstance(child, TextBlock) and child.Text in (u"▼", u"▲"):
                arrow = child
                break
 
        if level_body is None:
            return
 
        if level_body.Visibility == Visibility.Collapsed:
            level_body.Visibility = Visibility.Visible
            if arrow:
                arrow.Text = u"▲"
        else:
            level_body.Visibility = Visibility.Collapsed
            if arrow:
                arrow.Text = u"▼"

    def OnPodiumChanged(self, sender, e):
        """
        Handle Цоколь checkbox state change.
 
        Updates LevelVM.is_podium from the checkbox state.
 
        Args:
            sender: The CheckBox control.
            e:      RoutedEventArgs.
        """
        row = sender.DataContext
        if isinstance(row, LevelVM):
            row.is_podium = bool(sender.IsChecked)

    def OnBuildingChanged(self, sender, e):
        """
        Handle Будівельний поверх checkbox state change.
 
        Updates LevelVM.is_building from the checkbox state.
        Levels with is_building=False will be removed during confirm.
 
        Args:
            sender: The CheckBox control.
            e:      RoutedEventArgs.
        """
        row = sender.DataContext
        if isinstance(row, LevelVM):
            row.is_building = bool(sender.IsChecked)

    def OnConfirmFloors(self, sender, e):
        """
        Handle Step 3 confirm button click.
 
        Applies level classification decisions to each BuildingWrapper:
            - is_building == False: remove from building.levels (excluded
              from all floor count calculations).
            - is_podium == True: set floor_type = FloorType.PODIUM on the
              LevelWrapper.
 
        Levels that are building storeys but not podium retain floor_type=None
        and are classified by elevation (above/below 0) by LevelWrapper.
 
        Transitions to Step 4 (manual field fill).
 
        Args:
            sender: The confirm Button.
            e:      RoutedEventArgs.
        """
        for model_vm in self.model_lvl_list_vms:
            building = model_vm.building
            to_remove = []
            cache_b_lvls = set()
            cache_ground_lvls = set()

            for level_vm in model_vm.levels:
                lv = level_vm.wrapper

                # add lvl to cache if is checked
                if level_vm.is_building:
                    cache_b_lvls.add(lv)
 
                if not level_vm.is_building:
                    to_remove.append(lv)
                    continue
 
                if level_vm.is_podium:
                    lv.set_floor_type(FloorType.PODIUM)
                    cache_ground_lvls.add(lv)
 
            for lv in to_remove:
                if lv in building.levels:
                    building.levels.discard(lv)

        #self.cache_prep = {model.model_name: {"building lvls": {lvl.name for lvl in model.building.levels}, 
        #                                        "building ground lvls": {lvl.name for lvl in model.building.levels if lvl.is_podium}} for model in self.model_lvl_list_vms}

        for model in self.model_lvl_list_vms:
            self.cache_prep["models"][get_clean_model_filename(model.doc)]["building lvls"] = [lvl.name for lvl in model.building.levels]
            self.cache_prep["models"][get_clean_model_filename(model.doc)]["building ground lvls"] = [lvl.name for lvl in model.building.levels if lvl.is_podium]

        logger.debug("----- Level cache collected:")
        logger.debug(self.cache_prep)

        self._hide(self.section_level_validation)
        self._configure_manual_data_fields()


    # =============== USER PARAMAETER FILL ===============

    def _configure_manual_data_fields(self):
        """
        Pre-fill project name and address fields from the first building
        with available ProjectInformation data, then show Step 4.
 
        The TypeOfConstruction TextBox is hidden initially and only shown
        when the user checks its inclusion checkbox.
        """
        # prepare section for manual fill
        self.project_name = self.project.name
        self.project_address = self.project.address
        self.FindName("ProjectName").Text = self.project_name
        self.FindName("ProjectAddress").Text = self.project_address

        self._build_manual_field_rows()
        self._show(self.section_manual_fill)
        self._hide(self.FindName("TypeOfConstruction"))

    # ── event handlers ────────────────────────────────────────────────────

    def _build_manual_field_rows(self):
        """
        Create ManualFieldsVM instances for all parsed buildings and bind
        them to the ManualParamsList ItemsControl.
        """
        rows = ObservableCollection[object]()
        for building in self.usr_model_choice:

            # get cached data for building
            cached_manual_field_data = self.cache.manual_data_lookup(get_clean_model_filename(building.doc))
            if cached_manual_field_data:
                cached_height, \
                f_rating, \
                e_class, \
                duration = cached_manual_field_data

                rows.Add(ManualFieldsVM(building, 
                                        cached_height, 
                                        f_rating, 
                                        e_class, 
                                        duration))
            else:
                rows.Add(ManualFieldsVM(building))

        f_con_typ, con_typ, f_prop_a, f_common_a, f_park_a, f_tot_vol = self.cache.manual_data_row_visibility_lookup()

        # helper to set a checkbox and its backing flag
        def restore_checkbox(control_name, flag, attr_name):
            cb = self.FindName(control_name)
            if cb:
                cb.IsChecked = bool(flag)
                setattr(self, attr_name, bool(flag))

        restore_checkbox("ShowPropertyArea", f_prop_a, "include_property_area_row")
        restore_checkbox("ShowCommonArea", f_common_a, "include_common_rooms_area_row")
        restore_checkbox("ShowParkingSpotsArea", f_park_a, "include_parking_spots_total_area_row")
        restore_checkbox("ShowTotalVolume", f_tot_vol, "include_total_volume_row")

        cb = self.FindName("ShowTypeOfConstruction")
        tb = self.FindName("TypeOfConstruction")
        if cb:
            cb.IsChecked = bool(f_con_typ)
            self.include_type_of_construction_row = bool(f_con_typ)
        if tb:
            if f_con_typ and con_typ:
                self._show(tb)
                tb.Text = con_typ
                self.type_of_construction = con_typ
            else:
                self._hide(tb)

        self._manual_info_rows = rows
        self._manual_params_ctrl.ItemsSource = self._manual_info_rows

    def OnProjectNameChanged(self, sender, e):
        """
        Handle project name TextBox changes.
 
        Validates non-empty and updates self.project_name.
 
        Args:
            sender: The ProjectName TextBox.
            e:      TextChangedEventArgs.
        """
        if self.__validate_not_empty(sender):
            if sender.Text != self.project_name:
                self.project_name = sender.Text

    def OnProjectAddressChanged(self, sender, e):
        """
        Handle project address TextBox changes.
 
        Validates non-empty and updates self.project_address.
 
        Args:
            sender: The ProjectAddress TextBox.
            e:      TextChangedEventArgs.
        """
        if self.__validate_not_empty(sender):
            if sender.Text != self.project_address:
                self.project_address = sender.Text

    def OnHeightChanged(self, sender, e):
        """
        Handle max building height TextBox changes.
 
        Validates as float and writes the converted value to
        ManualFieldsVM.height.
 
        Args:
            sender: The height TextBox (Tag="Float").
            e:      TextChangedEventArgs.
        """
        if self.__validate_float(sender):
            row = sender.DataContext
            # safely convert to float
            row.height = self.__convert_float(sender.Text)
    
    def OnFireRatingChanged(self, sender, e):
        """
        Handle fire resistance rating TextBox changes.
 
        Validates non-empty and writes to ManualFieldsVM.fire_rating.
 
        Args:
            sender: The fire rating TextBox.
            e:      TextChangedEventArgs.
        """
        if self.__validate_not_empty(sender):
            row = sender.DataContext
            row.fire_rating = sender.Text
        # row.building.set_fire_resistance_rating(sender.Text)
    
    def OnEnergyClassChanged(self, sender, e):
        """
        Handle energy efficiency class TextBox changes.
 
        Validates non-empty and writes to ManualFieldsVM.energy_class.
 
        Args:
            sender: The energy class TextBox.
            e:      TextChangedEventArgs.
        """
        if self.__validate_not_empty(sender):
            row = sender.DataContext
            row.energy_class = sender.Text
        # row.building.set_energy_efficiency_class(sender.Text)
    
    def OnDurationChanged(self, sender, e):
        """
        Handle construction duration TextBox changes.
 
        Validates as float and writes to ManualFieldsVM.duration.
 
        Args:
            sender: The duration TextBox (Tag="Float").
            e:      TextChangedEventArgs.
        """
        if self.__validate_float(sender):
            row = sender.DataContext
            # safely convert to float
            row.duration = self.__convert_float(sender.Text)
    
    def TypeOfConstructionChanged(self, sender, e):
        """
        Handle type of construction TextBox changes.
 
        Validates non-empty and caches the value in self.type_of_construction.
 
        Args:
            sender: The TypeOfConstruction TextBox.
            e:      TextChangedEventArgs.
        """
        if self.__validate_not_empty(sender):
            self.type_of_construction = str(sender.Text)
    
    def IncludeTypeOfConstructionRow(self, sender, e):
        """
        Handle the type-of-construction inclusion checkbox.
 
        Shows or hides the TypeOfConstruction TextBox and updates the
        inclusion flag for later use by OnConfirmManualData.
 
        Args:
            sender: The CheckBox control.
            e:      RoutedEventArgs.
        """
        if sender.IsChecked:
            self._show(self.FindName("TypeOfConstruction"))
        else:
            self._hide(self.FindName("TypeOfConstruction"))
        self.include_type_of_construction_row = sender.IsChecked
    
    def IncludePropertyAreaRow(self, sender, e):
        """
        Handle the property area inclusion checkbox.
 
        Args:
            sender: The CheckBox control.
            e:      RoutedEventArgs.
        """
        self.include_property_area_row = sender.IsChecked
    
    def IncludeCommonRoomsAreaRow(self, sender, e):
        """
        Handle the common rooms area inclusion checkbox.
 
        Args:
            sender: The CheckBox control.
            e:      RoutedEventArgs.
        """
        self.include_common_rooms_area_row = sender.IsChecked
    
    def IncludeParkingSpotsTotalAreaRow(self, sender, e):
        """
        Handle the parking spots area inclusion checkbox.
 
        Args:
            sender: The CheckBox control.
            e:      RoutedEventArgs.
        """
        self.include_parking_spots_total_area_row = sender.IsChecked

    def IncludeTotalValumeRow(self, sender, e):
        """
        Handle the total building volume inclusion checkbox.
 
        Args:
            sender: The CheckBox control.
            e:      RoutedEventArgs.
        """
        self.include_total_volume_row = sender.IsChecked
    

    def OnConfirmManualData(self, sender, e):
        """
        Handle Step 4 final confirm button click.
 
        Validation sequence:
            1. Validate project info TextBoxes (name, address).
            2. Validate all per-building manual fields.
            3. Check project name and address are not whitespace-only.
            4. If type-of-construction row is included, validate its TextBox.
 
        On success:
            - Set optional row visibility flags on the Table class.
            - Push project name/address to ProjectWrapper.
            - Push height, fire rating, energy class, duration to each
              BuildingWrapper via their setters.
            - Set DialogResult = True and close the form.
 
        Args:
            sender: The confirm Button.
            e:      RoutedEventArgs.
        """
        if not self._validate_fields(self.section_project_info):
            logger.debug("Check if project info fields are filled!")
            return

        # highlight all textboxes that are empty or with wrong datatype
        if not self._validate_fields(self._manual_params_ctrl):
            logger.debug("Check if all fields are filled!")
            return

        # check if project name and address are filled
        if not (self.project_name.strip() and self.project_address.strip()):
            logger.debug("Project name or address or both are not filled in!")
            return
        
        # if type of construction row is set to be visible - check if user set value for that row
        if self.include_type_of_construction_row and not self.__validate_not_empty(self.FindName("TypeOfConstruction")):
            logger.debug("Type of construction row is set to be shown, enter a valid value!")
            return
        
        # set optional rows' visibility
        if self.include_type_of_construction_row:
            # set type of construction data to project
            self.project.set_type_of_construction(self.type_of_construction)
            Table._VIS_TYPE_OF_CONSTRUCTION = True
            logger.debug("Type of construction field is set to visible!")
            self.cache_prep["flag - construction type"] = True
            self.cache_prep["construction type"] = self.type_of_construction
            logger.debug("----- Type of construction is cached")
        
        if self.include_property_area_row:
            Table._VIS_PROPERTY_AREA = True
            logger.debug("Property area field is set to visible!")
            self.cache_prep["flag - property area"] = True
            logger.debug("----- Property area row visibility is cached")

        if self.include_common_rooms_area_row:
            Table._VIS_COMMON_AREA = True
            logger.debug("Common rooms area field is set to visible!")
            self.cache_prep["flag - common area"] = True
            logger.debug("----- Common area row visibility is cached")

        if self.include_parking_spots_total_area_row:
            Table._VIS_TOTAL_PARKING_SPOT_AREA = True
            logger.debug("Total parking spots area field is set to visible!")
            self.cache_prep["flag - parking spots area"] = True
            logger.debug("----- Parking spots area row visibility is cached")

        if self.include_total_volume_row:
            Table._VIS_TOTAL_VOLUME = True
            logger.debug("Total volume field is set to visible!")
            self.cache_prep["flag - total volume"] = True
            logger.debug("----- Total volume row visibility is cached")

        
        # push project name and address to project instance
        if self.project.name != self.project_name:
            self.project.set_usr_name(self.project_name)
            logger.debug("Pushed user set project name to building!")

        if self.project.address != self.project_address:
            self.project.set_usr_address(self.project_address)
            logger.debug("Pushed user set project address to building!")

        # push all data to building instances
        for row in self._manual_info_rows:
            building = row.building
            
            self.cache_prep["models"][get_clean_model_filename(building.doc)]["limit height"] = row.height
            building.set_max_building_height(row.height)
            
            self.cache_prep["models"][get_clean_model_filename(building.doc)]["fire rating"] = row.fire_rating
            building.set_fire_resistance_rating(row.fire_rating)
            
            self.cache_prep["models"][get_clean_model_filename(building.doc)]["energy class"] = row.energy_class
            building.set_energy_efficiency_class(row.energy_class)
            
            self.cache_prep["models"][get_clean_model_filename(building.doc)]["duration"] = row.duration
            building.set_construction_duration(row.duration)

        logger.debug("Pushed all user set data to building instances!")
        logger.debug("----- Cache for manual data is set:")
        logger.debug(self.cache_prep)

        # write updates to cache
        self.cache.update(self.cache_prep)

        # set dialog result to true as the indicator that user didnt exit dialog
        self.DialogResult = True
        self.Close()
        

    # ── public ───────────────────────────────────────────────────────────

    def show(self):
        """
        Display the form modally and return the result.
 
        If the user completes all steps (DialogResult == True), returns
        the populated ProjectWrapper. If the user closes the form early,
        returns False so the caller can skip TEP generation.
 
        Returns:
            ProjectWrapper: Populated project data on success.
            bool (False):   If the user cancelled or closed early.
        """
        # if user fills in all fields in the form,
        # before closing it we set self.DialogResult to True. 
        # If user exits form before the last data fill, do not generate table later on
        dialog_result = self.ShowDialog()

        if dialog_result:
            # all data filled, continue on with table generation
            return self.project
        # user exited form, not all fields are set, do not generate table
        return False