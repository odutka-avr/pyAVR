# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

import clr

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (BuiltInParameter)

# local custom imports
from value_conversion import (convert_feet_to_m, 
                              convert_sq_feet_to_hectares, 
                              convert_sq_feet_to_sq_m)

from shared_parameters import Shared_parameters
from enums import FloorType, RoomCategories

# configure debugging
from pyrevit import script
logger = script.get_logger()


# ========================================================================

class LevelWrapper:
    """
    Wraps a Revit Level element with project-specific metadata.
 
    Stores the parsed elevation, a reference to the next level above
    (used for volume calculation), and the floor type assigned by the
    user in the level-check UI dialog.
 
    Attributes:
        level_el:    The raw Autodesk.Revit.DB.Level element.
        elevation:   Level elevation in metres (converted from feet).
        name:        Level name string as shown in Revit.
        floor_type:  FloorType value assigned after UI confirmation.
                     None until the user completes the level-check step.
        next_level:  LevelWrapper of the next level above this one,
                     or None if this is the topmost level.
        building:    Back-reference to the owning BuildingWrapper.
    """
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
        """
        Assign the floor type for this level.
        Called after the user confirms level types in the UI dialog.

        NOTE: currently used to set only FloorType.PODIUM
        other level types are derived from elevation data
 
        Args:
            floor_type (str): One of FloorType.ABOVE_GROUND,
                              FloorType.UNDERGROUND, or FloorType.PODIUM.
        """
        self.floor_type = floor_type
 
    def set_building(self, building):
        """Set the owning BuildingWrapper back-reference."""
        self.building = building
 
    # ── computed helpers ─────────────────────────────────────────────────
 
    @property
    def is_above_ground(self):
        """True if elevation >= 0 and is a building story."""
        return self.elevation >= 0 and self.is_building_story
 
    @property
    def is_underground(self):
        """True if elevation < 0 and is a building story."""
        return self.elevation < 0 and self.is_building_story
 
    @property
    def is_podium(self):
        """True if the user designated this level as podium (цокольний поверх)."""
        return self.floor_type == FloorType.PODIUM
    
    @property
    def is_building_story(self):
        """
        True if Revit marks this level as a building story. (Building Story native parameter)
        Non-story levels are excluded from counts.
        """
        param_val = self.level_el.get_Parameter(BuiltInParameter.LEVEL_IS_BUILDING_STORY)
        if param_val:
            return param_val.AsInteger()
    
    def __str__(self):
        if self.next_level:
            return "[Name: {}, Elevation: {}, Next lvl: {}]".format(self.name, self.elevation, self.next_level.name)
        return "[Name: {}, Elevation: {}, Next lvl: {}]".format(self.name, self.elevation, self.next_level)

    def __repr__(self):
        return self.__str__()


# =========================================================================


class RoomWrapper:
    """
    Wraps a Revit Room element and exposes shared parameters as
    typed Python properties. All reads are lazy (computed on access).
 
    Attributes:
        room_el:          Raw Room element.
        level:            LevelWrapper for this room's level.
        apartment:        ApartmentWrapper this room belongs to, or None.
        building:         Back-reference to BuildingWrapper.
        development_phase: Back-reference to DevelopmentPhaseWrapper.
    """
    def __init__(self, room_el):
        self.room_el   = room_el 
        self.level     = None
        self.apartment = None   # ApartmentWrapper | None (None for non-residential)
        self.building  = None
        self.development_phase = None
    
    # ── computed helpers ─────────────────────────────────────────────────

    @property
    def room_type(self):
        """
        Integer room type from AVR_Тип приміщення shared param.
        0=other, 1=living, 2=auxiliary, 3+=summer/loggia.
        """
        return self.room_el.get_Parameter(Shared_parameters.ROOM_TYPE).AsInteger()
    
    @property
    def room_category(self):
        """
        Category string from AVR_Категорія shared param.
        First routing key: "Житло", "МЗК", "Паркінг", "Комерція".
        """
        return self.room_el.get_Parameter(Shared_parameters.ROOM_CATEGORY).AsString()
    
    @property
    def room_department(self):
        """
        Department string from built-in ROOM_DEPARTMENT param.
        Second routing key within a category.
        """
        return self.room_el.get_Parameter(BuiltInParameter.ROOM_DEPARTMENT).AsString()
    
    @property
    def room_name(self):
        """Room name from built-in ROOM_NAME param."""
        return self.room_el.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()

    @property
    def area(self):
        """
        Room area in m² with normative coefficient already applied.
        Reads from AVR_Площа з коефіцієнтом.
        """
        return convert_sq_feet_to_sq_m(self.room_el.get_Parameter(Shared_parameters.ROOM_AREA_WITH_COEFFICIENT).AsDouble())
 
    @property
    def is_living(self):
        """True if room_type == 1 (жила кімната)."""
        return self.room_type == 1
 
    @property
    def is_summer(self):
        """True if room_type not in {0,1,2} (loggia, balcony, terrace)."""
        # check if room category == Житло?
        return self.room_type not in (0, 1, 2)
 
    @property
    def is_underground(self):
        """True if this room sits on an underground level."""
        return self.level is not None and self.level.is_underground

    @property
    def is_mixed_usage_shelter(self):
        """
        True if this room carries the AVR_Укриття flag but is not a
        dedicated shelter department. Used to populate
        BuildingWrapper.mixed_usage_shelter_rooms.
        """
        if self.room_department != RoomCategories.COMMON_ROOMS.SHELTER:
            val = self.room_el.get_Parameter(Shared_parameters.MIXED_USAGE_SHELTER_ROOM)
            if val:
                return bool(val.AsInteger())
    
    # building data
    @property
    def building_phase_id(self):
        """
        Development phase ID from AVR_Номер черги project parameter.
        Uses LookupParameter because it is a project (not instance) param.
        """
        return self.room_el.LookupParameter(Shared_parameters.BUILDING_DEV_PHASE_NUMBER).AsString()
    
    @property
    def building_section_id(self):
        """Building number from AVR_Номер будівлі shared param."""
        return self.room_el.get_Parameter(Shared_parameters.BUILDING_SECTION_NUMEBR).AsString()

    # ── setters ──────────────────────────────────────────────────────────

    def set_level(self, level):
        """Link this room to its LevelWrapper."""
        self.level = level
 
    def set_apartment(self, apartment):
        """Back-link to owning ApartmentWrapper (called by add_room in ApartmentWrapper)."""
        self.apartment = apartment
 
    def set_building(self, building):
        """Set owning BuildingWrapper back-reference."""
        self.building = building
 
    def set_development_phase(self, phase):
        """Set owning DevelopmentPhaseWrapper back-reference."""
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
    """
    Groups RoomWrapper instances sharing the same apartment number
    and computes apartment-level area aggregates.
 
    Attributes:
        apartment_number (str):   AVR_Номер квартири value, e.g. "101".
        rooms (set):              RoomWrapper instances in this apartment.
        rooms_count (int):        Total rooms added.
        living_rooms_count (int): Rooms with room_type == 1.
        building:                 Back-reference to BuildingWrapper.
        development_phase:        Back-reference to DevelopmentPhaseWrapper.
    """
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
        """
        Add a RoomWrapper and set its apartment back-reference.
        Idempotent — skips duplicates. Updates living_rooms_count.
 
        Args:
            room (RoomWrapper): Room to add.
        """
        if room in self.rooms:
            return
        self.rooms.add(room)
        self.rooms_count += 1
        room.set_apartment(self)
 
        if room.is_living:
            self.living_rooms_count += 1
    
    # ── setters ──────────────────────────────────────────────────────────

    def set_level(self, level):
        """Explicit level override (normally inferred from rooms)."""
        self.level = level
    
    def set_building(self, building):
        """Set owning BuildingWrapper back-reference."""
        self.building = building

    def set_development_phase(self, development_phase):
        """Set owning DevelopmentPhaseWrapper back-reference."""
        self.development_phase = development_phase    


# =========================================================================


class AreaWrapper:
    """
    Wraps a Revit Area element from an Area Plan.
 
    Used to calculate total building area (TEP items 9, 10) and
    building footprint area (TEP item 5).
 
    Attributes:
        area_el:     Raw Area element.
        area:        Area in m² (converted from sq feet).
        scheme_name: Parent AreaScheme name used as filter key.
        name:        Area element name.
        level:       LevelWrapper for this area's level.
        building:    Back-reference to BuildingWrapper.
        development_phase: Back-reference to DevelopmentPhaseWrapper.
    """
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
        """Link to LevelWrapper for underground detection."""
        self.level = level
 
    def set_building(self, building):
        """Set owning BuildingWrapper back-reference."""
        self.building = building
 
    def set_development_phase(self, phase):
        """Set owning DevelopmentPhaseWrapper back-reference."""
        self.development_phase = phase
 
    # ── computed helpers ─────────────────────────────────────────────────
 
    @property
    def is_underground(self):
        """True if this area plan is on an underground level."""
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
    """
    Central data container for one parsed Revit building model.
 
    Populated by DocumentParser then enriched by user input in the form.
    Exposes aggregated properties consumed by TepRow getters.
 
    Room sets are split by category/department for fine-grained TEP
    area aggregation. All sets are populated by the parser via
    add_room() plus direct set.add() for category-specific routing.
 
    Manual fields (height, fire rating, etc.) are None until the user
    fills them in the form and OnConfirmManualData commits them.
    """
    def __init__(self, doc):
        self.doc = doc
 
        # raw collections — populated by parsers
        self.levels     = set()   # set[LevelWrapper]
        self.areas      = set()   # set[AreaWrapper]
        self.rooms      = set()
        self.apartments = set()   # set[ApartmentWrapper]
        self.property_outlines = set()
 
        # COMMON ROOMS
        self.common_rooms                   = set()     # МЗК
        self.service_rooms                  = set()     # МЗК - технічне приміщення
        self.car_passage_rooms              = set()     # МЗК - проїзди паркінгу
        self.shelter_rooms                  = set()     # МЗК - укриття
        self.mixed_usage_shelter_rooms      = set()

        # COMMERCE ROOMS
        self.commerce_rooms                 = set()     # Комерція - громадського призначення
        self.office_rooms                   = set()     # Комерція - офіси
        self.transformer_substation_rooms   = set()     # Комерція - трансформаторні
        self.hotel_rooms                    = set()     # Комерція - готельні кімнати
        self.storage_rooms                  = set()     # Комерція - комірки

        # PARKING
        self.parking_space_rooms            = set()     # Паркінг - машино-місця
        
        # self.outdoor_rooms          = set()     # літні приміщення (standalone, not per-apt)
        # self.educational_rooms      = set()     # громадського призначення - ЗДО, навчальні
        # self.shelter_parking_rooms  = set()     # укриття / паркінг

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

        # dev pase wrapper with dev phase id which was parsed or provided by user
        self.development_phase = None
        self.project = None
    
    # ── helpers ───────────────────────────────────────────────────────────

    def __get_construction_p_name(self):
        """Read project name from Revit ProjectInformation."""
        pr_info = self.doc.ProjectInformation
        p_name = pr_info.get_Parameter(BuiltInParameter.PROJECT_NAME).AsString()
        if p_name:
            return p_name

    def __construction_p_address(self):
        """Read project address from Revit ProjectInformation."""
        pr_info = self.doc.ProjectInformation
        p_addr = pr_info.get_Parameter(BuiltInParameter.PROJECT_ADDRESS).AsString()
        if p_addr:
            return p_addr

    def __get_parsed_building_phase_id(self):
        """
        Infer phase ID by majority vote across all rooms.
        Handles inconsistently filled parameters gracefully.
        """
        # in case if rooms phase params data is inconsistent - form dict
        # key: value from param -> value: number of occurances
        # return value with the most occurances
        available_r_phases = dict()

        for room in self.rooms:
            if not (room.building_phase_id in available_r_phases):
                available_r_phases[room.building_phase_id] = 1
            available_r_phases[room.building_phase_id] += 1

        if not available_r_phases:
            return None
        return max(available_r_phases, key=lambda ph_id: available_r_phases.get(ph_id))
    
    def __get_parsed_building_section_id(self):
        """
        Infer building section ID by majority vote across all rooms.
        """
        # in case if rooms phase params data is inconsistent - form dict
        # key: value from param -> value: number of occurances
        # return value with the most occurances
        available_b_sections = dict()

        for room in self.rooms:
            if not (room.building_section_id in available_b_sections):
                available_b_sections[room.building_section_id] = 1
            available_b_sections[room.building_section_id] += 1

        if not available_b_sections:
            return None
        return max(available_b_sections, key=lambda b_id: available_b_sections.get(b_id))


    # ── computed properties ───────────────────────────────────────────────

    @property
    def apartment_data(self):
        """
        Apartments grouped by living-room count.
 
        Returns:
            dict[int, list[ApartmentWrapper]] ->
                {living_room_count: [apt, apt, ...]}
        """
        result = dict()

        for apt in self.apartments:
            result.setdefault(apt.living_rooms_count, []).append(apt)
        return result
    
    @property
    def parsed_building_section_id(self):
        """Majority-vote building section ID inferred from room params."""
        return self.__get_parsed_building_section_id()
    
    @property
    def parsed_building_phase_id(self):
        """Majority-vote development phase ID inferred from room params."""
        return self.__get_parsed_building_phase_id()

    # ── collection adders ────────────────────────────────────────────────

    def add_level(self, level):
        """Add LevelWrapper and set its building back-reference."""
        level.set_building(self)
        self.levels.add(level)
 
    def add_area(self, area):
        """Add AreaWrapper and set its building back-reference."""
        area.set_building(self)
        self.areas.add(area)
    
    def add_room(self, room):
        """
        Add RoomWrapper to master room set.
        Category routing to sub-sets is done separately by the parser.
        """
        room.set_building(self)
        self.rooms.add(room)
 
    def add_apartment(self, apartment):
        """Add ApartmentWrapper and set its building back-reference."""
        apartment.set_building(self)
        self.apartments.add(apartment)
    
    def add_property_outine(self, property_outline):
        """Add a PropertyLine element to the property outline set."""
        self.property_outlines.add(property_outline)

    # ── setters ──────────────────────────────────────────────────────────
 
    def set_building_number(self, value):
        """Set building number string."""
        self.building_number = str(value)

    def set_development_phase(self, phase):
        """Set owning DevelopmentPhaseWrapper back-reference."""
        self.development_phase = phase
    
    def set_project(self, project):
        """Set owning ProjectWrapper back-reference."""
        self.project = project
    
    def set_constuction_name(self, c_name):
        """Override project name with user-provided value."""
        self.construction_p_name = c_name
    
    def set_constuction_address_name(self, c_address):
        """Override project address with user-provided value."""
        self.construction_p_address = c_address

    def set_max_building_height(self, max_h):
        """Set max building height in metres (TEP item 6)."""
        self.max_building_height = max_h
    
    def set_fire_resistance_rating(self, f_rating):
        """Set fire resistance rating string (TEP item 7)."""
        self.fire_resistance_rating = f_rating
    
    def set_energy_efficiency_class(self, e_class):
        """Set energy efficiency class string (TEP item 8)."""
        self.energy_efficiency_class = e_class

    def set_construction_duration(self, c_duration):
        """Set construction duration in months (TEP item 22)."""
        self.construction_duration = c_duration
    
    def set_building_section_id(self, s_id):
        """
        Set building section ID, falling back to parsed value if None/empty.
        (in case user sets a new one via UI)
 
        Args:
            s_id (str | None): User-provided ID or None.
        """
        if s_id:
            self.building_section_id = s_id
        else:
            self.building_section_id = self.parsed_building_section_id

    def set_building_phase_id(self, bp_id):
        """
        Set phase ID, falling back to parsed value if None/empty.
        (in case user sets a new one via UI)
 
        Args:
            bp_id (str | None): User-provided ID or None.
        """
        if bp_id:
            self.building_phase_id = bp_id
        else:
            self.building_phase_id = self.parsed_building_phase_id

    # ── identity data─────────────────────────────────────────────────────


    # ── floor counts ─────────────────────────────────────────────────────
 
    @property
    def floor_count_above(self):
        """Count of above-ground building storeys (TEP item 3.1)."""
        return sum(1 for lv in self.levels if lv.is_above_ground)
 
    @property
    def floor_count_underground(self):
        """Count of underground building storeys (TEP item 3.3)."""
        return sum(1 for lv in self.levels if lv.is_underground)
 
    @property
    def floor_count_podium(self):
        """Count of podium storeys (TEP item 3.2)."""
        return sum(1 for lv in self.levels if lv.is_podium)
    
    @property
    def floor_count(self):
        """Total count of building story levels."""
        return len(self.levels)
    
    # ── area aggregations ─────────────────────────────────────────────────
 
    @property
    def total_apartment_area(self):
        """Total apartment area with coefficients applied (TEP item 14)."""
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
        """Total number of apartments (TEP item 13)."""
        return len([apt for apt in self.apartments])
    
    @property
    def _apartment_count_by_room_count(self):
        """Dict {num_rooms: count} for all apartment types."""
        return {k: len(v) for k, v in self.apartment_data.items()}

    def apartment_count_by_room_count(self, num_of_rooms):
        """
        Count of apartments with a specific living-room count (TEP item 13 sub-row).
 
        Args:
            num_of_rooms (int): Living room count to query.
 
        Returns:
            int | None
        """
        return self._apartment_count_by_room_count.get(num_of_rooms)
    
    @property
    def _apartment_areas_by_room_count(self):
        """Dict {num_rooms: total_area} for all apartment types."""
        return {k: sum(apt.area_total for apt in v) for k, v in self.apartment_data.items()}
    
    def apartment_areas_by_room_count(self, num_of_rooms):
        """
        Total area of apartments with a specific room count (TEP item 14 sub-row).
 
        Args:
            num_of_rooms (int): Living room count to query.
 
        Returns:
            float | None
        """
        return self._apartment_areas_by_room_count.get(num_of_rooms)

    @property
    def _apartment_living_areas_by_room_count(self):
        """Dict {num_rooms: total_living_area} for all apartment types."""
        return {k: sum(apt.area_living for apt in v) for k, v in self.apartment_data.items()}
    
    def apartment_living_areas_by_room_count(self, num_of_rooms):
        """
        Total living area of apartments with a specific room count (TEP item 15 sub-row).
 
        Args:
            num_of_rooms (int): Living room count to query.
 
        Returns:
            float | None
        """
        return self._apartment_living_areas_by_room_count.get(num_of_rooms)

    @property
    def summer_area(self):
        """Total summer space area from all apartments (TEP item 16)."""
        from_apts = sum(apt.area_summer for apt in self.apartments)
        # standalone = sum(r.area_with_coeff for r in self.outdoor_rooms)
        return from_apts #+ standalone
    
    @property
    def common_area(self):
        """
        Total shared-use space area (TEP item 17).
        Includes МЗК, service, car passages, dedicated shelters.
        """
        return sum(r.area for r in self.common_rooms) + \
                sum(r.area for r in self.shelter_rooms) + \
                sum(r.area for r in self.car_passage_rooms) + \
                sum(r.area for r in self.service_rooms)
    
    @property
    def commerce_area(self):
        """
        Total embedded commercial room area (TEP item 18).
        Includes commerce, offices, substations, hotel rooms, storage.
        """
        return sum(r.area for r in self.commerce_rooms) + \
                sum(r.area for r in self.office_rooms) + \
                sum(r.area for r in self.transformer_substation_rooms) + \
                sum(r.area for r in self.hotel_rooms) + \
                sum(r.area for r in self.hotel_rooms) + \
                sum(r.area for r in self.storage_rooms)
    
    @property
    def parking_total_area(self):
        """Total parking area including car passages (TEP item 19)."""
        return sum(r.area for r in self.parking_space_rooms) + \
                sum(r.area for r in self.car_passage_rooms) + self._get_all_rooms_area_near_parking()
    
    def _get_all_rooms_area_near_parking(self):
        """
        Загальна площа приміщень паркінгу must include all rooms that are near parking spaces
        Get all levels on which are parking spaces located, iterte through all rooms
        if room is on the same level as parking spots - add room's area to total area

        IMPORTANT - omit parking spots areas + car passage areas as they are already included
        """
        lvls_with_parking = set()
        for parking_space in self.parking_space_rooms:
            lvls_with_parking.add(parking_space.level)
        
        total_parking_space_area = 0
        
        for room in self.rooms:
            # omit parking spots' areas and car passage areas as they are already included
            if (room.level in lvls_with_parking) and not (room in self.car_passage_rooms or room in self.parking_space_rooms):
                total_parking_space_area += room.area
        
        return total_parking_space_area

    @property
    def parking_spots_area(self):
        """Parking space area excluding car passages (TEP item 19 sub-row)."""
        return sum(r.area for r in self.parking_space_rooms)
    
    @property
    def parking_spots_count(self):
        """Number of individual parking spaces (TEP item 20)."""
        return len(self.parking_space_rooms)
    
    @property
    def shelter_area(self):
        """
        Total shelter area including mixed-usage rooms (TEP item 21).
        Combines dedicated shelter rooms and AVR_Укриття-flagged rooms.
        """
        return sum(r.area for r in self.shelter_rooms) + \
                sum(r.area for r in self.mixed_usage_shelter_rooms)
    
    @property
    def total_room_area(self):
        """Sum of all room areas across all categories (TEP item 11)."""
        return sum(r.area for r in self.rooms)
    
    @property
    def property_area(self):
        """
        Property line area in hectares (TEP item 4).
        Reads PROPERTY_AREA param from each PropertyLine element.
        Open (unclosed) lines have area == -1 and are skipped.
        """
        pr_area = 0
        for pr_outline in self.property_outlines:
            area = pr_outline.get_Parameter(BuiltInParameter.PROPERTY_AREA).AsDouble()
            pr_area += convert_sq_feet_to_hectares(area)
        return pr_area

    def __get_areas_by_area_scheme(self, scheme_names):
        """Return AreaWrappers matching the given area scheme name."""
        return [area for area in self.areas if area.scheme_name in scheme_names]

    def __sort_areas_by_lvl(self, scheme_names):
        """Return AreaWrappers for a scheme sorted by ascending level elevation."""
        areas = self.__get_areas_by_area_scheme(scheme_names)
        areas.sort(key = lambda area: area.level.elevation)
        return areas
    
    def __get_lvl_volume(self, area, lvl, next_lvl):
        """
        Storey volume = floor plate area × floor-to-floor height.
        Returns 0 for the topmost storey (no next level to measure to).
        """
        if not next_lvl:
            return 0
        lvl_height = next_lvl.elevation - lvl.elevation
        return area.area * lvl_height

    def __get_volume(self, below0=False, above0=False):
        """
        Building volume for levels above or below elevation 0.
 
        Uses "Загальна площа будинку" area scheme.
        Multiplies each floor plate by its floor-to-floor height.
 
        Args:
            below0 (bool): Include levels with elevation < 0.
            above0 (bool): Include levels with elevation >= 0.
 
        Returns:
            float: Volume in m³.
        """
        DEFAULT_AREA_SCHEME_NAMES = ["Загальна площа будівлі", "Загальна площа будинку"]
        total_volume = 0
        sorted_areas = self.__sort_areas_by_lvl(DEFAULT_AREA_SCHEME_NAMES)

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
        """Total building volume above and below 0.000 (TEP item 22)."""
        return self.__get_volume(below0=True, above0=True)

    def get_volume_below_0(self):
        """Building volume below elevation 0.000 (TEP item 22.1)."""
        return self.__get_volume(below0=True)

    def get_volume_above_0(self):
        """Building volume above elevation 0.000 (TEP item 22.2)."""
        return self.__get_volume(above0=True)
    

    def __get_total_area(self, exclude_undeground_lvl=False):
        """
        Total building area from "Загальна площа будинку" area scheme.
 
        Args:
            exclude_undeground_lvl (bool): Skip areas on underground levels.
 
        Returns:
            float: Area in m².
        """
        DEFAULT_AREA_SCHEME_NAMES = ["Загальна площа будівлі", "Загальна площа будинку"]
        building_area = 0

        for area in self.areas:
            a_is_underground = area.is_underground

            if not (a_is_underground and exclude_undeground_lvl):
                if area.scheme_name in DEFAULT_AREA_SCHEME_NAMES:
                    building_area += area.area
        
        return building_area
    
    def get_total_area(self):
        """Total building area including underground levels (TEP item 9)."""
        return self.__get_total_area()
    
    def get_total_area_above0(self):
        """Total building area excluding underground levels (TEP item 10)."""
        return self.__get_total_area(exclude_undeground_lvl=True)
    
    def get_building_outline_area(self):
        """Building footprint from 'Площа забудови' area scheme (TEP item 5)."""
        DEFAULT_AREA_SCHEME_NAME = "Площа забудови"
        b_outline_area = 0
        
        for area in self.areas:
            if area.scheme_name == DEFAULT_AREA_SCHEME_NAME:
                b_outline_area += area.area
        
        return b_outline_area    

    def __str__(self):
        return "BUILDING WRAPPER: {}".format(self.building_section_id)

    def __repr__(self):
        return self.__str__()


# =========================================================================

class SumAdapter:
    """
    Aggregation adapter that makes a collection of BuildingWrapper
    instances behave like a single BuildingWrapper.
 
    Base class for DevelopmentPhaseWrapper and ProjectWrapper.
    Allows TepRow getters to call the same interface on a building,
    a phase total, or a project total without any changes.
 
    Floor count properties return a range string when buildings differ.
    Manual fields return comma-separated sets of distinct values.
 
    Args:
        buildings: Iterable of BuildingWrapper instances.
                   Stored by reference — mutations to the set are
                   reflected immediately in aggregated properties.
    """
    def __init__(self, buildings):
        self.buildings = buildings
    
    def __sum(self, getter):
        """
        Sum a property across all buildings, skipping None and errors.
 
        Args:
            getter (callable): Function(BuildingWrapper) -> numeric | None.
 
        Returns:
            float | int: Summed value.
        """
        total = 0
        for b in self.buildings:
            try:
                val = getter(b)
                if val is not None:
                    total += val
            except (TypeError, ValueError):
                pass
        return total

    @property
    def construction_p_name(self):
        """First non-empty project name across buildings."""
        for b in self.buildings:
            if b.construction_p_name:
                return b.construction_p_name
    
    @property
    def construction_p_address(self):
        """First non-empty project address across buildings."""
        for b in self.buildings:
            if b.construction_p_address:
                return b.construction_p_address
    
    @property
    def type_of_construction(self):
        """First non-empty type-of-construction across buildings."""
        for b in self.buildings:
            if b.type_of_construction:
                return b.type_of_construction

    @property
    def floor_count(self):
        """Floor count range string, or single value if all equal."""
        max_f_count = max(b.floor_count for b in self.buildings)
        min_f_count = min(b.floor_count for b in self.buildings)
        if max_f_count == min_f_count:
            return str(max_f_count)
        return "{}-{}".format(str(min_f_count), str(max_f_count))
    
    @property
    def floor_count_above(self):
        """Above-ground floor count range across buildings."""
        max_f_count = max(b.floor_count_above for b in self.buildings)
        min_f_count = min(b.floor_count_above for b in self.buildings)
        if max_f_count == min_f_count:
            return str(max_f_count)
        return "{}-{}".format(str(min_f_count), str(max_f_count))
    
    @property
    def floor_count_podium(self):
        """
        Podium floor count range across buildings.
        max and min amount of podium floors in project's buildings
        """
        max_f_count = max(b.floor_count_podium for b in self.buildings)
        min_f_count = min(b.floor_count_podium for b in self.buildings)
        if max_f_count == min_f_count:
            return str(max_f_count)
        return "{}-{}".format(str(min_f_count), str(max_f_count))

    @property
    def floor_count_underground(self):
        """Underground floor count range across buildings."""
        max_f_count = max(b.floor_count_underground for b in self.buildings)
        min_f_count = min(b.floor_count_underground for b in self.buildings)
        if max_f_count == min_f_count:
            return str(max_f_count)
        return "{}-{}".format(str(min_f_count), str(max_f_count))
    
    @property
    def property_area(self):
        """Sum of property areas in hectares."""
        return self.__sum(lambda b: b.property_area)
    
    def get_building_outline_area(self):
        """Sum of building footprint areas."""
        return self.__sum(lambda b: b.get_building_outline_area())
    
    def _numeric_max_height(self):
        """
        Return the highest max_building_height across buildings as a float.
        Handles None values and MergedBuildingWrapper instances that
        return strings from their max_building_height display property.
        """
        values = []
        for b in self.buildings:
            val = b.max_building_height
            if val is None:
                continue
            try:
                # handles both float (BuildingWrapper) and
                # string like "15.00" or "12.00-18.00" (MergedBuildingWrapper)
                if isinstance(val, str):
                    # take the highest number from a range string
                    val = max(float(v) for v in val.split("-"))
                values.append(float(val))
            except (TypeError, ValueError):
                continue
        return values

    @property
    def max_building_height(self):
        values = self._numeric_max_height()
        if not values:
            return None
        max_h = max(values)
        min_h = min(values)
        if max_h == min_h:
            return str(max_h)
        return "{:.2f}-{:.2f}".format(min_h, max_h)
    
    @property
    def fire_resistance_rating(self):
        """Comma-separated distinct fire resistance ratings."""
        ratings = set()
        for b in self.buildings:
            if ", " in b.fire_resistance_rating:
                for r in b.fire_resistance_rating.split(", "):
                    ratings.add(r)
            else:
                ratings.add(b.fire_resistance_rating)
        #f_ratings = set([str(b.fire_resistance_rating) for b in self.buildings])
        logger.debug(ratings)
        return ", ".join(sorted(ratings))
    
    @property
    def energy_efficiency_class(self):
        """Comma-separated distinct energy efficiency classes."""
        ratings = set()
        for b in self.buildings:
            if ", " in b.energy_efficiency_class:
                for r in b.energy_efficiency_class.split(", "):
                    ratings.add(r)
            else:
                ratings.add(b.energy_efficiency_class)
        #f_ratings = set([str(b.fire_resistance_rating) for b in self.buildings])
        logger.debug(ratings)
        return ", ".join(sorted(ratings))
    
    def get_total_area(self):
        """Sum of total building areas including underground."""
        return self.__sum(lambda b: b.get_total_area())
    
    def get_total_area_above0(self):
        """Sum of total building areas excluding underground."""
        return self.__sum(lambda b: b.get_total_area_above0())
    
    @property
    def total_room_area(self):
        """Sum of all room areas across buildings."""
        return self.__sum(lambda b: b.total_room_area)
    
    @property
    def apartment_count(self):
        """Total apartment count across all buildings."""
        return self.__sum(lambda b: b.apartment_count)
    
    def apartment_count_by_room_count(self, num_of_rooms):
        """Total apartments with specific room count across all buildings."""
        return self.__sum(lambda b: b.apartment_count_by_room_count(num_of_rooms))
    
    @property
    def total_apartment_area(self):
        """Total apartment area across all buildings."""
        return self.__sum(lambda b: b.total_apartment_area)
    
    def apartment_areas_by_room_count(self, num_of_rooms):
        """Total area of apartments with specific room count across all buildings."""
        return self.__sum(lambda b: b.apartment_areas_by_room_count(num_of_rooms))
    
    @property
    def living_apartment_area(self):
        """Total living area across all buildings."""
        return self.__sum(lambda b: b.living_apartment_area)
    
    def apartment_living_areas_by_room_count(self, num_of_rooms):
        """Total living area of apartments with specific room count across all buildings."""
        return self.__sum(lambda b: b.apartment_living_areas_by_room_count(num_of_rooms))
    
    @property
    def summer_apartment_area(self):
        """Total summer space area across all buildings."""
        return self.__sum(lambda b: b.summer_apartment_area)
    
    @property
    def common_area(self):
        """Total shared-use room area across all buildings."""
        return self.__sum(lambda b: b.common_area)
    
    @property
    def commerce_area(self):
        """Total commercial room area across all buildings."""
        return self.__sum(lambda b: b.commerce_area)
    
    @property
    def parking_total_area(self):
        """Total parking area including car passages across all buildings."""
        return self.__sum(lambda b: b.parking_total_area)
    
    @property
    def parking_spots_area(self):
        """Total parking space area excluding car passages across all buildings."""
        return self.__sum(lambda b: b.parking_spots_area)
    
    @property
    def parking_spots_count(self):
        """Total parking space count across all buildings."""
        return self.__sum(lambda b: b.parking_spots_count)
    
    @property
    def shelter_area(self):
        """Total shelter area across all buildings."""
        return self.__sum(lambda b: b.shelter_area)
    
    def get_total_volume(self):
        """Total building volume across all buildings."""
        return self.__sum(lambda b: b.get_total_volume())
    
    def get_volume_below_0(self):
        """Total below-grade volume across all buildings."""
        return self.__sum(lambda b: b.get_volume_below_0())
    
    def get_volume_above_0(self):
        """Total above-grade volume across all buildings."""
        return self.__sum(lambda b: b.get_volume_above_0())
    
    @property
    def construction_duration(self):
        """Sum of construction durations in months across all buildings."""
        return self.__sum(lambda b: b.construction_duration)

    def __str__(self):
        return ",".join(str(b) for b in self.buildings)
    
    def __repr__(self):
        return self.__str__()


# =========================================================================


class MergedBuildingWrapper(SumAdapter):
    """"""
    def __init__(self, buildings):
        self.buildings = buildings
        SumAdapter.__init__(self, self.buildings)
    
    @property
    def building_section_id(self):
        return "-".join(str(b.building_section_id) for b in self.buildings)
    
    def __str__(self):
        return "MergedWrapper [{}]".format(", ".join(str(b) for b in self.buildings))
    

# =========================================================================


class DevelopmentPhaseWrapper(SumAdapter):
    """
    One development phase (черга) containing one or more buildings.
 
    Extends SumAdapter so TepRow getters aggregate correctly across
    all buildings in the phase for the phase-total column.
 
    Attributes:
        name (str):      Phase identifier entered by the user, e.g. "1".
        buildings (set): BuildingWrapper instances in this phase.
    """
    def __init__(self, name):
        self.name = name
        self.buildings = set()
        SumAdapter.__init__(self, self.buildings)
    
    @property
    def num_of_buildings(self):
        """Number of buildings in this development phase."""
        return len(self.buildings)
    
    def add_building(self, building):
        """
        Add a building and set its phase back-reference.
 
        Args:
            building (BuildingWrapper): Building to add.
        """
        building.set_development_phase(self)
        self.buildings.add(building)
    
    def __str__(self):
        return "NAME: {}, BUILDINGS: {}".format(self.name, ", ".join(str(b) for b in self.buildings))
    
    def __repr__(self):
        return self.__str__()


# =========================================================================


class ProjectWrapper(SumAdapter):
    """
    Top-level container for all buildings and phases.
 
    Extends SumAdapter for project-total TEP column aggregation.
 
    Attributes:
        buildings (set):          All BuildingWrapper instances.
        development_phases (set): All DevelopmentPhaseWrapper instances.
        property_line_area (float): Site area in hectares (TEP item 4).
        usr_name (str | None):    User-overridden project name.
        usr_address (str | None): User-overridden project address.
        type_of_construction (str | None): Propagated to all buildings.
    """
    def __init__(self):
        self.buildings          = set()
        SumAdapter.__init__(self, self.buildings)

        self.development_phases = set()     # list[DevelopmentPhaseWrapper] — ordered
        self.property_line_area = 0.0       # га, parsed from property lines in 000

        self.usr_name = None
        self.usr_address = None 

        self.type_of_construction = None
    
    def add_dev_phase(self, phase):
        """Add a DevelopmentPhaseWrapper to the project."""
        self.development_phases.add(phase)
    
    def add_building(self, building):
        """Add a building and set its project back-reference."""
        self.buildings.add(building)
        building.set_project(self)

    def set_type_of_construction(self, type_of_construction):
        """
        Set type-of-construction on the project and propagate to all buildings.
 
        Args:
            type_of_construction (str): e.g. "Нове будівництво".
        """
        self.type_of_construction = type_of_construction
        for b in self.buildings:
            b.type_of_construction = type_of_construction
    
    # ── setters ──────────────────────────────────────────────────────────
    
    @property
    def name(self):
        """Project name from the first building with a non-empty value."""
        for building in self.buildings:
            if building.construction_p_name:
                return building.construction_p_name
    
    @property
    def address(self):
        """Project address from the first building with a non-empty value."""
        for building in self.buildings:
            if building.construction_p_address:
                return building.construction_p_address
    
    def get_name(self):
        """Effective name — user override takes priority over parsed value."""
        if self.usr_name:
            return self.usr_name
        return self.name
    
    def get_address(self):
        """Effective address — user override takes priority over parsed value."""
        if self.usr_address:
            return self.usr_address
        return self.address
    
    def set_usr_name(self, name):
        """
        Override project name with user-provided value from the form.
        Propagate to all buildings
        """
        self.usr_name = name
        for b in self.buildings:
            b.set_constuction_name(name)
    
    def set_usr_address(self, address):
        """
        Override project address with user-provided value from the form.
        Propagate to all buildings
        """
        self.usr_address = address
        for b in self.buildings:
            b.set_constuction_address_name(address)
 
    def set_property_line_area(self, area_ha):
        """Set total site area in hectares."""
        self.property_line_area = float(area_ha)
    
    def __str__(self):
        return "NAME: {}, ADDRESS: {}, DEV PHASES: [{}]".format(
            self.get_name(), 
            self.get_address(),
            ", ".join(d for d in list(self.development_phases)))

    def __repr__(self):
        return self.__str__()
 

