# -*- coding: utf-8 -*-
"""The plugin's settings: settings.txt in the extension's folder (next to Froglet.tab and lib).

One setting per line, `key = value`; empty lines and lines that start with # are ignored. The file is read at
every call (a change needs no restart of Revit). Keys: backend_url, backend_token, apartments_pool, stairs_folder.
"""

import io
import os

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.txt")


def _read():
    values = {}
    try:
        with io.open(SETTINGS_PATH, "r", encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except (IOError, OSError):
        pass
    return values


def get(key, default=u""):
    """The value of `key`, or `default` when settings.txt does not have it (or is missing)."""
    return _read().get(key, default)
