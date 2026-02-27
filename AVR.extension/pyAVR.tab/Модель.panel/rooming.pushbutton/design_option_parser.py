# -*- coding: utf-8 -*-

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory, ElementCategoryFilter


class DO_set_wrapper:
    """
    Design option set wrapper, for quick access to do set info

    :type design_option_set: Autodesk.Revit.DB.Element
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
        Add dependent design option to list of all dependent design options that belong to this set
        
        :type do_el: DO_wrapper
        """
        self.do_elems.append(do_el)
    
    def __str__(self):
        return "DO_set_wrapper: {}, options: [{}]".format(self.name, ", ".join(self.do_elems))
    
    def __repr__(self):
        return self.__str__()


class DO_wrapper:
    """
    Design option wrapper, for quick access to info

    :type design_option_el: Autodesk.Revit.DB.Element
    :type design_option_set: Autodesk.Revit.DB.Element
    """
    def __init__(self, design_option_el, design_option_set):
        self.do_el = design_option_el
        if design_option_el:
            self.name = design_option_el.Name.replace("<primary>", "").strip()
        else:
            self.name = "Main model"
        self.do_set = design_option_set
    
    def __str__(self):
        return "DO_wrapper: {}".format(self.name)
    
    def __repr__(self):
        return self.__str__()
        

class GetDesignOptions:
    def __init__(self, doc):
        self.doc = doc
        self.design_option_sets = self._parse_design_option_sets()
        self.design_option_data = self._get_all_design_options()
        self.defauilt_template_do_set_name = "Матеріали шаблону"
    
    def _parse_design_option_sets(self):
        """
        -> return list of available design option set elements

        :rtype: Autodesk.Revit.DB.FilteredElementCollector
        """
        return FilteredElementCollector(self.doc).OfCategory(BuiltInCategory.OST_DesignOptionSets)
    
    def _get_all_design_options(self):
        """
        Iterate through available design option sets, get every DO from each set
        -> return list([DO_set_wrapper] elements) - each DO_set_wrapper element has list of its DOs

        :rtype: list
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
        Set default DO set name that is predefined in .rte file.
        By default = "Приклад моделювання елементів"

        :type default_set_name: str
        """
        self.defauilt_template_design_option_name = default_set_name
    
    def get_fortmatted_do_data(self):
        """
        :rtype: dict
        """
        do_data_dict = dict()

        for do_set in self.design_option_data:
            for do in do_set.do_elems:
                do_data_dict[do.name] = (do.do_set, do)
        
        return do_data_dict

