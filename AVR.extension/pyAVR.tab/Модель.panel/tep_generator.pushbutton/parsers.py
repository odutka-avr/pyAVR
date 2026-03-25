# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (FilteredElementCollector, 
                               BuiltInCategory,
                               BuiltInParameter, 
                               Level, 
                               DesignOption,
                               RevitLinkInstance,
                               PropertyLine)

# local custom imports
from wrappers import (LevelWrapper, 
                      AreaWrapper,
                      RoomWrapper,
                      ApartmentWrapper,
                      BuildingWrapper,
                      DevelopmentPhaseWrapper,
                      ProjectWrapper)

from shared_parameters import Shared_parameters
from value_conversion import convert_sq_feet_to_sq_m
from enums import RoomCategory

# ========================================================================


class DesignOptionWrapper:
    def __init__(self, do_el):
        self.do_el = do_el
    
    @property
    def name(self):
        if self.do_el:
            return self.do_el.Name
        return "Main model"

    def __str__(self):
        return "DesignOptionWrapper({})".format(self.name)
 
    def __repr__(self):
        return self.__str__()


# ========================================================================


DEPARTMENT_ROUTING = {
    "МЗК":                              "common_rooms",
    "Місце загального користування":    "common_rooms",
    "Технічне приміщення":              "common_rooms",
    "Технічні приміщення":              "common_rooms",
    "Трансформаторна підстанція":       "common_rooms",
    "Офіс":                             "office_rooms",
    "ЗДО":                              "educational_rooms",
    "Комерція":                         "commerce_rooms",
    "Укриття":                          "shelter_rooms",
    "Проїзд":                           "driveway_rooms",
    "Машино-місце":                     "parking_space_rooms",
    "Проїзд/укриття":                   "",
    "Машино-місце/укриття":             "",
}

RESIDENTIAL_CATEGORY = "Житло"

# ========================================================================



class DocumentParser:
    """
    Parses a single Revit document (current or linked) into a BuildingWrapper.
 
    Usage:
        parser = DocumentParser(doc)
        building = parser.parse()
    """
 
    def __init__(self, doc, project):
        """
        doc           — the Document to parse (current or from link.GetLinkDocument())
        link_instance — the RevitLinkInstance if this is a linked model, else None
        """
        self.project       = project
        self.doc           = doc
        self.model_name    = doc.Title
        self.do_to_parse   = None

        self.untracked_rooms = list()
        self.untracked_cat_depts = list()

        self.building_number = None
        self.development_phase_id = None
 
    # ── public entry point ────────────────────────────────────────────────
 
    def parse(self):
        """
        Full parse: levels → rooms → areas.
        Returns a populated BuildingWrapper.
        """
        building = BuildingWrapper(self.doc)
        self.project.add_building(building)
 
        self._parse_levels(building)
        self._parse_rooms(building)
        self._parse_areas(building)
        self._parse_property_lines(building)
  
        return building
 
    # ── levels ────────────────────────────────────────────────────────────
 
    def _parse_levels(self, building):
        """
        Collect all Level elements, wrap them, add to building.
        floor_type is left as None — must be set later via the UI dialog.
        """
        level_els = (
            FilteredElementCollector(self.doc)
            .OfClass(Level)
            .ToElements()
        )
        
        lvl_el_lst = list()
        for lvl_el in level_els:
            lvl_el_lst.append(lvl_el)

        # sort by elevation
        lvl_el_lst.sort(key=lambda lvl: lvl.Elevation)
        
        # get only building levels
        b_levels_filtered = list(filter(lambda lvl: 
                                   lvl.get_Parameter(BuiltInParameter.LEVEL_IS_BUILDING_STORY).AsInteger() == 1,
                                   lvl_el_lst))
        
        num_of_lvl = len(b_levels_filtered)
        
        # helper param
        next_lvl = None

        for i in range(num_of_lvl):
            # gets executed in the 1st iteration when there is no next lvl instance
            if next_lvl is None:
                curr_lvl = LevelWrapper(b_levels_filtered[i])
            # executed every next iteration after 1st 
            # as the wrapper for the next lvl was created
            else:
                curr_lvl = next_lvl
            
            building.add_level(curr_lvl)
            curr_lvl.set_building(building)

            # check if there is next lvl available
            # if next_i == num of elems -> its the last iteration, loop breaks after this if-clause
            next_i = i + 1
            if next_i < num_of_lvl:
                next_lvl = LevelWrapper(b_levels_filtered[next_i])
                curr_lvl.next_level = next_lvl

 
    # ── level lookup helper ───────────────────────────────────────────────
 
    def _level_map(self, building):
        """
        Returns dict {Revit ElementId integer → LevelWrapper}
        for fast lookup when linking rooms/areas to their level.
        """
        return {lv.level_el.Id.IntegerValue: lv for lv in building.levels}
 
    # ── rooms ─────────────────────────────────────────────────────────────
    
    def _parse_rooms(self, building):
        """
        Collect all placed rooms, wrap them, route to apartment or
        to the appropriate non-residential set on the building.
        """
        level_map   = self._level_map(building)
        apartments  = {}   # apt_number (str) → ApartmentWrapper
 
        room_els = (
            FilteredElementCollector(self.doc)
            .OfCategory(BuiltInCategory.OST_Rooms)
            .WhereElementIsNotElementType()
            .ToElements()
        )
 
        for room in room_els:
            # skip unplaced rooms (area == 0)
            if room.Area == 0:
                continue
            
            # get DO name
            room_do = room.DesignOption
            if room_do:
                room_do_name = room_do.Name
            else:
                room_do_name = "Main model"

            # filter by user-chosen DO
            if room_do_name == self.do_to_parse.name:
                r_wrapper = RoomWrapper(room)
    
                # ── link to level wrapper ─────────────────────────────────
                level_id = room.LevelId
                if level_id is not None:
                    lv = level_map.get(level_id.IntegerValue)
                    if lv:
                        r_wrapper.set_level(lv)
    
                # ── route by category ──────────────────────────────────────
                category = r_wrapper.room_category   # reads SharedParam from el
                department = r_wrapper.room_department

                # add room to building room collector
                building.add_room(r_wrapper)

                # add to appartment if room belongs to Житлова category
                if category == RESIDENTIAL_CATEGORY:
                    self._add_room_to_apartment(r_wrapper, apartments)
                else:
                    # dept = r_wrapper.room_department
                    # target_attr = DEPARTMENT_ROUTING.get(dept)
                    attr_name = RoomCategory.route(category)
                    if attr_name:
                        getattr(building, attr_name).add(r_wrapper)
                    else:
                        self.untracked_rooms.append(r_wrapper)
                        self.untracked_cat_depts.append((category, department))

        # ── commit apartments to building ──────────────────────────────
        for apt in apartments.values():
            building.add_apartment(apt)
 

    def _add_room_to_apartment(self, room_wrapper, apartments):
        """
        Read AVR_Номер квартири from the room, find or create the
        ApartmentWrapper, and add the room to it.
        """
        apt_param = room_wrapper.room_el.get_Parameter(
            Shared_parameters.APARTMENT_NUMBER
        )
        if apt_param is None or not apt_param.HasValue:
            # residential room with no apartment number — skip silently
            # (could be a balcony template room or stray room)
            return
 
        apt_number = apt_param.AsString()
        if not apt_number:
            return
 
        if apt_number not in apartments:
            apartments[apt_number] = ApartmentWrapper(apt_number)
 
        apartments[apt_number].add_room(room_wrapper)
    
 
    # ── areas ─────────────────────────────────────────────────────────────
 
    def _parse_areas(self, building):
        """
        Collect all placed Area elements, wrap them, link to level.
        Only areas with area > 0 are included (unplaced areas ignored).
        """
        level_map = self._level_map(building)
 
        area_els = (
            FilteredElementCollector(self.doc)
            .OfCategory(BuiltInCategory.OST_Areas)
            .WhereElementIsNotElementType()
            .ToElements()
        )
 
        for el in area_els:
            if el.Area == 0:
                continue
 
            wrapper = AreaWrapper(el)
 
            level_id = el.LevelId
            if level_id is not None:
                lv = level_map.get(level_id.IntegerValue)
                if lv:
                    wrapper.set_level(lv)
 
            building.add_area(wrapper)
    
    # ── property lines ────────────────────────────────────────────────────

    def _parse_property_lines(self, building):
        """
        """
        pr_outline = (
            FilteredElementCollector(self.doc)
            .OfClass(PropertyLine)
            .ToElements()
        )

        for outline in pr_outline:
            area = outline.get_Parameter(BuiltInParameter.PROPERTY_AREA).AsDouble()
            # if outline is not closed loop, its area == -1
            if area > 0:
                building.add_property_outine(outline)
        
 
    # ── design options ────────────────────────────────────────────────────
 
    def parse_design_options(self):
        """
        Returns list[DesignOptionWrapper] for all design options in this doc.
        Empty list if the model has no design options.
        """
        do_els = (
            FilteredElementCollector(self.doc)
            .OfClass(DesignOption)
            .ToElements()
        )

        do_wrappers = list()

        # create Main model DO manualy (Main model cant be parsed)
        main_model = DesignOptionWrapper(None)
        do_wrappers.append(main_model)
        
        for do in do_els:
            do_wrappers.append(DesignOptionWrapper(do))

        return do_wrappers
    

    def set_work_design_option(self, do_wrapper):
        self.do_to_parse = do_wrapper
 
 
# ========================================================================
