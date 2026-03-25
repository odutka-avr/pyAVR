# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

import clr

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (BuiltInParameter)

# local custom imports
from value_conversion import (convert_feet_to_mm,
                              convert_feet_to_m, 
                              convert_sq_feet_to_hectares, 
                              convert_sq_feet_to_sq_m)

from shared_parameters import Shared_parameters

from enums import (FloorType)

# ========================================================================

class LevelWrapper:
    def __init__(self, level_el):
        self.level_el  = level_el
        self.elevation = convert_feet_to_m(level_el.Elevation)
        self.name      = level_el.Name
        self.floor_type = None  # set later via UI (FloorType enum)

        # limk to next level instance, if no level above - None
        self.next_level = None

        self.building  = None
 
    # ── setters ──────────────────────────────────────────────────────────
 
    def set_floor_type(self, floor_type):
        """Called after user confirms above / below / podium in the UI dialog."""
        self.floor_type = floor_type
 
    def set_building(self, building):
        self.building = building
 
    # ── computed helpers ─────────────────────────────────────────────────
 
    @property
    def is_above_ground(self):
        if not self.floor_type:
            return self.elevation >= 0 and self.is_building_story
        return self.floor_type == FloorType.ABOVE_GROUND
 
    @property
    def is_underground(self):
        if not self.floor_type:
            return self.elevation < 0 and self.is_building_story
        return self.floor_type == FloorType.UNDERGROUND
 
    @property
    def is_podium(self):
        return self.floor_type == FloorType.PODIUM
    
    @property
    def is_building_story(self):
        return self.level_el.get_Parameter(BuiltInParameter.LEVEL_IS_BUILDING_STORY).AsInteger()
    
    def __str__(self):
        if self.next_level:
            return "[Name: {}, Elevation: {}, Next lvl: {}]".format(self.name, self.elevation, self.next_level.name)
        return "[Name: {}, Elevation: {}, Next lvl: {}]".format(self.name, self.elevation, self.next_level)

    def __repr__(self):
        return self.__str__()


# =========================================================================


class RoomWrapper:
    def __init__(self, room_el):
        self.room_el   = room_el 
        self.level     = None
        self.apartment = None   # ApartmentWrapper | None (None for non-residential)
        self.building  = None
        self.development_phase = None
    
    # ── computed helpers ─────────────────────────────────────────────────

    @property
    def room_type(self):
        return self.room_el.get_Parameter(Shared_parameters.ROOM_TYPE).AsInteger()
    
    @property
    def room_category(self):
        return self.room_el.get_Parameter(Shared_parameters.ROOM_CATEGORY).AsString()
    
    @property
    def room_department(self):
        return self.room_el.get_Parameter(BuiltInParameter.ROOM_DEPARTMENT).AsString()
    
    @property
    def room_name(self):
        return self.room_el.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()

    @property
    def area(self):
        """Area reduced by normative coefficient"""
        return convert_sq_feet_to_sq_m(self.room_el.get_Parameter(Shared_parameters.ROOM_AREA_WITH_COEFFICIENT).AsDouble())
 
    @property
    def is_living(self):
        return self.room_type == 1
 
    @property
    def is_summer(self):
        # check if room category == Житло?
        return self.room_type not in (0, 1, 2)
 
    @property
    def is_underground(self):
        return self.level is not None and self.level.is_underground
    
    # building data
    @property
    def building_phase_id(self):
        """Project parameter - hence using LookupParameter method"""
        return self.room_el.LookupParameter(Shared_parameters.BUILDING_DEV_PHASE_NUMBER).AsString()
    
    @property
    def building_section_id(self):
        return self.room_el.get_Parameter(Shared_parameters.BUILDING_SECTION_NUMEBR).AsString()

    # ── setters ──────────────────────────────────────────────────────────

    def set_level(self, level):
        self.level = level
 
    def set_apartment(self, apartment):
        self.apartment = apartment
 
    def set_building(self, building):
        self.building = building
 
    def set_development_phase(self, phase):
        self.development_phase = phase
 
    # ── dunder ───────────────────────────────────────────────────────────
 
    def __str__(self):
        return "RoomWrapper(cat={}, dept={}, area={}, type={})".format(
            self.room_category, self.room_department, self.area, self.room_type
        )
 
    def __repr__(self):
        return self.__str__()


# =========================================================================


class ApartmentWrapper:
    def __init__(self, apartment_number):
        self.apartment_number = apartment_number
        self.rooms = set()
        self.rooms_count = 0
        self.living_rooms_count = 0
        
        self.building = None
        self.development_phase = None
    
    # ── computed properties ───────────────────────────────────────────────
 
    @property
    def area_living(self):
        """Sum of living rooms only (type = 1.0)."""
        return sum(r.area for r in self.rooms if r.is_living)
 
    @property
    def area_total(self):
        """Sum of all rooms with normative coefficients applied."""
        return sum(r.area for r in self.rooms)
 
    @property
    def area_summer(self):
        """Sum of summer spaces (loggias, balconies)"""
        return sum(r.area for r in self.rooms if r.is_summer)

 
    @property
    def type_label(self):
        """Human-readable label: '1-кімнатна', '2-кімнатна', etc."""
        return "{}-кімнатна".format(self.living_rooms_count)
    
    @property
    def level(self):
        """
        Floor of the apartment inferred from its rooms' levels.
        For multi-level apartments returns the lowest floor number.
        Returns None if levels are not set.
        """
        levels = [r.level for r in self.rooms if r.level is not None]
        if not levels:
            return None
        # sort by elevation, return name of lowest level
        return min(levels, key=lambda lv: lv.elevation)

    @property
    def is_true_apartment(self):
        """Check if apartment is living apartment or i.e. hotel number"""
        pass

    # ── room management ──────────────────────────────────────────────────

    def add_room(self, room):
        """Add a RoomWrapper and back-link it to this apartment."""
        if room in self.rooms:
            return
        self.rooms.add(room)
        self.rooms_count += 1
        room.set_apartment(self)
 
        if room.is_living:
            self.living_rooms_count += 1
    
    # ── setters ──────────────────────────────────────────────────────────

    def set_level(self, level):
        # what if its two story apartment?
        self.level = level
    
    def set_building(self, building):
        self.building = building

    def set_development_phase(self, development_phase):
        self.development_phase = development_phase    


# =========================================================================


class AreaWrapper:
    def __init__(self, area_el):
        self.area_el     = area_el
        self.area        = convert_sq_feet_to_sq_m(area_el.Area)
        self.scheme_name = (
            area_el.AreaScheme.Name if area_el.AreaScheme is not None else ""
        )
        self.name = area_el.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()
 
        self.level     = None
        self.building  = None
        self.development_phase = None
 
    # ── setters ──────────────────────────────────────────────────────────
 
    def set_level(self, level):
        self.level = level
 
    def set_building(self, building):
        self.building = building
 
    def set_development_phase(self, phase):
        self.development_phase = phase
 
    # ── computed helpers ─────────────────────────────────────────────────
 
    @property
    def is_underground(self):
        return self.level is not None and self.level.is_underground
 
    # ── dunder ───────────────────────────────────────────────────────────
 
    def __str__(self):
        return "AreaWrapper(scheme={}, area={})".format(
            self.scheme_name, self.area
        )
 
    def __repr__(self):
        return self.__str__()


# =========================================================================


class BuildingWrapper:
    def __init__(self, doc):
        self.doc = doc
 
        # raw collections — populated by parsers
        self.levels     = set()   # set[LevelWrapper]
        self.areas      = set()   # set[AreaWrapper]
        self.rooms      = set()
        self.apartments = set()   # set[ApartmentWrapper]
        self.property_outlines = set()
 
        # non-residential rooms — populated by parsers
        self.common_rooms           = set()     # МЗК + технічні
        self.commerce_rooms         = set()     # громадського призначення
        self.parking_space_rooms    = set()     # машино-місця

        
        # self.outdoor_rooms          = set()     # літні приміщення (standalone, not per-apt)
        # self.office_rooms           = set()     # громадського призначення - офіси
        # self.commerce_rooms         = set()     # громадського призначення - комерція, готельні номери
        # self.educational_rooms      = set()     # громадського призначення - ЗДО, навчальні
        # self.driveway_rooms         = set()     # проїзди паркінгу
        # self.shelter_rooms          = set()     # укриття
        # self.shelter_parking_rooms  = set()     # укриття / паркінг
        
        # category: {department: list()}
        #self.rooms_by_category = dict()

        # set by user
        self.type_of_construction = None
        self.max_building_height = None
        self.fire_resistance_rating = None
        self.energy_efficiency_class = None
        self.construction_duration = None

        # can be read from file or set by user
        self.construction_p_name = self.__get_construction_p_name()
        self.construction_p_address = self.__construction_p_address()

        # finilized values after user made a choice
        self.building_section_id = None
        self.building_phase_id = None

        self.development_phase = None
    
    # ── helpers ───────────────────────────────────────────────────────────

    def __get_construction_p_name(self):
        pr_info = self.doc.ProjectInformation
        p_name = pr_info.get_Parameter(BuiltInParameter.PROJECT_NAME).AsString()
        if p_name:
            return p_name

    def __construction_p_address(self):
        pr_info = self.doc.ProjectInformation
        p_addr = pr_info.get_Parameter(BuiltInParameter.PROJECT_ADDRESS).AsString()
        if p_addr:
            return p_addr

    def __get_parsed_building_phase_id(self):
        # in case if rooms phase params data is inconsistent - form dict
        # key: value from param -> value: number of occurances
        # return value with the most occurances
        available_r_phases = dict()

        for room in self.rooms:
            if not (room.building_phase_id in available_r_phases):
                available_r_phases[room.building_phase_id] = 1
            available_r_phases[room.building_phase_id] += 1

        return max(available_r_phases, key=lambda ph_id: available_r_phases[ph_id])
    
    def __get_parsed_building_section_id(self):
        # in case if rooms phase params data is inconsistent - form dict
        # key: value from param -> value: number of occurances
        # return value with the most occurances
        available_b_sections = dict()

        for room in self.rooms:
            if not (room.building_section_id in available_b_sections):
                available_b_sections[room.building_section_id] = 1
            available_b_sections[room.building_section_id] += 1

        return max(available_b_sections, key=lambda b_id: available_b_sections[b_id])


    # ── computed properties ───────────────────────────────────────────────

    @property
    def apartment_data(self):
        """
        Dict keyed by num_rooms -> list[ApartmentWrapper].
        {1: [...], 2: [...], 3: [...]}
        """
        result = dict()

        for apt in self.apartments:
            result.setdefault(apt.living_rooms_count, []).append(apt)
        return result
    
    @property
    def parsed_building_section_id(self):
        return self.__get_parsed_building_section_id()
    
    @property
    def parsed_building_phase_id(self):
        return self.__get_parsed_building_phase_id()

    # ── collection adders ────────────────────────────────────────────────

    def add_level(self, level):
        level.set_building(self)
        self.levels.add(level)
 
    def add_area(self, area):
        area.set_building(self)
        self.areas.add(area)
    
    def add_room(self, room):
        room.set_building(self)
        self.rooms.add(room)
 
    def add_apartment(self, apartment):
        apartment.set_building(self)
        self.apartments.add(apartment)
    
    def add_property_outine(self, property_outline):
        self.property_outlines.add(property_outline)

    # ── setters ──────────────────────────────────────────────────────────

    def set_queue_number(self, value):
        self.queue_number = str(value)
 
    def set_building_number(self, value):
        self.building_number = str(value)

    def set_development_phase(self, phase):
        self.development_phase = phase
    
    def set_type_of_construction(self, c_type):
        self.type_of_construction = c_type
    
    def set_constuction_name(self, c_name):
        """Allow user to specify such name, if param is not filled or differs"""
        self.construction_p_name = c_name
    
    def set_constuction_address_name(self, c_address):
        """Allow user to specify address, if param is not filled or differs"""
        self.construction_p_address = c_address

    def set_max_building_height(self, max_h):
        self.max_building_height = max_h
    
    def set_fire_resistance_rating(self, f_rating):
        self.fire_resistance_rating = f_rating
    
    def set_energy_efficiency_class(self, e_class):
        self.energy_efficiency_class = e_class

    def set_construction_duration(self, c_duration):
        self.construction_duration = c_duration
    
    def set_building_section_id(self, s_id):
        """In case user wants to change parsed value"""
        if s_id:
            self.building_section_id = s_id
        else:
            self.building_section_id = self.parsed_building_section_id

    def set_building_phase_id(self, bp_id):
        """In case user wants to change parsed value"""
        if bp_id:
            self.building_phase_id = bp_id
        else:
            self.building_phase_id = self.parsed_building_phase_id
        
        # # create development phase instance
        # dev_phase_wrapper = DevelopmentPhaseWrapper(self.building_phase_id)
        # # assign it to current building instance
        # self.development_phase = dev_phase_wrapper
        # # add current building instance to the dev phase wrapper
        # dev_phase_wrapper.add_building(self)


    # ── floor counts ─────────────────────────────────────────────────────
 
    @property
    def floor_count_above(self):
        return sum(1 for lv in self.levels if lv.is_above_ground)
 
    @property
    def floor_count_underground(self):
        return sum(1 for lv in self.levels if lv.is_underground)
 
    @property
    def floor_count_podium(self):
        return sum(1 for lv in self.levels if lv.is_podium)
    
    @property
    def floor_count(self):
        return len(self.levels)
    
    # ── area aggregations ─────────────────────────────────────────────────
 
    @property
    def total_apartment_area(self):
        """Total apartment area - sum of areas with coefficient in all apartmen's rooms"""
        return sum(apt.area_total for apt in self.apartments)
 
    @property
    def living_apartment_area(self):
        """Living area, sum of all rooms in apartment that have type = 1"""
        return sum(apt.area_living for apt in self.apartments)
    
    @property
    def summer_apartment_area(self):
        """Summer rooms in appartments, room category = 1, r_type != 0,1,2"""
        return sum(apt.area_summer for apt in self.apartments)
    
    @property
    def apartment_count(self):
        return len([apt for apt in self.apartments])
    
    @property
    def apartment_count_by_room_count(self):
        """Get amaount of rooms with certain number of living rooms (type 1)"""
        return {k: len(v) for k, v in self.apartment_data.items()}
    
    @property
    def apartment_areas_by_room_count(self):
        """Get apartment areas groupped by amount of living rooms (type 1)"""
        return {k: sum(apt.area_total for apt in v) for k, v in self.apartment_data.items()}
    
    @property
    def apartment_living_areas_by_room_count(self):
        """Get apartment living areas groupped by amount of living rooms (type 1)"""
        return {k: sum(apt.area_living for apt in v) for k, v in self.apartment_data.items()}
    

    
    # TODO iterate through public_rooms and common_rooms and check their category
    @property
    def summer_area(self):
        """
        Площа літніх приміщень — combines apartment-level summer rooms
        and any standalone outdoor rooms.
        """
        from_apts = sum(apt.area_summer for apt in self.apartments)
        standalone = sum(r.area_with_coeff for r in self.outdoor_rooms)
        return from_apts + standalone
    
    @property
    def common_area(self):
        """Площа МЗК + технічних приміщень."""
        return sum(r.area for r in self.common_rooms)
    
    @property
    def commerce_area(self):
        """Площа вбудованих нежитлових приміщень громадського призначення."""
        return sum(r.area for r in self.commerce_rooms)
    
    # TODO
    @property
    def parking_total_area(self):
        """
        Загальна площа паркінгу (проїзди + машино-місця).
        Сума площі категорії Паркінг і категорії МЗК-department: Проїзд
        """
        return sum(r.area for r in self.parking_space_rooms) # + sum(r.area for r in self.driveway_rooms)
    
    @property
    def parking_spots_area(self):
        """Площа машино-місць."""
        return sum(r.area for r in self.parking_space_rooms)
    
    @property
    def parking_spots_count(self):
        """Кількість машино-місць."""
        return len(self.parking_space_rooms)
    
    # TODO
    # @property
    # def shelter_area(self):
    #     """Площа укриття."""
    #     return sum(r.area for r in self.shelter_rooms)
    
    @property
    def total_room_area(self):
        """Get all room elements area"""
        return self.total_apartment_area +\
                self.common_area +\
                self.commerce_area +\
                self.parking_total_area
    
    @property
    def property_area(self):
        pr_area = 0
        for pr_outline in self.property_outlines:
            area = pr_outline.get_Parameter(BuiltInParameter.PROPERTY_AREA).AsDouble()
            pr_area += convert_sq_feet_to_hectares(area)
        return pr_area


    def __get_areas_by_area_scheme(self, scheme_name):
        return [area for area in self.areas if area.scheme_name == scheme_name]

    def __sort_areas_by_lvl(self, scheme_name):
        areas = self.__get_areas_by_area_scheme(scheme_name)
        areas.sort(key = lambda area: area.level.elevation)
        return areas
    
    def __get_lvl_volume(self, area, lvl, next_lvl):
        if not next_lvl:
            return 0
        lvl_height = next_lvl.elevation - lvl.elevation
        return area.area * lvl_height

    def __get_volume(self, below0=False, above0=False):
        DEFAULT_AREA_SCHEME_NAME = "Загальна площа будинку"
        total_volume = 0
        sorted_areas = self.__sort_areas_by_lvl(DEFAULT_AREA_SCHEME_NAME)

        for area in sorted_areas:
            if below0 and area.level.elevation < 0:
                pass
            elif above0 and area.level.elevation >= 0:
                pass
            else:
                continue
            total_volume += self.__get_lvl_volume(area, area.level, area.level.next_level)

        return total_volume


    def get_total_volume(self):
        return self.__get_volume(below0=True, above0=True)

    def get_volume_below_0(self):
        return self.__get_volume(below0=True)

    def get_volume_above_0(self):
        return self.__get_volume(above0=True)
    

    def __get_total_area(self, exclude_undeground_lvl=False):
        """Calculate building total area by each level using modelled areas elements"""
        DEFAULT_AREA_SCHEME_NAME = "Загальна площа будинку"
        building_area = 0

        for area in self.areas:
            a_is_underground = area.is_underground

            if not (a_is_underground and exclude_undeground_lvl):
                if area.scheme_name == DEFAULT_AREA_SCHEME_NAME:
                    building_area += area.area
        
        return building_area
    
    def get_total_area(self):
        return self.__get_total_area()
    
    def get_total_area_above0(self):
        return self.__get_total_area(exclude_undeground_lvl=True)
    
    def get_building_outline_area(self):
        """
        Get area of outline of the building
        """
        DEFAULT_AREA_SCHEME_NAME = "Площа забудови"
        b_outline_area = 0
        
        for area in self.areas:
            if area.scheme_name == DEFAULT_AREA_SCHEME_NAME:
                b_outline_area += area.area
        
        return b_outline_area    


# =========================================================================



class DevelopmentPhaseWrapper:
    def __init__(self, name):
        self.name = name
        self.buildings = set()
    
    def add_building(self, building):
        building.set_development_phase(self)
        self.buildings.add(building)


# =========================================================================


class ProjectWrapper:
    def __init__(self):
        self.buildings          = set()
        self.development_phases = set()     # list[DevelopmentPhaseWrapper] — ordered
        self.property_line_area = 0.0       # га, parsed from property lines in 000
        self.run_mode           = None      # RunMode enum, set at startup

        self.usr_name = None
        self.usr_address = None 
    
    def add_phase(self, phase):
        self.development_phases.add(phase)
    
    def add_building(self, building):
        self.buildings.add(building)
        print(self.buildings)
    
    # ── setters ──────────────────────────────────────────────────────────
    
    @property
    def name(self):
        for building in self.buildings:
            if building.construction_p_name:
                return building.construction_p_name
    
    @property
    def address(self):
        for building in self.buildings:
            if building.construction_p_address:
                return building.construction_p_address
    
    def set_usr_name(self, name):
        self.usr_name = name
    
    def set_usr_address(self, address):
        self.usr_address = address
 
    def set_property_line_area(self, area_ha):
        self.property_line_area = float(area_ha)
 
    # def set_run_mode(self, mode):
    #     self.run_mode = mode
    
    # ── aggregated properties ─────────────────────────────────────────────

    # TODO
