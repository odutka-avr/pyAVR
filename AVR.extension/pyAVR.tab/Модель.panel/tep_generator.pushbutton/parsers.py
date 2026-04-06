# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (FilteredElementCollector, 
                               BuiltInCategory,
                               BuiltInParameter, 
                               Level, 
                               DesignOption,
                               PropertyLine)

# local custom imports
from wrappers import (LevelWrapper, 
                      AreaWrapper,
                      RoomWrapper,
                      ApartmentWrapper,
                      BuildingWrapper)

from shared_parameters import Shared_parameters
from enums import RoomCategories

# ========================================================================


class DesignOptionWrapper:
    """
    Wraps a Revit DesignOption element or represents the Main Model.
 
    The Main Model is not a real Revit element — it is created manually
    with do_el=None and returns the sentinel name "Main model".
    This lets the UI treat the Main Model and real design options
    uniformly in the same dropdown list.
 
    Attributes:
        do_el: The raw Autodesk.Revit.DB.DesignOption element,
               or None for the Main Model sentinel.
    """
    def __init__(self, do_el):
        """
        Args:
            do_el: Autodesk.Revit.DB.DesignOption element, or None
                   to create the Main Model sentinel.
        """
        self.do_el = do_el
    
    @property
    def name(self):
        """
        Design option name string.
 
        Returns:
            str: DesignOption.Name, or "Main model" for the sentinel.
        """
        if self.do_el:
            return self.do_el.Name
        return "Main model"

    def __str__(self):
        return "DesignOptionWrapper({})".format(self.name)
 
    def __repr__(self):
        return self.__str__()


# ========================================================================
# ========================================================================



class DocumentParser:
    """
    Parses a single Revit document into a populated BuildingWrapper.
 
    Handles level ordering, room routing by category and department,
    apartment grouping, area plan collection, and property line parsing.
 
    Must be configured with a design option via set_work_design_option()
    before calling parse(). Rooms are filtered to include only those
    belonging to the selected design option (or the Main Model).
 
    Typical usage::
 
        parser = DocumentParser(doc, project)
        dos = parser.parse_design_options()
        parser.set_work_design_option(chosen_do_wrapper)
        building = parser.parse()
 
    Attributes:
        project (ProjectWrapper):  Project the parsed building is added to.
        doc:                       Autodesk.Revit.DB.Document being parsed.
        model_name (str):          Document title, used as display name.
        do_to_parse:               DesignOptionWrapper set by the user.
                                   None until set_work_design_option() called.
        untracked_rooms (list):    RoomWrappers whose category/department
                                   did not match any routing rule.
                                   Useful for debugging missing room data.
        untracked_cat_depts (list): Parallel list of (category, department)
                                   tuples for untracked_rooms entries.
    """
 
    def __init__(self, doc, project):
        """
        Args:
            doc:     Autodesk.Revit.DB.Document to parse.
            project: ProjectWrapper that the resulting BuildingWrapper
                     will be registered with.
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
        Execute the full parse sequence for this document.
 
        Parse order: levels -> rooms -> areas -> property lines.
        Registers the resulting BuildingWrapper with the ProjectWrapper.
 
        Returns:
            BuildingWrapper: Fully populated building data container.
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
        Collect, filter, sort, and link all building-story Level elements.
 
        Only levels with LEVEL_IS_BUILDING_STORY == 1 are included.
        Levels are sorted by ascending elevation then linked into a
        singly-linked list via LevelWrapper.next_level so the volume
        calculation can determine floor-to-floor heights without a
        separate lookup.
 
        The linking algorithm creates each LevelWrapper exactly once:
        on the first iteration the wrapper is created fresh; on each
        subsequent iteration it reuses the wrapper already created as
        next_level in the previous pass, avoiding duplicate wrapping.
 
        Args:
            building (BuildingWrapper): Target building to add levels to.
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
        Build an ElementId integer -> LevelWrapper lookup dictionary.
 
        Built once per parse and reused by _parse_rooms and _parse_areas
        to avoid iterating the level set for every element.
 
        Args:
            building (BuildingWrapper): Source of level wrappers.
 
        Returns:
            dict[int, LevelWrapper]: Keys are Revit ElementId integers.
        """
        return {lv.level_el.Id.IntegerValue: lv for lv in building.levels}
 
    # ── rooms ─────────────────────────────────────────────────────────────
    
    def _parse_rooms(self, building):
        """
        Collect all placed rooms, filter by design option, and route
        each to its appropriate destination on BuildingWrapper.
 
        Routing logic:
            1. Skip unplaced rooms (area == 0).
            2. Skip rooms not in the selected design option.
            3. Link each room to its LevelWrapper via the level map.
            4. Add to building.rooms for total_room_area.
            5. Call RoomCategories.route_categories(category):
               - RESIDENCE  -> _add_room_to_apartment()
               - Other known category -> route_department() -> building sub-set
               - Mixed-usage shelter flag -> also add to mixed_usage_shelter_rooms
               - Unrecognised -> append to untracked_rooms
            6. Commit all apartments to the building after all rooms processed.
 
        Args:
            building (BuildingWrapper): Target building to populate.
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
                # if category == RESIDENTIAL_CATEGORY:
                #     self._add_room_to_apartment(r_wrapper, apartments)
                # else:

                valid_category = RoomCategories.route_categories(category)
                
                if valid_category == RoomCategories.RESIDENCE:
                    self._add_room_to_apartment(r_wrapper, apartments)

                elif valid_category:
                    attr_name = valid_category.route_department(department)

                    if attr_name:
                        getattr(building, attr_name).add(r_wrapper)
                        
                        # check if room is mixed usage shelter room
                        if r_wrapper.is_mixed_usage_shelter:
                            building.mixed_usage_shelter_rooms.add(r_wrapper)

                    else:
                        self.untracked_rooms.append(r_wrapper)
                        self.untracked_cat_depts.append((category, department))
                
                else:
                    self.untracked_rooms.append(r_wrapper)
                    self.untracked_cat_depts.append((category, department))

        # ── commit apartments to building ──────────────────────────────
        for apt in apartments.values():
            building.add_apartment(apt)
 

    def _add_room_to_apartment(self, room_wrapper, apartments):
        """
        Read the apartment number and add the room to its ApartmentWrapper.
 
        Creates a new ApartmentWrapper if this number has not been seen.
        Rooms with a missing or empty AVR_Номер квартири are silently
        skipped.
 
        Args:
            room_wrapper (RoomWrapper):           Residential room to group.
            apartments (dict[str, ApartmentWrapper]): Working dict keyed
                by apartment number string. Modified in-place.
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
        Collect all placed Area elements and link each to its LevelWrapper.
 
        Unplaced areas (area == 0) are skipped. Area scheme name is stored
        on each AreaWrapper and used later by BuildingWrapper methods to
        filter areas by scheme (e.g. "Загальна площа будинку").
 
        Args:
            building (BuildingWrapper): Target building to populate.
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
        Collect all closed PropertyLine elements from the document.
 
        Open (unclosed) property lines have PROPERTY_AREA == -1 and
        are excluded. Elements are stored directly on
        BuildingWrapper.property_outlines and read lazily by the
        property_area computed property for TEP item 4.
 
        Args:
            building (BuildingWrapper): Target building to populate.
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
        Return all design options in this document plus the Main Model.
 
        The Main Model sentinel is always prepended so it appears first
        in the UI dropdown and can be pre-selected by default.
 
        Returns:
            list[DesignOptionWrapper]: Main Model first, then all real
            design options in collector order.
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
        """
        Set the design option that rooms are filtered against during parse().
 
        Must be called before parse(). Rooms whose DesignOption.Name
        does not match do_wrapper.name are skipped entirely.
 
        Args:
            do_wrapper (DesignOptionWrapper): The user-selected option.
                Pass the Main Model sentinel to include all non-DO rooms.
        """
        self.do_to_parse = do_wrapper
 
 
# ========================================================================
