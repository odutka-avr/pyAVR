# -*- coding: utf-8 -*-
"""The facade and the depth of an apartment layout (Рядове): the side with the windows and the side across it.

Nothing is measured in Revit and no apartment file is opened. The sizes come from, in this order:
1. the layout's sibling .json (same name as the .rvt): "facade_mm" and "depth_mm", when the file has them
   (JSON_FACADE_KEY / JSON_DEPTH_KEY - the one place to change when the files get their sizes under other
   names);
2. the file name: the first nominal size ("6600.10000" -> 6600) is the width = the facade, the second the
   depth. (Not every name keeps to it; the JSON sizes are meant to replace this.)
"""

import io
import json
import os

JSON_FACADE_KEY = "facade_mm"
JSON_DEPTH_KEY = "depth_mm"

_cache = {}   # json path -> (mtime, (facade, depth) or None)


def _json_path(rvt_path):
    return os.path.splitext(rvt_path)[0] + u".json"


def _number(value):
    try:
        number = float(value)
    except Exception:
        return None
    return number if number > 0 else None


def json_facade_depth(rvt_path):
    """(facade, depth) in mm from the layout's JSON, or None without the file or without the two sizes."""
    if not rvt_path:
        return None
    path = _json_path(rvt_path)
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return None
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]

    sizes = None
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            data = json.loads(handle.read())
        facade, depth = _number(data.get(JSON_FACADE_KEY)), _number(data.get(JSON_DEPTH_KEY))
        if facade is not None and depth is not None:
            sizes = (facade, depth)
    except Exception:
        sizes = None
    _cache[path] = (mtime, sizes)
    return sizes


def nominal_facade_depth(item):
    """(facade, depth) in mm of a bank item: from its JSON, else its two nominal sizes in the order of the
    file name (first = facade, second = depth); None without sizes."""
    sizes = json_facade_depth(item.get("rvtPath"))
    if sizes is not None:
        return sizes
    first, second = item.get("apartmentDimension1"), item.get("apartmentDimension2")
    if first is None or second is None:
        return None
    return (first, second)
