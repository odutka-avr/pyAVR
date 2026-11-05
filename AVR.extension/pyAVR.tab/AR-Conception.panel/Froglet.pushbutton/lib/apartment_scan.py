# -*- coding: utf-8 -*-
"""
Folder scanning and filename parsing for the DKUI apartment bank.

Ported from the original Grasshopper/DKUI script. The apartment bank
window scans its one fixed root folder recursively (see
scan_recursive_png_files / build_apartment_records's own recursive=True)
since that root now holds many category subfolders, each with its own
PNGs, rather than being a flat folder of apartments itself;
scan_direct_png_files (folder_path's own direct contents only, no
subfolders) still exists for any other use that wants just one folder.
Filenames encode metadata in underscore-separated fields, for example:

    1_C_AP_00_5695.995_46_1.png
                    ^^ area (second-to-last field)

    0_R_NP_00_4850.7000_29_1.png
              ^^^^^^^^^ nominal width.length (third-to-last field)

The first character of the filename is the apartment type code
(0 = Студіо, 1 = Однокімнатні, 2 = Двокімнатні, 3 = Трьохкімнатні).
"""

import math
import os
import re

try:
    from urllib.parse import unquote
except ImportError:
    from urllib import unquote

APARTMENT_TYPE_OPTIONS = [
    ("0", "Студіо"),
    ("1", "Однокімнатні"),
    ("2", "Двокімнатні"),
    ("3", "Трьохкімнатні"),
]

APARTMENT_TYPE_CODES = [code for code, _ in APARTMENT_TYPE_OPTIONS]

_scan_cache = {}

try:
    _is_finite = math.isfinite
except AttributeError:
    def _is_finite(value):
        # Python 2 / IronPython 2.7 has no math.isfinite.
        return value == value and value not in (float("inf"), float("-inf"))


def ensure_dir(path):
    """os.makedirs(path, exist_ok=True) without the Python-3-only kwarg."""
    try:
        os.makedirs(path)
    except OSError:
        if not os.path.isdir(path):
            raise


def _natural_sort_key(value):
    parts = re.split(r"(\d+)", value)
    return [
        int(part) if part.isdigit() else part.lower()
        for part in parts
    ]


def _unwrap_path_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            candidate = _unwrap_path_value(item)
            if candidate:
                return candidate
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def clean_folder_path(value):
    """Resolve a user-supplied path into a folder path.

    Accepts either a folder path or the path of a file located inside
    the required folder (its parent folder is used in that case).
    """
    path_value = _unwrap_path_value(value)

    if not path_value:
        return ""

    while (
        len(path_value) >= 2
        and path_value[0] == path_value[-1]
        and path_value[0] in ("\"", "'")
    ):
        path_value = path_value[1:-1].strip()

    lowered = path_value.lower()

    if lowered.startswith("file:///"):
        path_value = path_value[8:]
    elif lowered.startswith("file://"):
        path_value = path_value[7:]

    try:
        path_value = unquote(path_value)
    except Exception:
        pass

    path_value = os.path.expandvars(os.path.expanduser(path_value))
    path_value = os.path.normpath(path_value)
    path_value = os.path.abspath(path_value)

    if os.path.isfile(path_value):
        path_value = os.path.dirname(path_value)

    return path_value


def _folder_signature(folder_path):
    folder_stat = os.stat(folder_path)
    modified_ns = getattr(
        folder_stat, "st_mtime_ns", int(folder_stat.st_mtime * 1000000000)
    )
    return int(modified_ns)


def scan_direct_png_files(folder_path, force_refresh=False):
    """Return naturally sorted direct PNG files; never enters subfolders."""
    directory_signature = _folder_signature(folder_path)

    cached_value = _scan_cache.get(folder_path)

    if (
        not force_refresh
        and isinstance(cached_value, tuple)
        and len(cached_value) == 2
        and cached_value[0] == directory_signature
    ):
        return list(cached_value[1])

    direct_paths = []

    for entry_name in os.listdir(folder_path):
        entry_path = os.path.join(folder_path, entry_name)

        try:
            is_direct_file = os.path.isfile(entry_path)
        except Exception:
            is_direct_file = False

        if not is_direct_file:
            continue

        if not entry_name.lower().endswith(".png"):
            continue

        direct_paths.append(entry_path)

    direct_paths.sort(key=lambda path: _natural_sort_key(os.path.basename(path)))

    _scan_cache[folder_path] = (directory_signature, list(direct_paths))

    return direct_paths


def scan_recursive_png_files(folder_path):
    """Return naturally sorted PNG files found anywhere inside
    folder_path, at any depth - unlike scan_direct_png_files (which only
    ever looks at folder_path's own direct contents), this walks every
    subfolder too. The apartment pool now lives in one fixed root folder
    on a network share that itself holds many category subfolders, each
    with its own apartments, rather than a flat folder the user picks
    directly.

    No mtime-based caching here (unlike scan_direct_png_files's own) - a
    single root folder's own mtime doesn't reliably change when a file
    deep inside one of its subfolders changes, so that same caching
    approach would miss updates; this always walks fresh instead."""
    all_paths = []

    for current_dir, _subdir_names, file_names in os.walk(folder_path):
        for entry_name in file_names:
            if not entry_name.lower().endswith(".png"):
                continue
            all_paths.append(os.path.join(current_dir, entry_name))

    all_paths.sort(key=lambda path: _natural_sort_key(os.path.basename(path)))

    return all_paths


def source_signature(file_path):
    file_stat = os.stat(file_path)
    modified_ns = getattr(
        file_stat, "st_mtime_ns", int(file_stat.st_mtime * 1000000000)
    )
    return (int(modified_ns), int(file_stat.st_size))


def apartment_type_code_from_filename(file_name):
    """Return apartment type code 0-3 from the first filename character."""
    name_value = str(file_name or "")

    if not name_value:
        return ""

    first_character = name_value[0]

    if first_character in APARTMENT_TYPE_CODES:
        return first_character

    return ""


def apartment_window_placement_from_filename(file_name):
    """Return the window-placement letter from the second underscore-
    separated filename field (R = рядове, C = кутове, L = наскрізне,
    N = торцеве). An unrecognised or missing value returns "".
    """
    name_value = str(file_name or "").strip()

    if not name_value:
        return ""

    stem_value = os.path.splitext(name_value)[0]
    name_parts = stem_value.split("_")

    if len(name_parts) < 2:
        return ""

    return name_parts[1].strip().upper()


def apartment_added_part_from_filename(file_name):
    """Return the added-part code from the third underscore-separated
    filename field (AP = Присутня, NP = Відсутня). An unrecognised or
    missing value returns "".
    """
    name_value = str(file_name or "").strip()

    if not name_value:
        return ""

    stem_value = os.path.splitext(name_value)[0]
    name_parts = stem_value.split("_")

    if len(name_parts) < 3:
        return ""

    return name_parts[2].strip().upper()


def apartment_area_from_filename(file_name):
    """Return the apartment area from the second-to-last underscore field."""
    name_value = str(file_name or "").strip()

    if not name_value:
        return None

    stem_value = os.path.splitext(name_value)[0]
    name_parts = stem_value.split("_")

    if len(name_parts) < 2:
        return None

    raw_area = name_parts[-2].strip().replace(",", ".")

    if not raw_area:
        return None

    try:
        numeric_area = float(raw_area)
    except Exception:
        return None

    if not _is_finite(numeric_area) or numeric_area < 0:
        return None

    return numeric_area


def apartment_nominal_dimensions_from_filename(file_name):
    """Return the two nominal dimensions from the third-to-last field.

    Example: 4850.7000 -> (4850.0, 7000.0). No fixed width/length
    orientation is assigned; the UI checks both orientations.
    """
    name_value = str(file_name or "").strip()

    if not name_value:
        return None, None

    stem_value = os.path.splitext(name_value)[0]
    name_parts = stem_value.split("_")

    if len(name_parts) < 3:
        return None, None

    raw_dimensions = name_parts[-3].strip()
    dimension_parts = raw_dimensions.split(".")

    if len(dimension_parts) != 2:
        return None, None

    try:
        first_dimension = float(dimension_parts[0].strip().replace(",", "."))
        second_dimension = float(dimension_parts[1].strip().replace(",", "."))
    except Exception:
        return None, None

    if (
        not _is_finite(first_dimension)
        or not _is_finite(second_dimension)
        or first_dimension <= 0
        or second_dimension <= 0
    ):
        return None, None

    return first_dimension, second_dimension


def replace_extension_with_rvt(file_path):
    """Return the same path with its final extension replaced by .rvt."""
    path_value = str(file_path or "").strip()

    if not path_value:
        return ""

    root_path, _extension = os.path.splitext(path_value)

    if not root_path:
        return ""

    return root_path + ".rvt"


def build_apartment_records(folder_path, force_refresh=False, recursive=False):
    """Scan the folder and return a list of apartment metadata dicts.
    recursive=True also looks inside every subfolder (any depth) - see
    scan_recursive_png_files - instead of only folder_path's own direct
    contents."""
    if recursive:
        direct_paths = scan_recursive_png_files(folder_path)
    else:
        direct_paths = scan_direct_png_files(folder_path, force_refresh=force_refresh)

    records = []
    errors = []

    for file_path in direct_paths:
        file_name = os.path.basename(file_path)

        try:
            signature = source_signature(file_path)
        except Exception as error:
            errors.append("{}: {}".format(file_name, error))
            continue

        dimension1, dimension2 = apartment_nominal_dimensions_from_filename(
            file_name
        )

        records.append(
            {
                "path": file_path,
                "name": file_name,
                "displayName": os.path.splitext(file_name)[0],
                "signature": signature,
                "apartmentType": apartment_type_code_from_filename(file_name),
                "windowPlacement": apartment_window_placement_from_filename(file_name),
                "addedPart": apartment_added_part_from_filename(file_name),
                "apartmentArea": apartment_area_from_filename(file_name),
                "apartmentDimension1": dimension1,
                "apartmentDimension2": dimension2,
                "rvtPath": replace_extension_with_rvt(file_path),
            }
        )

    return records, errors
