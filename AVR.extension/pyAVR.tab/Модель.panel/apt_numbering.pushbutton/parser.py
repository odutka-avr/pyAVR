# -*- coding: utf-8 -*-
"""
parser.py
=========
Parses Revit documents into BuildingWrapper objects populated with
LevelWrappers, RoomWrappers, DoorWrappers and all cluster types:

    ApartmentWrapper       – КвартириВхід boundary, Квартири interior
    HotelRoomWrapper       – ГотельнийНомерВхід boundary, ГотельнийНомер interior
    CommercialWrapper      – КомерціяВхід boundary, Комерція interior
    MZKClusterWrapper      – one per level, all common-area rooms
    StorageWrapper         – single room behind Комірки door
"""

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    BuiltInParameter,
    DesignOption,
    ElementId,
    Phase,
    Level,
    SpatialElementBoundaryOptions,
    FamilyInstance,
    Element,
)

from pyrevit import script
import room_names
from shared_parameters import Shared_parameters

from wrappers import (
    BuildingWrapper,
    DesignOptionWrapper,
    DoorCategory,
    DoorWrapper,
    LevelWrapper,
    RoomWrapper,
    ApartmentWrapper,
    HotelRoomWrapper,
    CommercialWrapper,
    MZKClusterWrapper,
    StorageWrapper,
)

logger = script.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
#  DESIGN OPTION HELPERS  (unchanged)
# ═══════════════════════════════════════════════════════════════════════════

def _get_element_design_option_id(element):
    p = element.get_Parameter(BuiltInParameter.DESIGN_OPTION_ID)
    if p is None:
        return ElementId.InvalidElementId
    return p.AsElementId()


def _element_matches_design_option(element, do_wrapper):
    elem_do_id = _get_element_design_option_id(element)
    if do_wrapper.is_main_model:
        return elem_do_id == ElementId.InvalidElementId
    if do_wrapper.element_id is None:
        return False
    return elem_do_id == do_wrapper.element_id


# ═══════════════════════════════════════════════════════════════════════════
#  PHASE HELPER  (unchanged)
# ═══════════════════════════════════════════════════════════════════════════

def _get_last_phase(doc):
    phases = list(
        FilteredElementCollector(doc).OfClass(Phase).ToElements()
    )
    if not phases:
        return None
    return phases[-1]


# ═══════════════════════════════════════════════════════════════════════════
#  ROOM SEPARATION LINES HELPER  (unchanged)
# ═══════════════════════════════════════════════════════════════════════════

def get_room_separation_lines(doc, r):
    """Return list of ElementIds of Room Separation Lines on room r's boundary."""
    boundaries = r.room_el.GetBoundarySegments(SpatialElementBoundaryOptions())
    sep_lines = []
    for loop in boundaries:
        for seg in loop:
            elem = doc.GetElement(seg.ElementId)
            if (elem and elem.Category and
                    elem.Category.Id.IntegerValue == int(
                        BuiltInCategory.OST_RoomSeparationLines)):
                sep_lines.append(elem.Id)
    return sep_lines


# ═══════════════════════════════════════════════════════════════════════════
#  ADJACENCY BUILDER  –  shared by all cluster passes
# ═══════════════════════════════════════════════════════════════════════════

def _build_adjacency(doc, rooms, interior_doors):
    """
    Build an undirected adjacency graph for a set of rooms connected by
    interior_doors.  Falls back to Room Separation Lines for rooms with
    empty adjacency sets (same logic as existing _build_clusters).

    Args:
        doc            : Revit Document (needed for sep-line lookup)
        rooms          : iterable of RoomWrapper – nodes of the graph
        interior_doors : iterable of DoorWrapper – interior edges only
                         (entrance / boundary doors must be excluded before
                          calling this function)

    Returns:
        dict { RoomWrapper: set(RoomWrapper) }
    """
    room_set = set(rooms)
    adj = {r: set() for r in room_set}

    # ── door edges ────────────────────────────────────────────────────────
    for dw in interior_doors:
        fr = dw.from_room
        to = dw.to_room
        if fr in adj and to in adj:
            adj[fr].add(to)
            adj[to].add(fr)

    # ── separation-line fallback for isolated rooms ───────────────────────
    unconnected = {r for r in adj if not adj[r]}
    for r in unconnected:
        r_sep = get_room_separation_lines(doc, r)
        if not r_sep:
            continue
        for other in room_set:
            if other is r:
                continue
            other_sep = get_room_separation_lines(doc, other)
            if not other_sep:
                continue
            common = set(r_sep) & set(other_sep)
            if common:
                adj[r].add(other)
                adj[other].add(r)

    return adj


def _bfs_components(adj):
    """
    Run BFS over an adjacency dict and return a list of connected
    components, each component being a set of RoomWrapper objects.
    """
    visited    = set()
    components = []

    for start in adj:
        if start in visited:
            continue
        component = set()
        queue = [start]
        while queue:
            curr = queue.pop()
            if curr in visited:
                continue
            visited.add(curr)
            component.add(curr)
            for nbr in adj.get(curr, set()):
                if nbr not in visited:
                    queue.append(nbr)
        components.append(component)

    return components


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN PARSER CLASS
# ═══════════════════════════════════════════════════════════════════════════

class DocumentParser(object):
    """
    Parses one Revit document into a fully populated BuildingWrapper.

    Usage::
        parser = DocumentParser(building_wrapper)
        parser._parse_design_options()
        # user picks design option …
        building_wrapper.design_option = chosen_do_wrapper
        parser.parse()
    """

    def __init__(self, building_wrapper):
        self.building = building_wrapper
        self.building.parser = self
        self.doc      = building_wrapper.doc
        self.phase    = _get_last_phase(self.doc)

        self.design_options = self._parse_design_options()

        self._room_by_id = {}
        self._doors      = []
        self._levels     = {}
        self.hallways    = []

        self.adj = None   # exposed for debugging

        self.COMMON_USE_STAIRS_IDENTIFIER = "Під'їзд"

    # ══════════════════════════════════════════════════════════════════════
    #  DESIGN OPTIONS
    # ══════════════════════════════════════════════════════════════════════

    def _parse_design_options(self):
        do_els = list(
            FilteredElementCollector(self.doc)
            .OfClass(DesignOption)
            .ToElements()
        )
        wrappers = [DesignOptionWrapper(None)]
        for do in do_els:
            wrappers.append(DesignOptionWrapper(do))
        logger.debug("Document %r has %d design option(s).",
                     self.doc.Title, len(wrappers))
        self.design_options = wrappers
        self.building.design_options = wrappers
        return wrappers

    # ══════════════════════════════════════════════════════════════════════
    #  PUBLIC PARSE ENTRY POINT
    # ══════════════════════════════════════════════════════════════════════

    def parse(self):
        if self.building.design_option is None:
            raise ValueError(
                "Set building_wrapper.design_option before calling parse()."
            )
        logger.debug("Parsing document: %r (DO: %s)",
                     self.doc.Title, self.building.design_option.name)

        self._parse_levels()
        self._collect_rooms()
        self._collect_doors()
        self._resolve_door_rooms()
        self._get_hallways()
        self._build_clusters()
        self._get_stairs()
        self._get_elevators()

        logger.info(
            "Parsed %r → %d rooms, %d apartments, %d untracked.",
            self.doc.Title,
            len(self.building.rooms),
            len(self.building.apartments),
            len(self.building.untracked_rooms),
        )
        return self.building

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 0: LEVELS
    # ══════════════════════════════════════════════════════════════════════

    def _parse_levels(self):
        level_collector = (FilteredElementCollector(self.doc)
                           .OfClass(Level).ToElements())
        for lvl in level_collector:
            lvl_id = lvl.Id
            if lvl_id not in self._levels:
                lvl_w = LevelWrapper(lvl)
                self._levels[lvl_id] = lvl_w
                self.building.add_level(lvl_w)
        self.building.levels.sort(key=lambda x: x.elevation)

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 1: ROOMS
    # ══════════════════════════════════════════════════════════════════════

    def _collect_rooms(self):
        do_wrapper = self.building.design_option
        collector  = (FilteredElementCollector(self.doc)
                      .OfCategory(BuiltInCategory.OST_Rooms)
                      .WhereElementIsNotElementType())
        skipped = 0
        for room_el in collector:
            if room_el.Area <= 0:
                skipped += 1
                continue
            if not _element_matches_design_option(room_el, do_wrapper):
                skipped += 1
                continue
            rw = RoomWrapper(room_el, source_doc=self.doc)
            lvl_wrapper = self._levels.get(room_el.LevelId)
            rw.set_level(lvl_wrapper)
            self._room_by_id[room_el.Id] = rw
            self.building.add_room(rw)

        logger.debug("Rooms: accepted=%d, skipped=%d",
                     len(self.building.rooms), skipped)

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 2: DOORS
    # ══════════════════════════════════════════════════════════════════════

    def _collect_doors(self):
        if self.phase is None:
            logger.warning("No phase found in %r – FromRoom/ToRoom may be empty.",
                           self.doc.Title)

        for category in (BuiltInCategory.OST_Doors, BuiltInCategory.OST_Windows):
            collector = (FilteredElementCollector(self.doc)
                         .OfCategory(category)
                         .WhereElementIsNotElementType())
            for el in collector:
                dw = DoorWrapper(el, phase=self.phase, source_doc=self.doc)
                self._doors.append(dw)
                lvl_wrapper = self._levels.get(el.LevelId)
                if lvl_wrapper:
                    dw.set_level(lvl_wrapper)

        # populate building-level entrance list (backward compat)
        self.building.all_doors   = self._doors
        self.building.apt_entrances = [d for d in self._doors if d.is_apt_entrance]

        logger.debug(
            "Doors collected: %d total, %d apt entrances, %d hotel entrances, "
            "%d commercial entrances, %d storage, %d mzk, %d building entrances.",
            len(self._doors),
            sum(1 for d in self._doors if d.is_apt_entrance),
            sum(1 for d in self._doors if d.is_hotel_entrance),
            sum(1 for d in self._doors if d.is_commercial_entrance),
            sum(1 for d in self._doors if d.is_storage_entrance),
            sum(1 for d in self._doors if d.is_mzk),
            sum(1 for d in self._doors if d.is_building_entrance),
        )

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 3: RESOLVE DOOR ROOMS
    # ══════════════════════════════════════════════════════════════════════

    def _resolve_door_rooms(self):
        for dw in self._doors:
            try:
                if self.phase:
                    from_el = dw.door_el.FromRoom[self.phase]
                    to_el   = dw.door_el.ToRoom[self.phase]
                else:
                    from_el = dw.door_el.FromRoom
                    to_el   = dw.door_el.ToRoom
            except Exception as ex:
                logger.debug("Door id=%s FromRoom/ToRoom error: %s",
                             dw.element_id, ex)
                continue
            if from_el is not None:
                dw.from_room = self._room_by_id.get(from_el.Id)
            if to_el is not None:
                dw.to_room = self._room_by_id.get(to_el.Id)

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 4: GET HALLWAYS
    # ══════════════════════════════════════════════════════════════════════

    def _get_hallways(self):
        """
        Identify MZK (common hallway) rooms on each level.

        A room is a hallway when it appears on MORE THAN ONE side of
        apartment-entrance or hotel-entrance doors, OR when more than one
        storage-entrance door is connected to it.

        The method also handles the case where adjacent hallway rooms hide
        some apartment / hotel entrance doors (the cascade-search block).

        Results are stored in lvl.hallways and self.hallways, and each
        room is marked via room.mark_as_hallway().
        """
        for lvl in self.building.levels:
            logger.debug("\n\n=== _get_hallways LVL: %s", lvl)

            # ── collect entrance doors that imply hallways ─────────────
            # Both apt entrances AND hotel entrances define hallways the
            # same way.  Storage entrances use a different count rule.
            unit_entrance_doors = lvl.apt_entrance_doors + lvl.hotel_entrance_doors
            storage_doors       = lvl.storage_entrance_doors

            # doors that are NOT unit entrances (used in cascade search)
            other_doors = set(lvl.doors).difference(set(unit_entrance_doors))

            # ── count room appearances across unit entrance from/to pairs ─
            from_to_counter = []
            for d in unit_entrance_doors:
                from_to_counter.append(d.from_room)
                from_to_counter.append(d.to_room)

            room_occurrences = {}
            for r in from_to_counter:
                if r is not None and r not in room_occurrences:
                    room_occurrences[r] = from_to_counter.count(r)

            logger.debug("Room occurrences across unit entrance doors: %s",
                         room_occurrences)

            # rooms appearing >1 time are hallways (connected to multiple units)
            hallway_occurrences = {r: v for r, v in room_occurrences.items()
                                   if v > 1}
            hallways            = list(hallway_occurrences.keys())
            num_included        = sum(hallway_occurrences.values())

            # ── storage: if >1 storage door connects to same room → hallway ─
            storage_room_counter = {}
            for d in storage_doors:
                # the MZK side is whichever room is already known to be a
                # hallway candidate; if neither is yet, count both
                for candidate in (d.from_room, d.to_room):
                    if candidate is None:
                        continue
                    storage_room_counter[candidate] = (
                        storage_room_counter.get(candidate, 0) + 1)

            for r, count in storage_room_counter.items():
                if count > 1 and r not in hallways:
                    hallways.append(r)
                    logger.debug("Added %s as hallway via storage door count=%d",
                                 r, count)

            # ── cascade: some apt/hotel doors lead into adjacent rooms ────
            if num_included < len(unit_entrance_doors):
                logger.debug(
                    "Some unit entrance doors not yet covered – cascade search.")

                all_hallway_doors = [
                    d for d in other_doors
                    if (d.from_room in hallways) or (d.to_room in hallways)
                ]

                not_included = [
                    d for d in unit_entrance_doors
                    if not ((d.from_room in hallways) or (d.to_room in hallways))
                ]
                logger.debug("Not included unit entrance doors: %s", not_included)

                for apt_door in not_included:
                    connected = (apt_door.from_room, apt_door.to_room)
                    for h_door in all_hallway_doors:
                        if h_door.from_room in connected and \
                                h_door.from_room not in hallways:
                            hallways.append(h_door.from_room)
                            num_included += 1
                        elif h_door.to_room in connected and \
                                h_door.to_room not in hallways:
                            hallways.append(h_door.to_room)
                            num_included += 1

                logger.debug(
                    "After cascade: hallways=%d, included=%d, entrances=%d",
                    len(hallways), num_included, len(unit_entrance_doors))

            # ── mark and store ────────────────────────────────────────────
            hallways = [h.mark_as_hallway() for h in hallways if h is not None]
            logger.debug("Hallways after parse: {}".format(hallways))
            lvl.add_hallways(hallways)
            self.hallways.extend(hallways)

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 5: BUILD ALL CLUSTERS
    # ══════════════════════════════════════════════════════════════════════

    def _build_clusters(self):
        """
        Orchestrate all cluster-building passes in dependency order:

            1. Apartment clusters  (uses hallways from _get_hallways)
            2. Hotel room clusters (same logic as apartments)
            3. Commercial clusters (connected components via Комерція doors)
            4. MZK cluster         (one per level; extends get_hallways result)
            5. Storage clusters    (single-room; Комірки boundary)

        The existing adjacency / separation-line logic is factored into
        _build_adjacency() and _bfs_components() so each pass reuses it.
        """
        # Order matters: MZK must be fully resolved before commercial and
        # storage so we know which side of each entrance door is MZK.
        self._build_apartment_clusters()
        self._build_hotel_clusters()
        self._build_mzk_clusters()       # must come before commercial + storage
        self._build_commercial_clusters()
        self._build_storage_clusters()

        logger.info(
            "Clusters → %d apartments, %d hotel rooms, %d commercial, "
            "%d storage, %d untracked.",
            len(self.building.apartments),
            sum(len(lvl.hotel_rooms)         for lvl in self.building.levels),
            sum(len(lvl.commercial_clusters) for lvl in self.building.levels),
            sum(len(lvl.storage_clusters)    for lvl in self.building.levels),
            len(self.building.untracked_rooms),
        )

    # ──────────────────────────────────────────────────────────────────────
    #  5a. APARTMENT CLUSTERS
    # ──────────────────────────────────────────────────────────────────────

    def _build_apartment_clusters(self):
        """
        Identical logic to the original _build_clusters() — kept as close
        as possible to the original, just extracted into its own method.

        Boundary doors : КвартириВхід
        Interior doors : everything that is NOT an entrance of any kind
                         AND has both from_room and to_room in the room set
        Seed rooms     : the non-hallway side of each КвартириВхід door
        """
        logger.debug("_build_apartment_clusters: start")

        # ── seed rooms (non-hallway side of КвартириВхід) ─────────────────
        apt_seed_ids = set()
        for dw in self.building.apt_entrances:
            if dw.from_room in self.hallways:
                seed = dw.to_room
                if seed:
                    apt_seed_ids.add(seed.element_id)
                    seed.name = room_names.APT_HALLWAY
                    seed.is_apt_hallway = True
            elif dw.to_room in self.hallways:
                seed = dw.from_room
                if seed:
                    apt_seed_ids.add(seed.element_id)
                    seed.name = room_names.APT_HALLWAY
                    seed.is_apt_hallway = True

        logger.debug("Apartment seed rooms: %d", len(apt_seed_ids))

        # ── interior doors: not any kind of entrance, both rooms known ────
        interior_doors = [
            dw for dw in self._doors
            if not dw.is_any_entrance
            and dw.from_room is not None
            and dw.to_room   is not None
        ]

        # ── adjacency graph over ALL rooms ────────────────────────────────
        adj = _build_adjacency(self.doc, self.building.rooms, interior_doors)
        self.adj = adj  # expose for debugging

        # ── BFS components ────────────────────────────────────────────────
        components = _bfs_components(adj)
        logger.debug("Connected components (apt pass): %d", len(components))

        # ── classify ──────────────────────────────────────────────────────
        for component in components:
            id_set = {r.element_id for r in component}
            if id_set & apt_seed_ids:
                apt = ApartmentWrapper()
                for rw in component:
                    apt.add_room(rw)
                apt.set_level(self._get_component_level(apt.rooms))
                self.building.add_apartment(apt)
                logger.debug("Apartment: %s", apt)
            else:
                for rw in component:
                    self.building.untracked_rooms.append(rw)

        logger.debug("_build_apartment_clusters: %d apartments, %d untracked",
                     len(self.building.apartments),
                     len(self.building.untracked_rooms))

    # ──────────────────────────────────────────────────────────────────────
    #  5b. HOTEL ROOM CLUSTERS
    # ──────────────────────────────────────────────────────────────────────

    def _build_hotel_clusters(self):
        """
        Mirrors apartment cluster logic exactly, using:
            Boundary : ГотельнийНомерВхід
            Interior : ГотельнийНомер
            Seeds    : non-hallway side of each ГотельнийНомерВхід door
        """
        logger.debug("_build_hotel_clusters: start")

        hotel_entrance_doors = [d for d in self._doors if d.is_hotel_entrance]
        if not hotel_entrance_doors:
            logger.debug("_build_hotel_clusters: no hotel entrance doors found")
            return

        # ── seeds ─────────────────────────────────────────────────────────
        hotel_seed_ids = set()
        for dw in hotel_entrance_doors:
            if dw.from_room in self.hallways:
                seed = dw.to_room
            elif dw.to_room in self.hallways:
                seed = dw.from_room
            else:
                # neither side is a known hallway – use to_room as convention
                seed = dw.to_room
            if seed:
                hotel_seed_ids.add(seed.element_id)
                seed.is_hotel_hallway = True

        logger.debug("Hotel seed rooms: %d", len(hotel_seed_ids))

        # ── collect rooms that could belong to hotel rooms ────────────────
        # A room belongs to a hotel cluster if it is reachable from a seed
        # through ГотельнийНомер doors.  We only traverse hotel-inner doors
        # so we don't bleed into apartments.
        hotel_inner_doors = [d for d in self._doors if d.is_hotel_inner
                             and d.from_room is not None
                             and d.to_room   is not None]

        # candidate rooms: seeds + anything reachable via hotel-inner doors
        # Start with all rooms not already in an apartment
        apt_rooms = set()
        for apt in self.building.apartments:
            for r in apt.rooms:
                apt_rooms.add(r)

        candidate_rooms = [r for r in self.building.rooms
                           if r not in apt_rooms and r not in self.hallways]

        adj = _build_adjacency(self.doc, candidate_rooms, hotel_inner_doors)
        components = _bfs_components(adj)

        for component in components:
            id_set = {r.element_id for r in component}
            if id_set & hotel_seed_ids:
                hr = HotelRoomWrapper()
                for rw in component:
                    hr.add_room(rw)
                hr.set_level(self._get_component_level(hr.rooms))
                logger.debug("HotelRoom: %s", hr)

        logger.debug("_build_hotel_clusters: done")


    # ──────────────────────────────────────────────────────────────────────
    #  5d. MZK CLUSTER  (one per level)
    # ──────────────────────────────────────────────────────────────────────

    def _build_mzk_clusters(self):
        """
        Build one MZKClusterWrapper per level.

        Seeds (in priority order):
          1. Rooms already identified by _get_hallways() (apt/hotel hallways).
          2. Rooms on the inside of БудВхід (building entrance) doors.
             The "inside" is whichever from_room/to_room is not None
             (exterior side has no room).

        Extension:
          Connect additional rooms via МЗК doors and the Room Separation
          Line fallback (same logic as all other cluster passes).

        After this method completes, self.hallways and every lvl.hallways
        are updated to include the newly discovered БудВхід rooms so that
        _build_commercial_clusters and _build_storage_clusters can use
        the fully resolved MZK set to determine which side of their
        entrance doors is inside the cluster.
        """
        logger.debug("_build_mzk_clusters: start")

        for lvl in self.building.levels:
            # ── seed set: rooms from _get_hallways ────────────────────────
            seed_hallways = set(lvl.hallways)

            # ── extend seeds: rooms behind БудВхід doors ──────────────────
            # A БудВхід door has one side exterior (from_room or to_room is
            # None).  The non-None side is always an MZK room.
            for d in lvl.building_entrance_doors:
                inside = None
                if d.from_room is not None and d.to_room is None:
                    inside = d.from_room
                elif d.to_room is not None and d.from_room is None:
                    inside = d.to_room
                # else:
                #     # both sides resolved: take the one not yet in apartments
                #     apt_rooms = set()
                #     for apt in self.building.apartments:
                #         apt_rooms.update(apt.rooms)
                #     if d.from_room not in apt_rooms:
                #         inside = d.from_room
                #     elif d.to_room not in apt_rooms:
                #         inside = d.to_room

                if inside is not None and inside not in seed_hallways:
                    seed_hallways.add(inside)
                    inside.mark_as_hallway()
                    # keep parser-level list and level list in sync
                    if inside not in self.hallways:
                        self.hallways.append(inside)
                    if inside not in lvl.hallways:
                        lvl.hallways.append(inside)
                    logger.debug(
                        "_build_mzk_clusters: БудВхід seed added %s", inside)

            if not seed_hallways:
                logger.debug("_build_mzk_clusters: no seeds on %s – skip", lvl)

            # ── МЗК doors: interior edges connecting MZK rooms ────────────
            mzk_inner_doors = [d for d in lvl.mzk_doors
                               if d.from_room is not None
                               and d.to_room   is not None]

            # candidate rooms: seeds + rooms touched by МЗК doors
            mzk_adjacent = set()
            for d in mzk_inner_doors:
                mzk_adjacent.add(d.from_room)
                mzk_adjacent.add(d.to_room)

            candidate_rooms = seed_hallways | mzk_adjacent

            adj        = _build_adjacency(self.doc, candidate_rooms,
                                          mzk_inner_doors)
            components = _bfs_components(adj)

            # All components containing at least one seed form the single
            # MZK cluster for this level.
            mzk_cluster = MZKClusterWrapper()

            for component in components:
                if component:
                    for rw in component:
                        mzk_cluster.add_room(rw)
                        # ensure every room in the expanded cluster is also
                        # in self.hallways so downstream passes see it
                        if rw not in self.hallways:
                            self.hallways.append(rw)
                        if rw not in lvl.hallways:
                            lvl.hallways.append(rw)

            if mzk_cluster.rooms:
                mzk_cluster.set_level(lvl)
                logger.debug("MZK cluster on %s: %d rooms, rooms: %s",
                             lvl, mzk_cluster.room_count, mzk_cluster.rooms)
            else:
                logger.debug("_build_mzk_clusters: empty cluster on %s", lvl)

        logger.debug("_build_mzk_clusters: done")


    # ──────────────────────────────────────────────────────────────────────
    #  5c. COMMERCIAL CLUSTERS
    # ──────────────────────────────────────────────────────────────────────

    def _build_commercial_clusters(self):
        """
        Commercial clusters use:
            Boundary : КомерціяВхід  (marks which rooms are inside a cluster)
            Interior : Комерція       (connects rooms within a cluster)

        Multiple КомерціяВхід doors can lead into the same connected
        component — they all map to ONE CommercialWrapper.
        """
        logger.debug("_build_commercial_clusters: start")

        commercial_entrance_doors = [d for d in self._doors
                                     if d.is_commercial_entrance]
        commercial_inner_doors    = [d for d in self._doors
                                     if d.is_commercial_inner
                                     and d.from_room is not None
                                     and d.to_room   is not None]

        if not commercial_entrance_doors:
            logger.debug("_build_commercial_clusters: no commercial entrance doors")
            return

        # ── collect rooms already claimed ─────────────────────────────────
        claimed = set()
        for apt in self.building.apartments:
            claimed.update(apt.rooms)
        for lvl in self.building.levels:
            for hr in lvl.hotel_rooms:
                claimed.update(hr.rooms)
        claimed.update(self.hallways)
        claimed.update()

        # ── seed rooms: non-hallway side of КомерціяВхід ──────────────────
        commercial_seed_ids = set()
        for dw in commercial_entrance_doors:
            # the inside of the commercial unit is the room NOT in hallways
            if dw.from_room in self.hallways or dw.from_room in claimed or dw.from_room is None:
                seed = dw.to_room
            else:
                seed = dw.from_room
            if seed and seed not in claimed:
                commercial_seed_ids.add(seed.element_id)
                seed.is_commercial = True

        logger.debug("Commercial seed rooms: %d", len(commercial_seed_ids))

        # ── candidate rooms: not yet claimed ──────────────────────────────
        candidate_rooms = [r for r in self.building.rooms
                           if r not in claimed]

        adj        = _build_adjacency(self.doc, candidate_rooms,
                                      commercial_inner_doors)
        components = _bfs_components(adj)

        for component in components:
            id_set = {r.element_id for r in component}
            if id_set & commercial_seed_ids:
                cw = CommercialWrapper()
                for rw in component:
                    cw.add_room(rw)
                cw.set_level(self._get_component_level(cw.rooms))

                # attach all КомерціяВхід doors that led into this cluster
                for dw in commercial_entrance_doors:
                    inside = dw.to_room if (
                        dw.from_room in self.hallways or
                        dw.from_room not in {r for r in candidate_rooms}
                    ) else dw.from_room
                    if inside in component:
                        cw.entrance_doors.append(dw)

                logger.debug("Commercial: %s", cw)

        logger.debug("_build_commercial_clusters: done")


    # ──────────────────────────────────────────────────────────────────────
    #  5e. STORAGE CLUSTERS
    # ──────────────────────────────────────────────────────────────────────

    def _build_storage_clusters(self):
        """
        Each Комірки door leads to one storage room.
        One StorageWrapper is created per unique room on the inside of a
        Комірки door.  If the same room appears behind multiple Комірки
        doors, it still becomes a single StorageWrapper.
        """
        logger.debug("_build_storage_clusters: start")

        # collect rooms already claimed so we don't double-assign
        claimed = set(self.hallways)
        for apt in self.building.apartments:
            claimed.update(apt.rooms)
        for lvl in self.building.levels:
            for hr in lvl.hotel_rooms:
                claimed.update(hr.rooms)
            for cw in lvl.commercial_clusters:
                claimed.update(cw.rooms)

        seen_storage_rooms = set()   # avoid creating duplicate wrappers

        for dw in self._doors:
            if not dw.is_storage_entrance:
                continue
            if dw.from_room is None and dw.to_room is None:
                continue

            # storage room is the side that is NOT in hallways / claimed
            if dw.from_room in self.hallways or dw.from_room in claimed:
                storage_room = dw.to_room
            else:
                storage_room = dw.from_room

            if storage_room is None:
                continue
            if storage_room in seen_storage_rooms:
                continue
            if storage_room in claimed:
                continue

            seen_storage_rooms.add(storage_room)

            sw = StorageWrapper()
            sw.add_room(storage_room)
            sw.set_level(storage_room.level)

            logger.debug("Storage cluster: %s", sw)

        logger.debug("_build_storage_clusters: %d total",
                     sum(len(lvl.storage_clusters)
                         for lvl in self.building.levels))

    # ══════════════════════════════════════════════════════════════════════
    #  STAIRS & ELEVATORS  (unchanged)
    # ══════════════════════════════════════════════════════════════════════

    def _get_stairs(self):
        stair_collector = (FilteredElementCollector(self.doc)
                           .OfCategory(BuiltInCategory.OST_Stairs))
        for stair in stair_collector:
            st_base_lvl_id = stair.get_Parameter(
                BuiltInParameter.STAIRS_BASE_LEVEL_PARAM)
            if st_base_lvl_id:
                
                lvl_id = st_base_lvl_id.AsElementId()

                pivot = bool(stair.get_Parameter(Shared_parameters.BASE_STAIRS).AsInteger())
                type_name = Element.Name.GetValue(self.doc.GetElement(stair.GetTypeId()))
                common_use = self.COMMON_USE_STAIRS_IDENTIFIER in type_name
                
                self._levels[lvl_id].add_stairs(stair, is_pivot=pivot, is_common=common_use)
                logger.debug("Stair: %s, Base Level: %s (is pivot: %s, is common use: %s)",
                                stair.Id, self._levels[lvl_id], pivot, common_use)
                

    def _get_elevators(self):
        collector = (FilteredElementCollector(self.doc)
                     .OfCategory(BuiltInCategory.OST_SpecialityEquipment))
        for el in collector:
            if isinstance(el, FamilyInstance):
                if u"Ліфт" in el.Symbol.Family.Name:
                    self.building.add_elevator(el)
        logger.debug("Elevators: %s", self.building.elevators)

    # ══════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════

    def _get_component_level(self, rooms):
        """Return the level of the first room that has one."""
        for room in rooms:
            if room.level:
                return room.level
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  CONVENIENCE FUNCTIONS  (unchanged public API)
# ═══════════════════════════════════════════════════════════════════════════

def build_buildings_from_docs(host_doc, link_instances):
    buildings = []
    host_bw = BuildingWrapper(host_doc, is_link=False)
    parser  = DocumentParser(host_bw)
    host_bw._design_options = parser._parse_design_options()
    buildings.append(host_bw)

    for link_inst in link_instances:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            logger.warning("Link %s has no loaded document – skipping.",
                           link_inst.Id)
            continue
        bw     = BuildingWrapper(link_doc, is_link=True)
        parser = DocumentParser(bw)
        bw._design_options = parser._parse_design_options()
        buildings.append(bw)

    return buildings


def parse_selected_buildings(buildings):
    for bw in buildings:
        if bw.design_option is None:
            logger.warning(
                "BuildingWrapper %r has no design option – skipping.",
                bw.display_name)
            continue
        parser = DocumentParser(bw)
        parser.parse()
    return buildings