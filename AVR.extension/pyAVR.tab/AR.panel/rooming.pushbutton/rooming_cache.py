# coding: utf-8
"""
RoomingCacheManager — persistent JSON cache for rooming script user choices.
Place this file in your extension's lib/ folder.
"""

# ====== IMPORTS =========================================================

import os
import json
import io   # python 2 doesnt support encoding keyword - import io to handle that

# ========================================================================


# ── path resolution ───────────────────────────────────────────────────────────

def get_cache_path(model_path, create_folders):
    """
    Computes the full path to rooming_cache.json based on the model file path.

    Supports two folder structures depending on whether the project follows
    the standard workshared structure or is a local standalone file:

    Workshared (contains '01_WIP' in path):
        Resolves the 'work folder' as the parent of the folder containing
        '01_WIP', then builds the cache path at:
        work_folder/00_Resources/Scripts/_pyavr_cache/rooming_cache/rooming_cache.json

    Local (no '01_WIP' found in path):
        Places the cache next to the model file at:
        model_folder/_pyavr_cache/rooming_cache/rooming_cache.json

    Creates all intermediate directories if they do not exist.

    Args:
        model_path (str):       Full path to the .rvt file (doc.PathName).
        create_folders (bool):  If true - create folders for cache (write cache is allowed)

    Returns:
        str: Full path to rooming_cache.json.
    """
    parts = model_path.replace("\\", "/").split("/")

    # find index of the part that equals '01_WIP'
    wip_index = None
    for i, part in enumerate(parts):
        if part == "01_WIP":
            wip_index = i
            break

    if wip_index is None:
        wip_index = -1

    # work folder is the parent of the folder containing 01_WIP
    work_folder_parts = parts[:wip_index]
    work_folder = "/".join(work_folder_parts)

    if wip_index == -1:
        # build cache dir in the model folder
        cache_dir = os.path.join(
            work_folder,
            "_pyavr_cache",
            "rooming_cache"
        )
    else:
        # build cache directory path
        cache_dir = os.path.join(
            work_folder,
            "00_Resources",
            "Scripts",
            "_pyavr_cache",
            "rooming_cache"
        )
    
    # create all folders if they don't exist
    if create_folders and (not os.path.exists(cache_dir)):
        os.makedirs(cache_dir)

    return os.path.join(cache_dir, "rooming_cache.json")


# ── cache manager ─────────────────────────────────────────────────────────────

class RoomingCacheManager(object):
    """
    Handles all read/write/merge logic for the rooming script JSON cache.

    JSON structure:
    {
        "Main Model": {
            "room_types":          { "1": 1.0, "2": 0.5 },
            "building_sections":   { "B01": 0, "B02": 0 },
            "global_fn_lr_width":  0,
            "omitted_categories":  ["Circulation"]
        },
        "Option A": { ... }
    }

    Note: JSON keys are always strings. Integer room type keys are stored
    as strings and converted back to int on load.
    """

    def __init__(self, model_path, write_to_cache, f_name):
        """
        Args:
            model_path (str): doc.PathName from Revit
        """
        self.json_path = get_cache_path(model_path, write_to_cache)
        self._data = self._load()
        self._write_to_cache = write_to_cache
        self.f_name = f_name

    # ── public API ────────────────────────────────────────────────────────────

    def _load(self):
        """
        Reads and returns the full cache dict.
        Returns {} if file is missing or corrupted.

        Returns:
            dict
        """
        if not os.path.exists(self.json_path):
            return {}

        try:
            with io.open(self.json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return {}
                return data
        except (ValueError, IOError):
            # corrupted or unreadable — start fresh
            return {}

    def save(self, data):
        """
        Writes the full cache dict to JSON.

        Args:
            data (dict): Full cache dict to write
        """
        try:
            with io.open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print("RoomingCacheManager: failed to save cache: {}".format(e))

    def get_for_design_option(self, do_name):
        """
        Returns the cached sub-dict for a specific design option.
        Returns {} if the design option has no cached data.

        Args:
            do_name (str): Design option name

        Returns:
            dict with keys: room_types, building_sections,
                            global_fn_lr_width, omitted_categories
        """
        file_data = self._data.get(self.f_name, {})
        return file_data.get(do_name, {})

    def update_for_design_option(self, do_name, new_data):
        """
        Deep-merges new_data into the existing entry for do_name, then saves.

        Merge behavior:
            - room_types:          merged (old keys kept, new keys added/overwritten)
            - building_sections:   replaced entirely with new_data version
            - global_fn_lr_width:  overwritten if present in new_data
            - omitted_categories:  replaced entirely with new list

        Args:
            do_name  (str):  Design option name
            new_data (dict): Data collected from the form on confirm
        """
        if self._write_to_cache:
            # get or create the file-level entry
            file_data = self._data.get(self.f_name, {})
            
            existing = file_data.get(do_name, {})
            merged = self._merge(existing, new_data)
            
            file_data[do_name] = merged
            self._data[self.f_name] = file_data
            self.save(self._data)

    # ── private ───────────────────────────────────────────────────────────────

    def _merge(self, existing, new_data):
        """
        Merges new_data into existing according to per-field rules.

        Args:
            existing (dict): Previously cached entry for this DO
            new_data (dict): Fresh data from the form

        Returns:
            dict: Merged result
        """
        merged = {}

        if "include_sills" in new_data:
            merged["include_sills"] = new_data["include_sills"]
        elif "include_sills" in existing:
            merged["include_sills"] = existing["include_sills"]

        # room_types: keep old coefficients, add/overwrite with new values
        old_room_types = existing.get("room_types", {})
        new_room_types = new_data.get("room_types", {})
        merged_room_types = {}
        merged_room_types.update(old_room_types)
        merged_room_types.update(new_room_types)
        merged["room_types"] = merged_room_types

        # building_sections: replace entirely (stale buildings removed)
        if "building_sections" in new_data:
            merged["building_sections"] = new_data["building_sections"]
        elif "building_sections" in existing:
            merged["building_sections"] = existing["building_sections"]

        # global_fn_lr_width: overwrite if present, otherwise keep old
        if "global_fn_lr_width" in new_data:
            merged["global_fn_lr_width"] = new_data["global_fn_lr_width"]
        elif "global_fn_lr_width" in existing:
            merged["global_fn_lr_width"] = existing["global_fn_lr_width"]

        # omitted_categories: replace entirely with current selection
        merged["omitted_categories"] = new_data.get(
            "omitted_categories",
            existing.get("omitted_categories", [])
        )

        merged["global_fn_lr_width_option"] = new_data.get("global_fn_lr_width_option",
                                                           existing.get("global_fn_lr_width_option"))
        
        merged["b_section_fn_lr_width_option"] = new_data.get("b_section_fn_lr_width_option",
                                                           existing.get("b_section_fn_lr_width_option"))

        return merged


# ── form integration helpers ──────────────────────────────────────────────────

def prefill_room_types(previous, fresh_room_types):
    """
    For each fresh room type, returns its cached coefficient or default 1.0.

    Args:
        previous         (dict):     Cached DO entry from get_for_design_option()
        fresh_room_types (set[int]): Room types found in model for this DO

    Returns:
        dict[int, float]: {room_type: coefficient}
    """
    cached = previous.get("room_types", {})
    result = {}
    for rt in fresh_room_types:
        # JSON keys are strings — convert int key to str for lookup
        result[rt] = float(cached.get(str(rt), cached.get(rt, 1.0)))
    return result


def prefill_building_sections(previous, fresh_buildings):
    """
    For each fresh building, returns its cached width or default 0.

    Args:
        previous        (dict):      Cached DO entry
        fresh_buildings (set[str]):  Buildings found in model for this DO

    Returns:
        dict[str, float]: {building: finish_width}
        bool:             True if previous data suggests global mode
        bool:             True if previous data suggests per-building mode
    """
    cached_sections = previous.get("building_sections", {})
    global_option_chosen = previous.get("global_fn_lr_width_option", False)
    # if run for the first time, two options must = False, so all radio buttons are disselected
    b_section_option_chosen = previous.get("b_section_fn_lr_width_option", False)

    result = {}
    for b in fresh_buildings:
        result[b] = float(cached_sections.get(str(b), cached_sections.get(b, 0)))

    return result, global_option_chosen, b_section_option_chosen


def prefill_global_width(previous):
    """
    Returns the cached global finish layer width or 0.

    Args:
        previous (dict): Cached DO entry

    Returns:
        float
    """
    return float(previous.get("global_fn_lr_width", 0))


def prefill_omitted_categories(previous, fresh_categories):
    """
    Returns set of categories that should be pre-checked.

    Args:
        previous          (dict):     Cached DO entry
        fresh_categories  (set[str]): Categories found in model

    Returns:
        set[str]: Categories to pre-check
    """
    cached_omitted = set(previous.get("omitted_categories", []))
    # only pre-check categories that still exist in the model
    return cached_omitted.intersection(fresh_categories)


def prefill_include_sills(previous):
    """
    Returns cached boolean state for including sills in room area, defaulting to False.
    """
    return bool(previous.get("include_sills", False))