# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

import clr

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (BuiltInParameter, StorageType, XYZ)

from value_conversion import (convert_feet_to_m,
                              convert_sq_feet_to_hectares,
                              convert_sq_feet_to_sq_m)

from pyrevit import revit, script
from shared_parameters import Shared_parameters
from System import Guid
import math

logger = script.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
#  DOOR CATEGORIES  –  values of AVR_Категорія прорізу type parameter
# ═══════════════════════════════════════════════════════════════════════════

class DoorCategory(object):
    """String constants for AVR_Категорія прорізу type parameter values."""
    APT_ENTRANCE        = u"КвартириВхід"        # apartment entrance boundary
    APT_INNER           = u"Квартири"             # inner apartment door (interior edge)
    COMMERCIAL_ENTRANCE = u"КомерціяВхід"         # commercial entrance boundary
    COMMERCIAL_INNER    = u"Комерція"             # inner commercial door (interior edge)
    HOTEL_ENTRANCE      = u"ГотельнийНомерВхід"  # hotel room entrance boundary
    HOTEL_INNER         = u"ГотельнийНомер"       # inner hotel room door (interior edge)
    MZK                 = u"МЗК"                  # common area / hallway door (interior)
    STORAGE             = u"Комірки"              # storage room entrance boundary
    BUILDING_ENTRANCE   = u"БудинокВхід"              # building entrance → next room is MZK


# ═══════════════════════════════════════════════════════════════════════════
#  DESIGN OPTION WRAPPER
# ═══════════════════════════════════════════════════════════════════════════

class DesignOptionWrapper:
    def __init__(self, do_el):
        self.do_el = do_el

    @property
    def name(self):
        if self.do_el:
            return self.do_el.Name
        return "Main model"

    @property
    def is_main_model(self):
        return self.do_el is None

    @property
    def element_id(self):
        return self.do_el.Id if self.do_el else None

    def __str__(self):
        return "DesignOptionWrapper({})".format(self.name)

    def __repr__(self):
        return self.__str__()


# ═══════════════════════════════════════════════════════════════════════════
#  ROOM WRAPPER
# ═══════════════════════════════════════════════════════════════════════════

class RoomWrapper:
    def __init__(self, room_el, source_doc=None):
        self.room_el            = room_el
        self.element_id         = room_el.Id
        self.level              = None
        self.apartment          = None
        self.building           = None
        self.development_phase  = None
        self.source_doc         = source_doc

        # cluster membership flags
        self.is_hallway         = False   # MZK / common hallway room
        self.is_apt_hallway     = False   # room directly behind apt entrance (Передпокій)
        self.is_hotel_hallway   = False   # room directly behind hotel entrance
        self.is_commercial      = False
        self.is_storage         = False

        self.name               = ""
        self.number             = None

    # ── shared / built-in parameter accessors ────────────────────────────

    @property
    def room_type_param(self):
        return self.room_el.get_Parameter(Shared_parameters.ROOM_TYPE)

    @property
    def room_category_param(self):
        return self.room_el.get_Parameter(Shared_parameters.ROOM_CATEGORY)

    @property
    def room_department_param(self):
        return self.room_el.get_Parameter(BuiltInParameter.ROOM_DEPARTMENT)

    @property
    def room_name_param(self):
        return self.room_el.get_Parameter(BuiltInParameter.ROOM_NAME)
    
    @property
    def room_number_param(self):
        return self.room_el.get_Parameter(BuiltInParameter.ROOM_NUMBER)

    @property
    def apartment_number_param(self):
        return self.room_el.get_Parameter(Shared_parameters.APARTMENT_NUMBER)
    
    @property
    def room_level_param(self):
        return self.room_el.get_Parameter(Shared_parameters.LEVEL)

    @property
    def building_phase_id_param(self):
        return self.room_el.LookupParameter(Shared_parameters.BUILDING_DEV_PHASE_NUMBER)

    @property
    def building_section_id_param(self):
        return self.room_el.get_Parameter(Shared_parameters.BUILDING_SECTION_NUMEBR)

    @property
    def room_base_elevation(self):
        base_offset = self.room_el.get_Parameter(
            BuiltInParameter.ROOM_LOWER_OFFSET).AsDouble()
        return self.level.elevation + base_offset

    @property
    def debug_apartment_number(self):
        return self.room_el.get_Parameter(Shared_parameters.APARTMENT_NUMBER).AsString()

    # ── back-link setters ─────────────────────────────────────────────────

    def set_level(self, level_wrapper):
        self.level = level_wrapper
        level_wrapper.add_room(self)

    def set_apartment(self, apt_wrapper):
        self.apartment = apt_wrapper

    def set_building(self, building_wrapper):
        self.building = building_wrapper

    def set_development_phase(self, phase):
        self.development_phase = phase

    def mark_as_hallway(self):
        self.is_hallway = True
        return self

    # ── dunder ────────────────────────────────────────────────────────────

    def __str__(self):
        return u"Room(ID: {}, Number: {}, Lvl: {})".format(
            self.element_id,
            self.apartment_number_param.AsString(),
            self.level.name)

    def __repr__(self):
        return self.__str__()


# ═══════════════════════════════════════════════════════════════════════════
#  DOOR WRAPPER  –  reads AVR_Категорія прорізу type parameter
# ═══════════════════════════════════════════════════════════════════════════

class DoorWrapper(object):
    """
    Wraps a Revit Door / Window FamilyInstance.

    Door category is read from the shared type parameter
    AVR_Категорія прорізу.  All is_* helpers derive from that value so
    category logic stays in the wrapper and the parser stays clean.
    """

    PARAM_NAME = u"AVR_Категорія прорізу"

    def __init__(self, door_el, phase, source_doc=None):
        self.door_el    = door_el
        self.phase      = phase
        self.source_doc = source_doc

        self.from_room  = None   # RoomWrapper | None
        self.to_room    = None   # RoomWrapper | None
        self.level      = None

    # ── identity ──────────────────────────────────────────────────────────

    @property
    def element_id(self):
        return self.door_el.Id

    @property
    def type_name(self):
        try:
            return self.door_el.Name or u""
        except Exception:
            return u""

    @property
    def family_name(self):
        try:
            return self.door_el.Symbol.Family.Name or u""
        except Exception:
            return u""

    @property
    def full_type_name(self):
        return u"{} :: {}".format(self.family_name, self.type_name)

    # ── AVR_Категорія прорізу ─────────────────────────────────────────────

    @property
    def door_category(self):
        """
        Read AVR_Категорія прорізу from the door type (Symbol).
        Returns the raw string value, or empty string if missing.
        Instance is checked first, then type, consistent with Revit priority.
        """
        # try instance parameter
        p = self.door_el.LookupParameter(self.PARAM_NAME)
        if p is None:
            # fall back to type parameter
            try:
                p = self.door_el.Symbol.LookupParameter(self.PARAM_NAME)
            except Exception:
                pass
        if p is None:
            return u""
        if p.StorageType == StorageType.ElementId:
            return p.AsValueString() or u""
        return u""

    # ── category boolean helpers ──────────────────────────────────────────

    @property
    def is_apt_entrance(self):
        return self.door_category == DoorCategory.APT_ENTRANCE

    @property
    def is_apt_inner(self):
        return self.door_category == DoorCategory.APT_INNER

    @property
    def is_hotel_entrance(self):
        return self.door_category == DoorCategory.HOTEL_ENTRANCE

    @property
    def is_hotel_inner(self):
        return self.door_category == DoorCategory.HOTEL_INNER

    @property
    def is_commercial_entrance(self):
        return self.door_category == DoorCategory.COMMERCIAL_ENTRANCE

    @property
    def is_commercial_inner(self):
        return self.door_category == DoorCategory.COMMERCIAL_INNER

    @property
    def is_mzk(self):
        return self.door_category == DoorCategory.MZK

    @property
    def is_storage_entrance(self):
        return self.door_category == DoorCategory.STORAGE

    @property
    def is_building_entrance(self):
        return self.door_category == DoorCategory.BUILDING_ENTRANCE

    @property
    def is_any_entrance(self):
        """True for any door that acts as a cluster boundary."""
        return self.door_category in (
            DoorCategory.APT_ENTRANCE,
            DoorCategory.HOTEL_ENTRANCE,
            DoorCategory.COMMERCIAL_ENTRANCE,
            DoorCategory.STORAGE,
            DoorCategory.BUILDING_ENTRANCE,
        )

    # ── legacy compat (used in _collect_doors log) ────────────────────────

    @property
    def is_apartment_entrance(self):
        """Kept for backward compat with any call sites not yet updated."""
        return self.is_apt_entrance

    # ── level linkage ─────────────────────────────────────────────────────

    def set_level(self, level_wrapper):
        self.level = level_wrapper
        level_wrapper.add_door(self)

    # ── room resolution ───────────────────────────────────────────────────

    def resolve_rooms(self, room_by_id):
        try:
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
        return u"DoorWrapper({!r}, cat={!r})".format(
            self.full_type_name, self.door_category)

    def __repr__(self):
        return self.__str__()


# ═══════════════════════════════════════════════════════════════════════════
#  BASE CLUSTER WRAPPER  –  shared interface for all cluster types
# ═══════════════════════════════════════════════════════════════════════════

class BaseClusterWrapper(object):
    """
    Shared interface for ApartmentWrapper, HotelRoomWrapper,
    CommercialWrapper, MZKClusterWrapper, StorageWrapper.

    All cluster types expose:
        .rooms        list[RoomWrapper]
        .level        LevelWrapper
        .number       int | None
        .centerpoint  Point
    """

    def __init__(self):
        self.rooms    = []
        self.level    = None
        self.number   = None
        self.building = None

    def add_room(self, room_wrapper):
        self.rooms.append(room_wrapper)

    @property
    def room_count(self):
        return len(self.rooms)

    @property
    def centerpoint(self):
        pts = [r.room_el.Location.Point for r in self.rooms
               if r.room_el.Location is not None]
        if not pts:
            return None
        x = sum(p.X for p in pts) / len(pts)
        y = sum(p.Y for p in pts) / len(pts)
        z = self.level.elevation if self.level else pts[0].Z
        return Point(XYZ(x, y, z), apartment=self)

    def set_level(self, level):
        self.level = level

    def __str__(self):
        return u"{}(rooms={}, lvl={}, n={}, rooms={})".format(
            type(self).__name__, self.room_count, self.level, self.number, self.rooms)

    def __repr__(self):
        return self.__str__()


# ═══════════════════════════════════════════════════════════════════════════
#  APARTMENT WRAPPER
# ═══════════════════════════════════════════════════════════════════════════

class ApartmentWrapper(BaseClusterWrapper):
    """Residential apartment cluster."""

    def __init__(self):
        BaseClusterWrapper.__init__(self)
        self.ordered_rooms = []

    def add_room(self, room_wrapper):
        BaseClusterWrapper.add_room(self, room_wrapper)
        room_wrapper.set_apartment(self)

    def set_level(self, level):
        BaseClusterWrapper.set_level(self, level)
        level.apartments.add(self)
    
    def get_ordered_rooms(self):
        ordered = []
        for r in self.rooms:
            if r.is_apt_hallway:
                ordered.append(r)
        
        # add to order all other apt rooms skipping already added hallway
        for r in set(self.rooms) - set(ordered):
            ordered.append(r)
        
        return ordered

    def debug_get_apt_number(self):
        return set(r.debug_apartment_number for r in self.rooms)

    def __str__(self):
        return u"\n\tApartmentWrapper(rooms={}, lvl={}, apt_n={})".format(
            self.room_count, self.level, self.debug_get_apt_number())


# ═══════════════════════════════════════════════════════════════════════════
#  HOTEL ROOM WRAPPER
# ═══════════════════════════════════════════════════════════════════════════

class HotelRoomWrapper(BaseClusterWrapper):
    """
    Hotel room cluster.  Formed identically to ApartmentWrapper —
    ГотельнийНомерВхід as boundary, ГотельнийНомер as interior edges.
    """

    def __init__(self):
        BaseClusterWrapper.__init__(self)

    def add_room(self, room_wrapper):
        BaseClusterWrapper.add_room(self, room_wrapper)
        # mirror the apartment back-link so room knows its cluster
        room_wrapper.apartment = self

    def set_level(self, level):
        BaseClusterWrapper.set_level(self, level)
        level.hotel_rooms.append(self)


# ═══════════════════════════════════════════════════════════════════════════
#  COMMERCIAL WRAPPER
# ═══════════════════════════════════════════════════════════════════════════

class CommercialWrapper(BaseClusterWrapper):
    """
    Commercial cluster.  Multiple КомерціяВхід doors may lead into one
    cluster if the interior spaces are connected via Комерція doors.
    """

    def __init__(self):
        BaseClusterWrapper.__init__(self)
        self.entrance_doors = []   # list[DoorWrapper] – all КомерціяВхід for this cluster

    def add_room(self, room_wrapper):
        BaseClusterWrapper.add_room(self, room_wrapper)
        room_wrapper.is_commercial = True

    def set_level(self, level):
        BaseClusterWrapper.set_level(self, level)
        level.commercial_clusters.append(self)


# ═══════════════════════════════════════════════════════════════════════════
#  MZK CLUSTER WRAPPER  –  one per level, holds all common-area rooms
# ═══════════════════════════════════════════════════════════════════════════

class MZKClusterWrapper(BaseClusterWrapper):
    """
    Common-area / hallway cluster.  All MZK rooms on a level form a single
    cluster connected via МЗК doors and Room Separation Lines.
    """

    def __init__(self):
        BaseClusterWrapper.__init__(self)

    def add_room(self, room_wrapper):
        if room_wrapper not in self.rooms:
            BaseClusterWrapper.add_room(self, room_wrapper)
            room_wrapper.mark_as_hallway()

    def set_level(self, level):
        BaseClusterWrapper.set_level(self, level)
        level.mzk_cluster = self


# ═══════════════════════════════════════════════════════════════════════════
#  STORAGE WRAPPER  –  single-room clusters
# ═══════════════════════════════════════════════════════════════════════════

class StorageWrapper(BaseClusterWrapper):
    """
    Storage room cluster.  Typically a single room behind a Комірки door.
    """

    def __init__(self):
        BaseClusterWrapper.__init__(self)

    def add_room(self, room_wrapper):
        BaseClusterWrapper.add_room(self, room_wrapper)
        room_wrapper.is_storage = True

    def set_level(self, level):
        BaseClusterWrapper.set_level(self, level)
        level.storage_clusters.append(self)


# ═══════════════════════════════════════════════════════════════════════════
#  LEVEL WRAPPER
# ═══════════════════════════════════════════════════════════════════════════

class LevelWrapper(object):
    def __init__(self, level_el):
        self.level_el   = level_el

        # rooms
        self.rooms      = []

        # cluster collections
        self.apartments         = set()          # set[ApartmentWrapper]
        self.hotel_rooms        = []             # list[HotelRoomWrapper]
        self.commercial_clusters= []             # list[CommercialWrapper]
        self.storage_clusters   = []             # list[StorageWrapper]
        self.mzk_cluster        = None           # MZKClusterWrapper | None (one per level)

        # doors – per category for fast lookup in the parser
        self.doors                   = []        # all doors on this level
        self.apt_entrance_doors      = []        # КвартириВхід
        self.hotel_entrance_doors    = []        # ГотельнийНомерВхід
        self.commercial_entrance_doors = []      # КомерціяВхід
        self.storage_entrance_doors  = []        # Комірки
        self.mzk_doors               = []        # МЗК
        self.building_entrance_doors = []        # БудВхід
        self.commercial_inner_doors  = []        # Комерція (interior edges)

        # kept for backward compat with get_hallways() and script.py
        self.apt_entrances      = self.apt_entrance_doors

        # stairs / elevators / misc
        self.pivot_stairs       = []            # list[Stairs] stairs marked by user as pivot for apt numbering, must be only one (script will use the first collected if there are more than one pivot stairs)
        self.common_use_stairs  = []            # list[Stairs] common use stairs
        self.stairs             = []            # list[Stairs] all other stairs that are available on lvl
        self.staircase_rooms    = []            # adds RoomWrapper that interscts stairs 
        self.hallways           = []             # list[RoomWrapper] marked as hallway
        self.ordered_apartments = []

    # ── properties ────────────────────────────────────────────────────────

    @property
    def name(self):
        return self.level_el.Name or u""
    
    @property
    def number(self):
        try:
            return str(int(self.name.split(" ")[-1]))
        except ValueError:
            return self.name

    @property
    def elevation(self):
        return self.level_el.Elevation

    @property
    def element_id(self):
        return self.level_el.Id

    # ── add helpers ───────────────────────────────────────────────────────

    def add_apartment(self, apt):
        self.apartments.add(apt)
        apt.level = self

    def add_room(self, room):
        self.rooms.append(room)

    def add_door(self, door):
        """
        Route door into the correct category list in addition to the
        catch-all self.doors list.
        """
        self.doors.append(door)
        cat = door.door_category

        from wrappers import DoorCategory  # local import avoids circular ref at module level
        if cat == DoorCategory.APT_ENTRANCE:
            self.apt_entrance_doors.append(door)
        elif cat == DoorCategory.HOTEL_ENTRANCE:
            self.hotel_entrance_doors.append(door)
        elif cat == DoorCategory.COMMERCIAL_ENTRANCE:
            self.commercial_entrance_doors.append(door)
        elif cat == DoorCategory.STORAGE:
            self.storage_entrance_doors.append(door)
        elif cat == DoorCategory.MZK:
            self.mzk_doors.append(door)
        elif cat == DoorCategory.BUILDING_ENTRANCE:
            self.building_entrance_doors.append(door)
        elif cat == DoorCategory.COMMERCIAL_INNER:
            self.commercial_inner_doors.append(door)
        # APT_INNER and HOTEL_INNER need no separate list

    # backward-compat shim used in parser._collect_doors
    def add_doors(self, door):
        self.add_door(door)

    def add_stairs(self, stairs, is_pivot=False, is_common=False):
        # check if are pivot point for apt numbering
        if is_pivot:
            self.pivot_stairs.append(stairs)
        # check is pivot is also common or just add to common if is not pivot
        if is_common: 
            self.common_use_stairs.append(stairs)
        # just regular stairs that are not common use ones 
        # and are not used as pivot point for apt numbering
        if not is_pivot and not is_common:
            self.stairs.append(stairs)

    def add_staircase_rooms(self, room):
        self.staircase_rooms.append(room)

    def add_hallways(self, hallways):
        self.hallways.extend(hallways)

    # ── dunder ────────────────────────────────────────────────────────────

    def __str__(self):
        return u"LevelWrapper({}, elev={:.2f})".format(self.name, self.elevation)

    def __repr__(self):
        return self.__str__()


# ═══════════════════════════════════════════════════════════════════════════
#  BUILDING WRAPPER
# ═══════════════════════════════════════════════════════════════════════════

class BuildingWrapper(object):
    def __init__(self, doc, is_link=True):
        self.doc             = doc
        self.dev_phase_id    = None
        self.building_id     = None
        self.link_instance   = is_link

        self.parser          = None
        self.design_options  = []
        self.levels          = []
        self.apartments      = []
        self.rooms           = []
        self.untracked_rooms = []
        self.all_doors       = []
        self.apt_entrances   = []      # КвартириВхід doors (all levels)
        self.elevators       = []
        self.hallways        = []

        self.design_option   = None
        self.order_index     = 0

    @property
    def is_host(self):
        return self.link_instance

    @property
    def display_name(self):
        return self.doc.Title or u"(Current Model)"

    def set_dev_phase_id(self, dev_phase_id):
        self.dev_phase_id = dev_phase_id

    def set_building_id(self, b_id):
        self.building_id = b_id

    def add_apartment(self, apt):
        self.apartments.append(apt)
        apt.building = self

    def add_room(self, room):
        self.rooms.append(room)
        room.set_building(self)

    def add_level(self, level):
        self.levels.append(level)

    def add_elevator(self, elevator):
        self.elevators.append(elevator)

    def __str__(self):
        return u"BuildingWrapper({!r}, apts={}, rooms={})".format(
            self.display_name, self.apartments, len(self.rooms))

    def __repr__(self):
        return self.__str__()


# ═══════════════════════════════════════════════════════════════════════════
#  POINT  –  used by the apartment ordering algorithm
# ═══════════════════════════════════════════════════════════════════════════

class Point:
    def __init__(self, point, is_pivot=False, apartment=None):
        self.point      = point
        self.x          = point.X
        self.y          = point.Y
        self.z          = point.Z

        self.visited    = False
        self.is_first   = False
        self.was_first  = False
        self.is_pivot   = is_pivot
        self.dist_to_pivot = float('inf')

        self.apartment  = apartment

    def distance(self, p):
        return ((self.x - p.x)**2 + (self.y - p.y)**2 + (self.z - p.z)**2) ** 0.5

    def vectorize(self, p):
        return self.point - p.point

    def __str__(self):
        apt = self.apartment.debug_get_apt_number() if self.apartment else "Pivot pt"
        return "p({}, {}, {}, {})".format(self.x, self.y, self.z, apt)

    def __repr__(self):
        return self.__str__()