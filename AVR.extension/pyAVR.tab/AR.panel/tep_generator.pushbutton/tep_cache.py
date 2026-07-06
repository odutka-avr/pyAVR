# coding: utf-8
"""
RoomingCacheManager — persistent JSON cache for rooming script user choices.
Place this file in your extension's lib/ folder.
"""

# ====== IMPORTS =========================================================

import os
import json
import io   # python 2 doesnt support encoding keyword - import io to handle that

from cache import CacheManager

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
        work_folder/00_Resources/Scripts/_pyavr_cache/tep_cache/tep_cache.json

    Local (no '01_WIP' found in path):
        Places the cache next to the model file at:
        model_folder/_pyavr_cache/rooming_cache/rooming_cache.json

    Creates all intermediate directories if they do not exist.

    Args:
        model_path (str):       Full path to the .rvt file (doc.PathName).
        create_folders (bool):  If true - create folders for cache (write cache is allowed)

    Returns:
        str: Full path to tep_cache.json.
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


class TEPCacheManager(CacheManager):
    def __init__(self, model_path, write_to_cache, cache_f_name):
        CacheManager.__init__(self, model_path, write_to_cache, cache_f_name)
        self._data = self._load()
    
    def _load(self):
        data = CacheManager._load(self)
        # return empty dict with set construction
        if not data:
            return {"models": {}}
        return data

    def get_for_model(self, model_name):
        return self._data["models"].get(model_name, {})
    
    def update(self, new_data):
        if self._write_to_cache:
            
            updated_data = dict()

            for model_name in new_data["models"]:
                
                # if model is not present in cache - add nested dict with data or empty if model was not selected
                if not model_name in self._data["models"]:
                    self._data["models"][model_name] = self._merge(new_data["models"][model_name])

                if not new_data["models"][model_name]["previously_selected"]:
                    self._data["models"][model_name]["previously_selected"] = False
                else:
                    self._data["models"][model_name] = self._merge(new_data["models"][model_name])

            #self._data["models"] = updated_data
            
            # update general user set parameters in the form
            if "flag - construction type" in new_data:
                self._data["flag - construction type"] = True
            else: self._data["flag - construction type"] = False

            if "construction type" in new_data and new_data["flag - construction type"]:
                self._data["construction type"] = new_data["construction type"]
            else: self._data["construction type"] = False
        
            if "flag - property area" in new_data:
                self._data["flag - property area"] = True
            else: self._data["flag - property area"] = False

            if "flag - common area" in new_data:
                self._data["flag - common area"] = True
            else: self._data["flag - common area"] = False
            
            if "flag - parking spots area" in new_data:
                self._data["flag - parking spots area"] = True
            else: self._data["flag - parking spots area"] = False
                
            if "flag - total volume" in new_data:
                self._data["flag - total volume"] = True
            else: self._data["flag - total volume"] = False
            
            self.save(self._data)
    
    def _merge(self, new):
        merged = {}
        
        if "DO" in new:
            merged["DO"] = new["DO"]
        
        if "previously_selected" in new:
            merged["previously_selected"] = new["previously_selected"]
        
        if "building lvls" in new:
            merged["building lvls"] = new["building lvls"]
        
        if "building ground lvls" in new:
            merged["building ground lvls"] = new["building ground lvls"]
        
        if "limit height" in new:
            merged["limit height"] = new["limit height"]
        
        if "fire rating" in new:
            merged["fire rating"] = new["fire rating"]
        
        if "energy class" in new:
            merged["energy class"] = new["energy class"]
        
        if "duration" in new:
            merged["duration"] = new["duration"]
        
        return merged
        

    def model_lookup(self, model_name):
        if "models" in self._data:
            if model_name in self._data["models"]:
                model_data = self._data["models"].get(model_name, None)

                if model_data:
                    if model_data.get("previously_selected", False):
                        do_name = model_data.get("DO", None)
                        return model_name, do_name
                    return False, False

        return False, False

    def __lvl_lookup(self, key, model_name):
        if "models" in self._data:
            if model_name in self._data["models"]:
                model_data = self._data["models"].get(model_name)
                
                checked_lvls = model_data.get(key, set())

                if checked_lvls:
                    return checked_lvls
        return set()

    def b_lvl_lookup(self, model_name):
        return self.__lvl_lookup("building lvls", model_name)
    
    def ground_lvl_lookup(self, model_name):
        return self.__lvl_lookup("building ground lvls", model_name)
    
    def manual_data_lookup(self, model_name):
        if "models" in self._data:
            if model_name in self._data["models"]:
                model_data = self._data["models"].get(model_name)

                height = model_data.get("limit height", None)
                fire_rating = model_data.get("fire rating", None)
                e_class = model_data.get("energy class", None)
                duration = model_data.get("duration", None)

                return height, fire_rating, e_class, duration
        return None

    def manual_data_row_visibility_lookup(self):
        f_construction_type = self._data.get("flag - construction type", False)
        construction_type = self._data.get("construction type", None)

        f_property_area = self._data.get("flag - property area", False)
        f_common_area = self._data.get("flag - common area", False)
        f_parking_spots_area = self._data.get("flag - parking spots area", False)
        f_total_volume = self._data.get("flag - total volume", False)

        return f_construction_type, \
                construction_type, \
                f_property_area, \
                f_common_area, \
                f_parking_spots_area, \
                f_total_volume
