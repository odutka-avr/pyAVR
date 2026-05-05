# -*- coding: utf-8 -*-
"""
parser.py
=========
Parses Revit documents (host + linked models) into BuildingWrapper objects
populated with LevelWrappers, RoomWrappers, DoorWrappers, and
ApartmentWrappers (connected room clusters).

Apartment detection algorithm
------------------------------
1. Collect all Rooms in the document (filtered to the chosen DesignOption).
2. Collect all Doors; classify each as "apartment entrance" or "interior".
3. Build an undirected adjacency graph:
     nodes  = RoomWrapper
     edges  = interior doors connecting two valid rooms
4. Run BFS / connected-components over the graph.
5. Any component that contains at least one room reachable THROUGH an
   apartment entrance door (i.e. the entrance door's ToRoom side) becomes
   an ApartmentWrapper.
6. Remaining rooms are added to BuildingWrapper.untracked_rooms.

Design option filtering
------------------------
Rooms whose DesignOption does not match the selected one are skipped.
"Main model" rooms have DesignOptionId == ElementId.InvalidElementId.
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
)

from pyrevit import script

from wrappers import (
    BuildingWrapper,
    DesignOptionWrapper,
    DoorWrapper,
    LevelWrapper,
    RoomWrapper,
    ApartmentWrapper,
)

logger = script.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
#  DESIGN OPTION HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _get_element_design_option_id(element):
    """
    Return the DesignOption ElementId for an element,
    or ElementId.InvalidElementId for Main Model elements.
    """
    p = element.get_Parameter(BuiltInParameter.DESIGN_OPTION_ID)
    if p is None:
        return ElementId.InvalidElementId
    return p.AsElementId()


def _element_matches_design_option(element, do_wrapper):
    """
    Return True if ``element`` belongs to the chosen DesignOption.

    - Main Model sentinel → accept elements with no design option.
    - Real DesignOption    → accept elements whose DesignOptionId matches.
    """
    elem_do_id = _get_element_design_option_id(element)
    if do_wrapper.is_main_model:
        return elem_do_id == ElementId.InvalidElementId
    if do_wrapper.element_id is None:
        return False
    return elem_do_id == do_wrapper.element_id


# ═══════════════════════════════════════════════════════════════════════════
#  LAST PHASE HELPER
# ═══════════════════════════════════════════════════════════════════════════

def _get_last_phase(doc):
    """
    Return the last (most recent) Phase element in the document.
    Doors and rooms are queried against this phase so that FromRoom/ToRoom
    reflect the final constructed state.
    """
    phases = list(
        FilteredElementCollector(doc)
        .OfClass(Phase)
        .ToElements()
    )
    if not phases:
        return None
    # Phases are ordered by index; the last one is the most recent.
    return phases[-1]


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN PARSER CLASS
# ═══════════════════════════════════════════════════════════════════════════

class DocumentParser(object):
    """
    Parses one Revit document into a fully populated BuildingWrapper.

    Usage::

        parser = DocumentParser(building_wrapper)
        design_options = parser.parse_design_options()
        # … user picks one …
        building_wrapper.design_option = chosen_do_wrapper
        parser.parse()
    """

    def __init__(self, building_wrapper):
        """
        Args:
            building_wrapper (BuildingWrapper): Target container.
                ``building_wrapper.doc`` is the document to parse.
                ``building_wrapper.design_option`` must be set before
                calling ``parse()``.
        """
        self.building = building_wrapper
        self.building.parser = self
        self.doc      = building_wrapper.doc
        self.phase    = _get_last_phase(self.doc)

        self.design_options = self._parse_design_options()

        # Populated during parse
        self._room_by_id = {}    # ElementId → RoomWrapper
        self._doors      = []    # list[DoorWrapper]
        self._levels     = {}    # ElementId → LevelWrapper

    # ── public: design options ────────────────────────────────────────────

    def _parse_design_options(self):
        """
        Collect all DesignOption elements in the document and wrap them.

        The Main Model sentinel is prepended so it appears first in the UI.

        Returns:
            list[DesignOptionWrapper]
        """
        do_els = list(
            FilteredElementCollector(self.doc)
            .OfClass(DesignOption)
            .ToElements()
        )
        wrappers = [DesignOptionWrapper(None)]           # Main Model first
        for do in do_els:
            wrappers.append(DesignOptionWrapper(do))
        logger.debug("Document %r has %d design option(s).",
                     self.doc.Title, len(wrappers))
        
        self.design_options = wrappers
        self.building.design_options = self.design_options

        logger.debug("===== {}".format(self.building.design_options))

        return self.design_options

    # ── public: full parse ────────────────────────────────────────────────

    def parse(self):
        """
        Execute the full parse sequence:
          0. Collect available levels
          1. Collect rooms (filtered to selected DesignOption).
          2. Collect doors; classify as entrance vs interior.
          3. Resolve door FromRoom/ToRoom references.
          4. Build room adjacency graph.
          5. Find connected components → ApartmentWrappers.
          6. Attach rooms/apartments to LevelWrappers.

        ``building_wrapper.design_option`` must be set before calling this.

        Returns:
            BuildingWrapper: The same object passed to __init__, now populated.
        """
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
        self._build_clusters()
        #self._attach_levels()

        logger.info(
            "Parsed %r → %d rooms, %d apartments, %d untracked.",
            self.doc.Title,
            len(self.building.rooms),
            len(self.building.apartments),
            len(self.building.untracked_rooms),
        )
        return self.building

    # ── step 1: rooms ─────────────────────────────────────────────────────

    def _collect_rooms(self):
        """
        Collect all placed, bounded rooms in the document.
        Filters to the selected DesignOption; skips unplaced/zero-area rooms.
        """
        do_wrapper = self.building.design_option
        collector  = (
            FilteredElementCollector(self.doc)
            .OfCategory(BuiltInCategory.OST_Rooms)
            .WhereElementIsNotElementType()
        )
        count_total   = 0
        count_skipped = 0

        for room_el in collector:
            count_total += 1

            # Skip unplaced or unbounded rooms
            if room_el.Area <= 0:
                count_skipped += 1
                logger.debug("Skipping unplaced room id=%s", room_el.Id)
                continue

            # Design option filter
            if not _element_matches_design_option(room_el, do_wrapper):
                count_skipped += 1
                continue
            
            # create room wrapper instance
            rw = RoomWrapper(room_el, source_doc=self.doc)
            
            # link level wrapper to room, level instance automatically links to room too
            lvl_wrapper = self._levels.get(room_el.LevelId)
            rw.set_level(lvl_wrapper)

            self._room_by_id[room_el.Id] = rw
            self.building.add_room(rw)

        logger.debug(
            "Rooms: total=%d, accepted=%d, skipped=%d",
            count_total, len(self.building.rooms), count_skipped,
        )

    # ── step 2: doors ─────────────────────────────────────────────────────

    def _collect_doors(self):
        """
        Collect all door instances and classify each as:
          - Apartment entrance door  → DoorWrapper.is_apartment_entrance = True
          - Interior door            → False
        """
        if self.phase is None:
            logger.warning("No phase found in %r – door FromRoom/ToRoom may be empty.",
                           self.doc.Title)

        door_collector = (
            FilteredElementCollector(self.doc)
            .OfCategory(BuiltInCategory.OST_Doors)
            .WhereElementIsNotElementType()
        )

        window_collector = (
            FilteredElementCollector(self.doc)
            .OfCategory(BuiltInCategory.OST_Windows)
            .WhereElementIsNotElementType()
        )

        for door_el in door_collector:
            dw = DoorWrapper(door_el, phase=self.phase, source_doc=self.doc)
            self._doors.append(dw)

            # link level wrapper to door, level instance automatically links to door too
            lvl_wrapper = self._levels.get(door_el.LevelId)
            dw.set_level(lvl_wrapper)
        
        for window in window_collector:
            dw = DoorWrapper(window, phase=self.phase, source_doc=self.doc)
            self._doors.append(dw)

            # link level wrapper to window, level instance automatically links to window too
            lvl_wrapper = self._levels.get(window.LevelId)
            dw.set_level(lvl_wrapper)

        logger.debug(
            "Doors collected: %d total, %d apartment entrances.",
            len(self._doors),
            sum(1 for d in self._doors if d.is_apartment_entrance),
        )

        self.building.doors = self._doors
        self.building.apt_entrances = [d for d in self._doors if d.is_apartment_entrance]

    # ── step 3: resolve door rooms ────────────────────────────────────────

    def _resolve_door_rooms(self):
        """
        Populate DoorWrapper.from_room and DoorWrapper.to_room using the
        phase-aware Revit API.  Only rooms already in self._room_by_id are
        linked (rooms in other documents are ignored for cross-link edges).
        """
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
            
            # assign room wrapper instance to doors
            if from_el is not None:
                dw.from_room = self._room_by_id.get(from_el.Id)
            if to_el is not None:
                dw.to_room = self._room_by_id.get(to_el.Id)


    # ── step 4 + 5: build clusters ────────────────────────────────────────

    def _build_clusters(self):
        """
        Build room adjacency graph from interior doors, then run BFS to find
        connected components.  Components that include at least one room on
        the "inside" of an apartment entrance door are promoted to
        ApartmentWrapper objects.
        """
        # ── 4a. Identify rooms that are "inside" an apartment entrance ────
        #   The FromRoom of an apartment entrance door faces INTO the apartment.
        #   These rooms are the seeds for apartment clusters.
        apt_seed_ids = set()

        for dw in self._doors:
            # pick room thats not in halways
            if dw.is_apartment_entrance and dw.from_room is not None:
                apt_seed_ids.add(dw.from_room.element_id)

        logger.debug("Apartment seed rooms: %d", len(apt_seed_ids))

        # ── 4b. Build undirected adjacency graph (interior doors only) ────
        # adj: {ElementId: set(ElementId)}
        adj = {r.element_id: set() for r in self.building.rooms}

        for dw in self._doors:
            if dw.is_apartment_entrance:
                continue   # entrance doors are BOUNDARIES, not edges
            if dw.from_room is None or dw.to_room is None:
                continue   # exterior door or unresolved
            fid = dw.from_room.element_id
            tid = dw.to_room.element_id
            if fid in adj and tid in adj:
                adj[fid].add(tid)
                adj[tid].add(fid)

        # ── 4c. BFS connected components ─────────────────────────────────
        visited    = set()
        components = []   # list of sets of ElementId

        for room in self.building.rooms:
            rid = room.element_id
            if rid in visited:
                continue
            # BFS
            component = set()
            queue     = [rid]
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

        logger.debug("Connected components found: %d", len(components))

        # ── 4d. Classify components ────────────────────────────────────────
        for component in components:
            is_apartment = bool(component & apt_seed_ids)

            if is_apartment:
                apt = ApartmentWrapper()
                for rid in component:
                    rw = self._room_by_id.get(rid)
                    if rw:
                        apt.add_room(rw)
                        
                        logger.debug(rw)
                
                # set apartment's level
                apt.level = self._get_apartment_lvl(apt.rooms)
                self.building.add_apartment(apt)
            else:
                # Not an apartment – add to untracked
                for rid in component:
                    rw = self._room_by_id.get(rid)
                    if rw:
                        self.building.untracked_rooms.append(rw)

        logger.info(
            "Clusters → %d apartments, %d untracked rooms.",
            len(self.building.apartments),
            len(self.building.untracked_rooms),
        )

    # ── step 6: attach levels ─────────────────────────────────────────────

    def _parse_levels(self):
        """Parse levels"""
        level_collector = FilteredElementCollector(self.doc).OfClass(Level).ToElements()

        for lvl in level_collector:
            lvl_el_id = lvl.Id

            if not lvl_el_id in self._levels:
                lvl_w = LevelWrapper(lvl)
                self._levels[lvl_el_id] = lvl_w
                self.building.add_level(lvl_w)
        
        self.building.levels.sort(key=lambda x: x.elevation)

    
    def _get_apartment_lvl(self, rooms):
        """Get level of the random rooms in the set"""
        for room in rooms:
            return room.level

    """
    def _attach_levels(self):
        
        #Group rooms and apartments into LevelWrappers by room.level_id.
        #Levels are created on demand and sorted by elevation.
        
        from Autodesk.Revit.DB import Level

        level_el_cache = {}   # ElementId → Level element

        def _get_level_el(level_id):
            if level_id in level_el_cache:
                return level_el_cache[level_id]
            el = self.doc.GetElement(level_id)
            if el and isinstance(el, Level):
                level_el_cache[level_id] = el
                return el
            return None

        for rw in self.building.rooms:
            level_el = _get_level_el(rw.level_id)
            if level_el is None:
                logger.debug("Room id=%s has no valid level.", rw.element_id)
                continue
            lw = self.building.get_or_create_level(level_el)
            lw.add_room(rw)

        # Attach each apartment to the level of its first room
        for apt in self.building.apartments:
            if apt.rooms:
                lw = apt.rooms[0].level
                if lw:
                    lw.add_apartment(apt)
    """


# ═══════════════════════════════════════════════════════════════════════════
#  CONVENIENCE: PARSE ALL BUILDINGS
# ═══════════════════════════════════════════════════════════════════════════

def build_buildings_from_docs(host_doc, link_instances):
    """
    Create one BuildingWrapper per document (host + links) and parse
    their design options, but do NOT run the full parse yet
    (the user must choose a design option first).

    Returns:
        list[BuildingWrapper]: Unparsed wrappers ready for the UI.
    """
    from Autodesk.Revit.DB import RevitLinkInstance

    buildings = []

    # Host model
    host_bw = BuildingWrapper(host_doc, link_instance=None)
    parser  = DocumentParser(host_bw)
    host_bw._design_options = parser.parse_design_options()
    buildings.append(host_bw)

    # Linked models
    for link_inst in link_instances:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            logger.warning("Link %s has no loaded document – skipping.",
                           link_inst.Id)
            continue
        bw     = BuildingWrapper(link_doc, link_instance=link_inst)
        parser = DocumentParser(bw)
        bw._design_options = parser.parse_design_options()
        buildings.append(bw)

    return buildings


def parse_selected_buildings(buildings):
    """
    Run the full parse on each BuildingWrapper that has been selected and
    configured by the user (building.design_option is set).

    Args:
        buildings (list[BuildingWrapper]): Only selected ones, ordered.

    Returns:
        list[BuildingWrapper]: Same list, now fully populated.
    """
    for bw in buildings:
        if bw.design_option is None:
            logger.warning(
                "BuildingWrapper %r has no design option set – skipping.",
                bw.display_name,
            )
            continue
        parser = DocumentParser(bw)
        parser.parse()
    return buildings
