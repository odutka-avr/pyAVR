# -*- coding: utf-8 -*-
"""The plugin's settings: settings.txt in the extension's folder (next to Froglet.tab and lib).

One setting per line, `key = value`; empty lines and lines that start with # are ignored. The file is read at
every call (a change needs no restart of Revit). Keys: backend_url, backend_token, apartments_pool, stairs_folder.
"""

import io
import os

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.txt")


def _decode(raw):
    """The file as text whatever it was saved as. It is written in UTF-8, but a copy of it that went through Notepad, a
    messenger or an archiver can be in the Windows code page (cp1251) or damaged - and a UnicodeDecodeError out of here
    stopped the whole plugin ("'unknown' codec can't decode byte 0xd0"). The values that matter (the address, the token)
    are ASCII, so a file that cannot be read exactly is still read."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    elif raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):          # (PowerShell's ">" writes UTF-16)
        try:
            return raw.decode("utf-16")
        except (UnicodeError, LookupError):
            pass
    for codec in ("utf-8", "cp1251"):
        try:
            return raw.decode(codec)
        except (UnicodeError, LookupError):
            continue
    return raw.decode("latin-1")


def _read():
    values = {}
    try:
        with io.open(SETTINGS_PATH, "rb") as handle:
            text = _decode(handle.read())
    except (IOError, OSError):
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(u"#") or u"=" not in line:
            continue
        key, value = line.split(u"=", 1)
        values[key.strip()] = value.strip()
    return values


def status():
    """What is wrong with settings.txt, in one line for a message: where it is looked for, whether it is there and how many
    settings were read out of it."""
    try:
        path = SETTINGS_PATH
        if isinstance(path, bytes):
            path = _decode(path)
        if not os.path.isfile(SETTINGS_PATH):
            return u"файл не знайдено: {}".format(path)
        return u"файл {} ({} байт), прочитано параметрів: {}".format(path, os.path.getsize(SETTINGS_PATH), len(_read()))
    except Exception:
        return u""


def get(key, default=u""):
    """The value of `key`, or `default` when settings.txt does not have it (or is missing)."""
    return _read().get(key, default)
