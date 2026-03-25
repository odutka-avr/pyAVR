# -*- coding: utf-8 -*-

import os
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("System")

from pyrevit import forms
from System.Windows import Window, Visibility
from System.IO import StringReader
# from System.Xml import XmlReader
from System.Windows.Markup import XamlReader
from System.Collections.ObjectModel import ObservableCollection
from System.ComponentModel import INotifyPropertyChanged
from System.Windows import DependencyObject
from System.Windows.Media import Brushes
from System.Windows.Media import VisualTreeHelper
from System.Windows.Controls import TextBox


from parsers import DocumentParser, DesignOptionWrapper

XAML_PATH = os.path.join(os.path.dirname(__file__), "step1.xaml")


# =========================================================================
# VIEW-MODEL
# =========================================================================

class DocRowVM(INotifyPropertyChanged):
    """
    One row in the document list.
    Bound to DataTemplate in step1.xaml.
    """

    def __init__(self, parser, design_options):
        self._is_selected       = False
        self._property_changed_handler = None
        self.Name               = parser.model_name
        self.Doc                = parser.doc
        self.parser             = parser
        self.DesignOptions      = ObservableCollection[object]()
        self.SelectedDesignOption = None

        for do in design_options:
            self.DesignOptions.Add(do)
        
    def add_PropertyChanged(self, handler):
        self._property_changed_handler = handler

    def remove_PropertyChanged(self, handler):
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
        from System.ComponentModel import PropertyChangedEventArgs
        self._property_changed_handler(self, PropertyChangedEventArgs(prop_name))


class BuildingRowVM(INotifyPropertyChanged):
    def __init__(self, building):
        self.building = building
        self.name = building.doc.Title

        # prefilled from parsed param values
        self.b_number = building.parsed_building_section_id
        self.b_dev_phase = building.parsed_building_phase_id

        self._property_changed_handler = None

    def add_PropertyChanged(self, handler):
        self._property_changed_handler = handler

    def remove_PropertyChanged(self, handler):
        self._property_changed_handler = None

class ManualFieldsVM(INotifyPropertyChanged):
    def __init__(self, building):
        self.building = building

        self.building_id = building.building_section_id
        self.dev_phase_id = building.building_phase_id
        
        self.height = None
        self.fire_rating = None
        self.energy_class = None
        self.duration = None

        self._property_changed_handler = None

    def add_PropertyChanged(self, handler):
        self._property_changed_handler = handler

    def remove_PropertyChanged(self, handler):
        self._property_changed_handler = None

    @property
    def all_properties_set(self):
        """Return true if all properties are not None or empty str"""
        return self.height and self.fire_rating and self.energy_class and self.duration


# =========================================================================
# FORM CONTROLLER
# =========================================================================

class Form(forms.WPFWindow):
    """
    Shows step 1 of the TEP wizard: document + design option selection.

    After the user confirms, form.result contains:
        list of (doc, DesignOptionWrapper) tuples for selected documents.
    """

    def __init__(self, docs, project):
        forms.WPFWindow.__init__(self, XAML_PATH)

        self.project                = project
        self.docs                   = docs # list[RevitLinkInstance]
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

        # manual parameters fill section
        self.section_manual_fill    = self.FindName("FieldFill")
        self.project_name           = None     # gets filled in manual fill section
        self.project_address        = None     # gets filled in manual fill section
        # self._b_height_ctrl         = self.FindName("HeightList")
        # self._b_fire_rating_ctrl    = self.FindName("FireRatingList")
        # self._b_energy_class_ctrl   = self.FindName("EnergyClassList")
        # self._b_duration_ctrl       = self.FindName("DurationList")
        self._manual_params_ctrl = self.FindName("ManualParamsList")
        self._manual_info_rows      = None

        # hide all steps until step 1 confirmed
        self._hide(self.section_b_validation)
        self._hide(self.section_manual_fill)


    # =================== HELPERS ===================
    def _show(self, el): 
        el.Visibility = Visibility.Visible
    
    def _hide(self, el): 
        el.Visibility = Visibility.Collapsed
    
    def _validate_fields(self):
        all_fields_set = True

        for tb in self._find_textboxes(self._manual_params_ctrl):
            # check and highlight all emty fields, if empty - set all_fields_set to False
            is_filled = self.__validate_not_empty(tb)
            if not is_filled:
                all_fields_set = False

            # run seperate check for heights - parse float
            if tb.Tag == "Height":
                is_valid = self.__validate_height(tb)
                if not is_valid:
                    all_fields_set = False

        # validate project name and address fields
        if not (self.__validate_not_empty(self.FindName("ProjectName")) and 
                self.__validate_not_empty(self.FindName("ProjectAddress"))):
            all_fields_set = False
        
        return all_fields_set
        
    
    def _find_textboxes(self, parent):
        result = []
        count = VisualTreeHelper.GetChildrenCount(parent)
        for i in range(count):
            child = VisualTreeHelper.GetChild(parent, i)
            if isinstance(child, TextBox):
                result.append(child)
            result.extend(self._find_textboxes(child))
        return result

    def __validate_not_empty(self, textbox):
        text = textbox.Text
        if not text or not text.strip():
            self.__field_error(textbox)
            return False
        else:
            self.__field_valid(textbox)
            return True
    
    def __validate_height(self, h_textbox):
        try:
            float(h_textbox.Text.replace(",", "."))
            self.__field_valid(h_textbox)
            return True
        except ValueError:
            self.__field_error(h_textbox)
            return False

    def __field_error(self, textbox):
        textbox.Background = Brushes.MistyRose
        textbox.BorderBrush = Brushes.Red

    
    def __field_valid(self, textbox):
        textbox.Background = Brushes.White
        textbox.BorderBrush = Brushes.Gray


    # =================== DO SELECTOR ===================
    # ── row construction ──────────────────────────────────────────────────

    def _build_rows(self):
        rows = ObservableCollection[object]()
        
        for doc in self.docs:
            parser = DocumentParser(doc, self.project)
            
            # add doc parser instance to set for future parse
            self.available_doc_parsers.add(parser)

            doc_do_list = parser.parse_design_options()
            rows.Add(DocRowVM(parser, design_options=doc_do_list))

        return rows

    def _populate_list(self):
        self._doc_list_ctrl.ItemsSource = self._rows

    # ── event handlers ────────────────────────────────────────────────────

    def OnCheckChanged(self, sender, e):
        # sender is the CheckBox — its DataContext is the DocRowVM row
        row = sender.DataContext
        row.IsSelected = sender.IsChecked
    
    def OnDesignOptionChanged(self, sender, e):
        row = sender.DataContext
        row.SelectedDesignOption = sender.SelectedItem

    def OnConfirmDO(self, sender, e):
        self.usr_do_choice = [
            (row.parser, row.SelectedDesignOption)
            for row in self._rows
            if row.IsSelected
        ]
        
        if not self.usr_do_choice:
            return
        

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
        rows = ObservableCollection[object]()
        for building in self.usr_model_choice:
            rows.Add(BuildingRowVM(building))
        self._b_rows = rows
        self._b_list_ctrl.ItemsSource = rows
    
    # ── event handlers ────────────────────────────────────────────────────

    def OnBNumberChanged(self, sender, e):
        row = sender.DataContext
        row.b_number = sender.Text

    def OnBDevPhaseChanged(self, sender, e):
        row = sender.DataContext
        row.b_dev_phase = sender.Text

    def OnConfirmB(self, sender, e):
        for row in self._b_rows:
            row.building.set_building_section_id(row.b_number)
            row.building.set_building_phase_id(row.b_dev_phase)
        
        self._hide(self.section_b_validation)

        # prepare section for manual fill
        self.project_name = self.project.name
        self.project_address = self.project.address



        self.FindName("ProjectName").Text = self.project_name
        self.FindName("ProjectAddress").Text = self.project_address

        print(self.FindName("ProjectName"))
        print(self.FindName("ProjectAddress"))

        self._build_manual_field_rows()
        self._show(self.section_manual_fill)
    

    # =============== USER PARAMAETER FILL ===============

    # ── event handlers ────────────────────────────────────────────────────

    def _build_manual_field_rows(self):
        rows = ObservableCollection[object]()
        for building in self.usr_model_choice:
            rows.Add(ManualFieldsVM(building))
        self._manual_info_rows = rows
        self._manual_params_ctrl.ItemsSource = self._manual_info_rows
        # self._b_height_ctrl.ItemsSource = rows
        # self._b_fire_rating_ctrl.ItemsSource = rows
        # self._b_energy_class_ctrl.ItemsSource = rows
        # self._b_duration_ctrl.ItemsSource = rows

    def OnProjectNameChanged(self, sender, e):
        if self.__validate_not_empty(sender):
            if sender.Text != self.project_name:
                self.project_name = sender.Text

    def OnProjectAddressChanged(self, sender, e):
        if self.__validate_not_empty(sender):
            if sender.Text != self.project_address:
                self.project_address = sender.Text

    def OnHeightChanged(self, sender, e):
        if self.__validate_height(sender):
            row = sender.DataContext
            row.height = sender.Text
        # row.building.set_max_building_height(row.height)
    
    def OnFireRatingChanged(self, sender, e):
        if self.__validate_not_empty(sender):
            row = sender.DataContext
            row.fire_rating = sender.Text
        # row.building.set_fire_resistance_rating(sender.Text)
    
    def OnEnergyClassChanged(self, sender, e):
        if self.__validate_not_empty(sender):
            row = sender.DataContext
            row.energy_class = sender.Text
        # row.building.set_energy_efficiency_class(sender.Text)
    
    def OnDurationChanged(self, sender, e):
        if self.__validate_not_empty(sender):
            row = sender.DataContext
            row.duration = sender.Text
        # row.building.set_construction_duration(sender.Text)
    
    def OnConfirmManualData(self, sender, e):
        # highlight all textboxes that are empty or with wrong datatype
        if not self._validate_fields():
            print("Check if all fields are filled!")
            return

        # check if project name and address are filled
        if not (self.project_name.strip() and self.project_address.strip()):
            print("Project name or address or both are not filled in!")
            return

        # check if all properties are set, if not - exit
        # for row in self._manual_info_rows:
        #     if not row.all_properties_set:
        #         print("Check if all properties are set!")
        #         return
        
        # push project name and address to project instance
        if self.project.name != self.project_name:
            self.project.set_usr_name(self.project_name)
            print("Pushed user set project name to building!")

        if self.project.address != self.project_address:
            self.project.set_usr_address(self.project_address)
            print("Pushed user set project address to building!")

        # push all data to building instances
        for row in self._manual_info_rows:
            building = row.building
            building.set_max_building_height(row.height)
            building.set_fire_resistance_rating(row.fire_rating)
            building.set_energy_efficiency_class(row.energy_class)
            building.set_construction_duration(row.duration)
        
        print("Pushed all user set data to building instances!")
        

    # ── public ───────────────────────────────────────────────────────────

    def show(self):
        """
        Show the form modally.
        Returns list[(doc, DesignOptionWrapper)] or None if cancelled.
        """
        self.ShowDialog()
        return 