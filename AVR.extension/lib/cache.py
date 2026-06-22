# coding: utf-8
import os
import json
import io   # python 2 doesnt support encoding keyword - import io to handle that


def get_cache_path(model_path, create_folders, cache_f_name):
    """
    Computes the full path to ***_cache.json based on the model file path.

    Supports two folder structures depending on whether the project follows
    the standard workshared structure or is a local standalone file:

    Workshared (contains '01_WIP' in path):
        Resolves the 'work folder' as the parent of the folder containing
        '01_WIP', then builds the cache path at:
        work_folder/00_Resources/Scripts/_pyavr_cache/tep_cache/tep_cache.json

    Local (no '01_WIP' found in path):
        Places the cache next to the model file at:
        model_folder/_pyavr_cache/***_cache/***_cache.json

    Creates all intermediate directories if they do not exist.

    Args:
        model_path (str):       Full path to the .rvt file (doc.PathName).
        create_folders (bool):  If true - create folders for cache (write cache is allowed)

    Returns:
        str: Full path to ***_cache.json.
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
            cache_f_name
        )
    else:
        # build cache directory path
        cache_dir = os.path.join(
            work_folder,
            "00_Resources",
            "Scripts",
            "_pyavr_cache",
            cache_f_name
        )
    
    # create all folders if they don't exist
    if create_folders and (not os.path.exists(cache_dir)):
        os.makedirs(cache_dir)

    return os.path.join(cache_dir, "{}.json".format(cache_f_name))


class CacheManager:
    def __init__(self, model_path, write_to_cache, cache_f_name):
        self.json_path = get_cache_path(model_path, write_to_cache, cache_f_name)
        self._data = self._load()
        self._write_to_cache = write_to_cache

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
            print("CacheManager: failed to save cache: {}".format(e))
        
    
