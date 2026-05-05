# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

import clr

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (BuiltInParameter, StorageType)

# local custom imports
from value_conversion import (convert_feet_to_m, 
                              convert_sq_feet_to_hectares, 
                              convert_sq_feet_to_sq_m)

from shared_parameters import Shared_parameters
from System import Guid


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
    
    @property
    def is_main_model(self):
        return self.do_el is None
 
    @property
    def element_id(self):
        """ElementId of the underlying DesignOption, or None for Main Model."""
        return self.do_el.Id if self.do_el else None

    def __str__(self):
        return "DesignOptionWrapper({})".format(self.name)
 
    def __repr__(self):
        return self.__str__()
    

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
    def __init__(self, room_el, source_doc=None):
        self.room_el   = room_el 
        self.element_id = room_el.Id
        self.level     = None
        self.apartment = None   # ApartmentWrapper | None (None for non-residential)
        self.building  = None
        self.development_phase = None
        self.source_doc = source_doc
    
    # ── computed helpers ─────────────────────────────────────────────────

    @property
    def room_type_param(self):
        """
        Integer room type from AVR_Тип приміщення shared param.
        0=other, 1=living, 2=auxiliary, 3+=summer/loggia.
        """
        return self.room_el.get_Parameter(Shared_parameters.ROOM_TYPE)
    
    @property
    def room_category_param(self):
        """
        Category string from AVR_Категорія shared param.
        First routing key: "Житло", "МЗК", "Паркінг", "Комерція".
        """
        return self.room_el.get_Parameter(Shared_parameters.ROOM_CATEGORY)
    
    @property
    def room_department_param(self):
        """
        Department string from built-in ROOM_DEPARTMENT param.
        Second routing key within a category.
        """
        return self.room_el.get_Parameter(BuiltInParameter.ROOM_DEPARTMENT)
    
    @property
    def room_name_param(self):
        """Room name from built-in ROOM_NAME param."""
        return self.room_el.get_Parameter(BuiltInParameter.ROOM_NAME)
    
    @property
    def apartment_number_param(self):
        """Raw Parameter for AVR_Номер квартири."""
        return self.room_el.get_Parameter(Shared_parameters.APARTMENT_NUMBER)

    # building data
    @property
    def building_phase_id_param(self):
        """
        Development phase ID from AVR_Номер черги project parameter.
        Uses LookupParameter because it is a project (not instance) param.
        """
        return self.room_el.LookupParameter(Shared_parameters.BUILDING_DEV_PHASE_NUMBER)
    
    @property
    def building_section_id_param(self):
        """Building number from AVR_Номер будівлі shared param."""
        return self.room_el.get_Parameter(Shared_parameters.BUILDING_SECTION_NUMEBR)
    
    # ── write helpers (must be inside open Transaction) ────────────────────
 
    def write_room_type(self, value):
        return self._write_param(self.room_el, Shared_parameters.ROOM_TYPE, value)
 
    def write_category(self, value):
        return self._write_param(self.room_el, Shared_parameters.ROOM_CATEGORY, value)
 
    def write_department(self, value):
        return self._write_param(self.room_el, None, value,
                            builtin=BuiltInParameter.ROOM_DEPARTMENT)
 
    def write_apartment_number(self, value):
        return self._write_param(self.room_el, Shared_parameters.APARTMENT_NUMBER, value)
 
    # ── setters for back-links ─────────────────────────────────────────────
 
    def set_level(self, level_wrapper):
        self.level = level_wrapper
        level_wrapper.add_room(self)
 
    def set_apartment(self, apt_wrapper):
        self.apartment = apt_wrapper
 
    def set_building(self, building_wrapper):
        self.building = building_wrapper
 
    def set_development_phase(self, phase):
        """Set owning DevelopmentPhaseWrapper back-reference."""
        self.development_phase = phase
 
    # ── dunder ───────────────────────────────────────────────────────────
 
    # def __str__(self):
    #     return "RoomWrapper(cat={}, dept={}, area={}, type={})".format(
    #         self.room_category, self.room_department, self.area, self.room_type
    #     )

    def __str__(self):
        return u"Room(ID: {}, Number: {})".format(self.element_id, self.apartment_number_param.AsString())
 
    def __repr__(self):
        return self.__str__()


class DoorWrapper(object):
    """
    Wraps a Revit Door (FamilyInstance) and exposes room connectivity.
 
    ``is_apartment_entrance`` is set by the parser based on the user's
    chosen detection strategy (type-name or Yes/No parameter).
    """
 
    def __init__(self, door_el, phase, source_doc=None):
        """
        Args:
            door_el:    Raw FamilyInstance (door).
            phase:      Autodesk.Revit.DB.Phase used for FromRoom/ToRoom lookup.
            source_doc: Document owning the door (for linked-model support).
        """
        self.door_el    = door_el
        self.phase      = phase
        self.source_doc = source_doc
 
        # RoomWrapper references (None if room is on the other side of a link
        # or if the door is exterior)
        self.from_room = None   # RoomWrapper | None <- appartment room
        self.to_room   = None   # RoomWrapper | None

        self.hallway_found = None
    
        self.level = None
 
    # ── properties ────────────────────────────────────────────────────────
 
    @property
    def element_id(self):
        return self.door_el.Id
 
    @property
    def type_name(self):
        """Family type name of the door (e.g. 'ДВ_Квартирні_Вхідні 900x2100')."""
        try:
            return self.door_el.Name or u""
        except Exception:
            return u""
 
    @property
    def family_name(self):
        """Family name of the door."""
        try:
            return self.door_el.Symbol.Family.Name or u""
        except Exception:
            return u""
 
    @property
    def full_type_name(self):
        """'FamilyName :: TypeName' string used for keyword matching."""
        return u"{} :: {}".format(self.family_name, self.type_name)

    @property
    def is_apartment_entrance(self):
        """
        Read a Yes/No (integer) parameter from the door instance or its type.
 
        Returns True if the parameter exists and its value is 1 (Yes),
        False otherwise.
        """
        # TODO: change for shared parameter

        # Try instance first, then type
        p = self.door_el.LookupParameter("Вхід у квартиру")
        if p is None:
            try:
                p = self.door_el.Symbol.LookupParameter("Вхід у квартиру")
            except Exception:
                pass
        if p is None:
            return False
        if p.StorageType == StorageType.Integer:
            return p.AsInteger() == 1
        return False



    
    def set_level(self, level_wrapper):
        self.level = level_wrapper
        level_wrapper.add_doors(self)
 
    # ── room resolution ───────────────────────────────────────────────────
 
    def resolve_rooms(self, room_by_id):
        """
        Populate ``from_room`` and ``to_room`` using the phase-aware API.
 
        Args:
            room_by_id (dict): Maps ElementId → RoomWrapper for the owning doc.
        """
        try:
            fr = self.door_el.get_Parameter(BuiltInParameter.PHASE_CREATED) # door phase
            from_el = self.door_el.FromRoom[self.phase]
            to_el   = self.door_el.ToRoom[self.phase]
        except Exception:
            return
 
        if from_el is not None:
            self.from_room = room_by_id.get(from_el.Id)
        if to_el is not None:
            self.to_room = room_by_id.get(to_el.Id)
 
    # ── dunder ────────────────────────────────────────────────────────────
 
    def __str__(self):
        return u"DoorWrapper({!r}, apt_ent={})".format(
            self.full_type_name, self.is_apartment_entrance
        )
 
    def __repr__(self):
        return self.__str__()


class ApartmentWrapper(object):
    """
    Represents one apartment: an ordered collection of RoomWrappers that
    form a connected component separated from the common areas by
    apartment entrance doors.
 
    The apartment number is assigned by the writer, not the parser.
    """
 
    def __init__(self):
        self.rooms          = []          # list[RoomWrapper]
        self.apartment_num  = None        # int, assigned during write pass
        self.level          = None        # LevelWrapper of majority of rooms
        self.building       = None        # BuildingWrapper
 
    # ── room management ───────────────────────────────────────────────────
 
    def add_room(self, room_wrapper):
        """Add a RoomWrapper and set its back-link."""
        self.rooms.append(room_wrapper)
        room_wrapper.set_apartment(self)
 
    # ── properties ────────────────────────────────────────────────────────
 
    @property
    def room_count(self):
        return len(self.rooms)
 
    @property
    def total_area_sqft(self):
        return sum(r.area_sqft for r in self.rooms)

    def set_level(self, level):
        self.level = level
        level.apartments.add(self)
 
    # ── dunder ────────────────────────────────────────────────────────────
 
    def __str__(self):
        return u"\n\tApartmentWrapper(rooms={}, lvl: {})".format(
            self.room_count, self.level
        )
 
    def __repr__(self):
        return self.__str__()
    

class LevelWrapper(object):
    """
    Wraps a Revit Level and groups apartments/rooms by floor.
    Levels are ordered by elevation for consistent numbering.
    """
 
    def __init__(self, level_el):
        self.level_el   = level_el
        self.apartments = set()       # list[ApartmentWrapper]
        self.rooms      = []          # all RoomWrappers on this level
        self.doors      = []
        self.apt_entrances = []
 
    @property
    def name(self):
        return self.level_el.Name or u""
 
    @property
    def elevation(self):
        """Elevation in Revit internal units (feet)."""
        return self.level_el.Elevation
 
    @property
    def element_id(self):
        return self.level_el.Id
 
    def add_apartment(self, apt):
        self.apartments.append(apt)
        apt.level = self
 
    def add_room(self, room):
        self.rooms.append(room)

    def add_doors(self, door):
        self.doors.append(door)
        if door.is_apartment_entrance:
            self.apt_entrances.append(door)
 
    def __str__(self):
        return u"LevelWrapper({!r}, elev={:.2f})".format(
            self.name, self.elevation)
 
    def __repr__(self):
        return self.__str__()


class BuildingWrapper(object):
    """
    Top-level container for one Revit document (host or linked model).
 
    Holds ordered levels, all apartments, all rooms, and design-option
    metadata.  The parser populates it; the writer consumes it.
    """
 
    def __init__(self, doc, is_link=True):
        """
        Args:
            doc:           Autodesk.Revit.DB.Document for this building.
            link_instance: RevitLinkInstance element in the host doc,
                           or None if this IS the host document.
        """
        self.doc           = doc
        self.dev_phase_id = None
        self.building_id = None

        self.link_instance = is_link

        # Populated by parser
        self.parser          = None
        self.design_options  = []
        self.levels          = []            # list[LevelWrapper], sorted by elevation
        self.apartments      = []            # list[ApartmentWrapper], all floors
        self.rooms           = []            # list[RoomWrapper], all floors
        self.untracked_rooms = []            # rooms not in any apartment
        self.all_doors       = []            # all doors and windows that belong to building
        self.apt_entrances   = []            # doors that are entrances to apartments
        
        self.hallways = []

        # Selected design option (DesignOptionWrapper set by the user in the UI)
        self.design_option   = None          # DesignOptionWrapper
 
        # UI ordering – lower index processed first (lower apartment numbers)
        self.order_index     = 0
 
    # ── identity ──────────────────────────────────────────────────────────
 
    @property
    def is_host(self):
        """True if this wraps the host (current) Revit document."""
        return self.is_link
 
    @property
    def display_name(self):
        """
        Name shown in the form.
        Host model → document title.
        Linked model → link instance name (file name without path/extension).
        """
        return self.doc.Title or u"(Current Model)"

    # ── setters ────────────────────────────────────────────────────────────

    def set_dev_phase_id(self, dev_phase_id):
        self.dev_phase_id = dev_phase_id
    
    def set_building_id(self, b_id):
        self.building_id = b_id
 
    # ── apartment management ───────────────────────────────────────────────
 
    def add_apartment(self, apt):
        self.apartments.append(apt)
        apt.building = self
 
    def add_room(self, room):
        self.rooms.append(room)
        room.set_building(self)
 
    # ── level management ──────────────────────────────────────────────────
 
    def add_level(self, level):
        """Add level wrapper to building instance"""
        self.levels.append(level)
 
    # ── dunder ────────────────────────────────────────────────────────────
 
    def __str__(self):
        return u"BuildingWrapper({!r}, apts={}, rooms={})".format(
            self.display_name, self.apartments, len(self.rooms)
        )
 
    def __repr__(self):
        return self.__str__()
    
