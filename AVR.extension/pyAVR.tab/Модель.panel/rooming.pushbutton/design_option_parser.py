# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================
import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory, ElementCategoryFilter

# ========================================================================


class DO_set_wrapper:
    """
    Wraps a Revit Design Option Set element for convenient access to its
    name and dependent Design Option elements.

    Can represent either a real Design Option Set from the model, or a
    virtual "Main model" entry (when main_model_flag=True), which has no
    corresponding Revit element but is treated as a valid option throughout
    the script.

    Args:
        design_option_set (Element): Revit DesignOptionSet element. Can be
                                     None if main_model_flag is True.
        main_model_flag   (bool):    If True, creates a virtual Main model
                                     entry with a single DO_wrapper(None).
    """
    def __init__(self, design_option_set=None, main_model_flag=False):
        self.do_set = design_option_set
        if main_model_flag:
            self.name = "Main model"
            self.do_elems = [DO_wrapper(None, self)]
        else:
            self.name = design_option_set.Name
            self.do_elems = list()
    
    def append_do_el(self, do_el):
        """
        Appends a DO_wrapper to this set's list of dependent design options.
        Called during model parsing for each DO belonging to this set.

        Args:
            do_el (DO_wrapper): Wrapped design option to add.
        """
        self.do_elems.append(do_el)
    
    def __str__(self):
        return "DO_set_wrapper: {}, options: [{}]".format(self.name, ", ".join(self.do_elems))
    
    def __repr__(self):
        return self.__str__()


class DO_wrapper:
    """
    Wraps a single Revit Design Option element for convenient access to
    its name and parent set reference.

    Can represent either a real Design Option element or the virtual
    "Main model" option (when design_option_el is None).

    Args:
        design_option_el  (Element):       Revit DesignOption element, or
                                           None for Main model.
        design_option_set (DO_set_wrapper): Parent set this option belongs to.
    """
    def __init__(self, design_option_el, design_option_set):
        self.do_el = design_option_el
        if design_option_el:
            self.name = design_option_el.Name
        else:
            self.name = "Main model"
        self.do_set = design_option_set
    
    def __str__(self):
        return "DO_wrapper: {}".format(self.name)
    
    def __repr__(self):
        return self.__str__()
        

class GetDesignOptions:
    """
    Parses all Design Option Sets and their dependent Design Options from
    the Revit document and exposes them in structured form for use in
    the input form.

    Always appends a virtual "Main model" entry so rooms modeled outside
    any Design Option can be targeted consistently.

    After init, design_option_data contains a list of DO_set_wrapper
    instances, each holding its dependent DO_wrapper instances.

    Args:
        doc: Revit DBDocument (revit.doc)
    """
    def __init__(self, doc):
        self.doc = doc
        self.design_option_sets = self._parse_design_option_sets()
        self.design_option_data = self._get_all_design_options()
        self.defauilt_template_do_set_name = "Матеріали шаблону"
    
    def _parse_design_option_sets(self):
        """
        Collects all DesignOptionSet elements from the model.

        Returns:
            FilteredElementCollector: Iterable of DesignOptionSet elements.
        """
        return FilteredElementCollector(self.doc).OfCategory(BuiltInCategory.OST_DesignOptionSets)
    
    def _get_all_design_options(self):
        """
        Iterates through all Design Option Sets, wraps each in a
        DO_set_wrapper, and populates it with its dependent DO_wrapper
        instances. Appends a virtual Main model entry at the end.

        Returns:
            list[DO_set_wrapper]: All sets including Main model.
        """
        design_option_info = list()
        do_filter = ElementCategoryFilter(BuiltInCategory.OST_DesignOptions)
        
        for do_set in self.design_option_sets:
            
            do_set_el = DO_set_wrapper(do_set)
            do_set_elems = do_set.GetDependentElements(do_filter)

            for do_id in do_set_elems:
                do_el = self.doc.GetElement(do_id)

                if do_el:
                    do_el = DO_wrapper(self.doc.GetElement(do_id), do_set_el)
                    do_set_el.append_do_el(do_el)
            
            design_option_info.append(do_set_el)
        
        # add Main model to available options
        design_option_info.append(DO_set_wrapper(main_model_flag=True))

        return design_option_info
    
    def set_default_template_do_set_name(self, default_set_name):
        """
        Overrides the default template DO set name used to identify and
        exclude the project template's built-in option set.
        Defaults to "Матеріали шаблону" if not called.

        Args:
            default_set_name (str): Name of the template DO set to exclude.

        =================================================================
        #### CURENTLY ALL DESIGN OPTIONS ARE SHOWN WITHOUT EXCEPTIONS
        - FOR POSSIBLE FUTURE USE
        =================================================================
        """
        self.defauilt_template_design_option_name = default_set_name
    
    def get_fortmatted_do_data(self):
        """
        Flattens all DO sets and their options into a single dict keyed
        by DO name, for direct use as ComboBox ItemsSource in the form.

        Returns:
            dict[str, tuple]: { do_name: (DO_set_wrapper, DO_wrapper) }
        """
        do_data_dict = dict()

        for do_set in self.design_option_data:
            for do in do_set.do_elems:
                do_data_dict[do.name] = (do.do_set, do)
        
        return do_data_dict

