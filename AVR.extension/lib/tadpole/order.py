# -*- coding: utf-8 -*-
"""Tadpole — the order: what the button hands the kitchen, in plain numbers.

The kitchen (`tadpole.engine`) is fed wall runs, records and dark stretches.
In Revit those carry faces, elements and XYZ; the kitchen reads nothing of
them but numbers. `pack` writes those numbers down as JSON-ready data,
`unpack` builds TWINS from it - small objects that answer the same names the
kitchen asks for - and the kitchen runs on the twins as it runs on the real
thing, with no Revit anywhere.

Three things the kitchen used to read off the element itself - its box, the
outline of its floor plate - are read here, once, by the button, and the
twin carries them (`bbox_of`, `plate_loop_of`). The rounded corners go too:
the outline follows an arc in steps (`arcs.curve_points`), and the circles
met and the sun measured along them (`arcs.ARCS`, `arcs.ARC_SUN`, filled by
the button's sun study) travel with the order and are put back in the
kitchen's `arcs` before it lays anything.

`digest` writes a laid-out file down the same way, so a run on the real
objects and a run on their twins can be compared number for number.

Works in IronPython 2.7 (the button) and CPython 3 (the kitchen alone).
"""

import io
import json
import os

# 3: the Tadpole-split button (lib/tadpole as of 2026-10-21) - arcs in the order; a kitchen of another format
# says so instead of laying a file it cannot read
FORMAT = 3


# ----------------------------------------------------------------------
# what the kitchen used to read off the element - the twin carries it
# ----------------------------------------------------------------------

def bbox_of(record):
    """(min x, min y, min z, max x, max y, max z) in feet, or None - the element's own box."""
    if isinstance(record, Record):
        return record.bbox
    try:
        box = record.element.get_BoundingBox(None)
    except Exception:
        return None
    if box is None:
        return None
    return (box.Min.X, box.Min.Y, box.Min.Z, box.Max.X, box.Max.Y, box.Max.Z)


def plate_loop_of(record):
    """[(x, y)] in feet, or None - the outer loop of the element's lowest down-facing face, as drawn."""
    if isinstance(record, Record):
        return None if record.plate_loop is None else list(record.plate_loop)
    try:
        from tadpole import geom
        face = geom.lowest_down_face(record.element)
        if face is None:
            return None
        loop, _count = geom.outer_loop(face)
        if loop is None:
            return None
        points = []
        for curve in loop:
            # an arc is followed, not cut by its chord - as `corridors.plate_outline` reads it
            points.extend(geom.curve_points(curve))
        return points
    except Exception:
        return None


# ----------------------------------------------------------------------
# the twins
# ----------------------------------------------------------------------

FT_TO_M = 0.3048


class Point(object):
    """An XYZ in plain numbers."""

    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class Record(object):
    """An `ElementRecord` without its element."""

    def __init__(self, data):
        for name in RECORD_FIELDS:
            setattr(self, name, data.get(name))
        self.has_geometry = bool(data.get('has_geometry'))
        self.bbox = _tuple(data.get('bbox'))
        self.plate_loop = None if data.get('plate_loop') is None else [tuple(p) for p in data['plate_loop']]


class Seg(object):
    """A `classify.WallSeg` without its face."""

    def __init__(self, data, record):
        self.record = record
        self.face = None
        self.p0 = Point(*data['p0'])
        self.p1 = Point(*data['p1'])
        self.z0 = data['z0']
        self.z1 = data['z1']
        self.normal = None if data['normal'] is None else Point(*data['normal'])
        self.parts = [tuple(p) for p in data['parts']]
        self.axis = _tuple(data['axis'])

    @property
    def length(self):
        import math
        return math.hypot(self.p1.X - self.p0.X, self.p1.Y - self.p0.Y)


class Run(object):
    """An `inputs.WallRun` - its numbers and what the sun study found, the hours already averaged."""

    def __init__(self, data, seg):
        self.seg = seg
        self.t0 = data['t0']
        self.t1 = data['t1']
        self.side = data['side']
        self.index = _tuple(data['index'])
        self.mean_hours = data['mean_hours']
        self.lit = [tuple(x) for x in data['lit']]
        self.dark = [tuple(x) for x in data['dark']]
        self.columns = []           # the study's columns stay with the button; `mean_hours` is their answer

    @property
    def parent(self):
        return None if self.index is None else self.index[0]

    @property
    def ordinal(self):
        return None if self.index is None else self.index[1]

    @property
    def label(self):
        if self.index is None:
            return u'-'
        return u'{0}.{1}'.format(self.index[0], self.index[1])

    @property
    def record(self):
        return self.seg.record

    @property
    def section(self):
        return self.seg.record.section

    @property
    def level_name(self):
        return self.seg.record.level_name

    @property
    def level_elev(self):
        return self.seg.record.level_elev

    @property
    def length_m(self):
        return (self.t1 - self.t0) * FT_TO_M


class Stretch(tuple):
    """An `inputs.Stretch` - (run, a, b, hours)."""

    __slots__ = ()

    def __new__(cls, run, a, b, hours):
        return tuple.__new__(cls, (run, a, b, hours))

    run = property(lambda self: self[0])
    a = property(lambda self: self[1])
    b = property(lambda self: self[2])
    hours = property(lambda self: self[3])

    @property
    def index(self):
        return self.run.index


RECORD_FIELDS = ('id', 'name', 'use_class', 'category', 'type_name', 'level_derived', 'matched',
                 'raw_type_value', 'section', 'level_id', 'level_name', 'level_elev', 'error')


# ----------------------------------------------------------------------
# pack - in the button
# ----------------------------------------------------------------------

def element_id(value):
    """An ElementId as an int (Revit 2024 has `.Value`, earlier ones `.IntegerValue`); anything else as it is."""
    if value is None:
        return None
    for name in ('Value', 'IntegerValue'):
        try:
            got = getattr(value, name)
        except Exception:
            continue
        if isinstance(got, (int, float)) or type(got).__name__ in ('long', 'Int64', 'Int32'):
            return int(got)
    return value


def _xyz(p):
    return None if p is None else [p.X, p.Y, p.Z]


def _tuple(value):
    return None if value is None else tuple(value)


class Tokens(object):
    """Names for the objects of one order - `r3`, `s7`, `o12`, `i5`, `d40` - by identity."""

    def __init__(self):
        self.by_id = {}
        self.count = {}

    def name(self, obj, prefix):
        key = id(obj)
        if key not in self.by_id:
            n = self.count.get(prefix, 0)
            self.count[prefix] = n + 1
            self.by_id[key] = (u'{0}{1}'.format(prefix, n), obj)
        return self.by_id[key][0]

    def get(self, obj):
        got = self.by_id.get(id(obj))
        return got[0] if got is not None and got[1] is obj else None


def pack(outer_walls, inner_walls, records, darkloft, height_records=(), north=None,
         flips=None, only=None, held=None, values=None):
    """(order, tokens) - the order as JSON-ready data, and the names it gave the button's objects.

    `height_records` - every record a section's height is measured on (`stairs.section_heights`: the sections and
    the blocks under them), each with its box. `values` - the settings window's answer (`apartments.adopt_bands`,
    `adopt_mix`)."""
    tokens = Tokens()
    rec_rows, seg_rows, run_rows = [], [], []

    def rec(record):
        got = tokens.get(record)
        if got is not None:
            return got
        name = tokens.name(record, u'r')
        row = dict((f, _plain(getattr(record, f, None))) for f in RECORD_FIELDS)
        row['id'] = element_id(getattr(record, 'id', None))
        row['level_id'] = element_id(getattr(record, 'level_id', None))
        row['has_geometry'] = bool(getattr(record, 'has_geometry', False))
        box = bbox_of(record)
        row['bbox'] = None if box is None else list(box)
        loop = plate_loop_of(record)
        row['plate_loop'] = None if loop is None else [list(p) for p in loop]
        row['token'] = name
        rec_rows.append(row)
        return name

    def seg(s):
        got = tokens.get(s)
        if got is not None:
            return got
        name = tokens.name(s, u's')
        seg_rows.append(dict(token=name, record=rec(s.record), p0=_xyz(s.p0), p1=_xyz(s.p1), z0=s.z0, z1=s.z1,
                             normal=_xyz(s.normal), parts=[list(p) for p in s.parts],
                             axis=None if not s.axis else list(s.axis)))
        return name

    def run(r, prefix):
        name = tokens.name(r, prefix)
        run_rows.append(dict(token=name, seg=seg(r.seg), t0=r.t0, t1=r.t1, side=r.side,
                             index=None if r.index is None else list(r.index), mean_hours=r.mean_hours,
                             lit=[list(x) for x in r.lit], dark=[list(x) for x in r.dark]))
        return name

    outer = [run(r, u'o') for r in outer_walls]
    inner = [run(r, u'i') for r in inner_walls]
    stretches = []
    for st in darkloft:
        name = tokens.get(st.run) or run(st.run, u'x')
        stretches.append([name, st.a, st.b, st.hours])
        tokens.name(st, u'd')
    record_names = [rec(r) for r in records]
    height_names = [rec(r) for r in height_records]
    # the rounded corners: the circles the plates were read with (every `plate_loop_of` above added its own) and
    # the sun along them (`sun.arc_study`)
    from tadpole import arcs
    order = dict(format=FORMAT, records=rec_rows, segs=seg_rows, runs=run_rows,
                 outer=outer, inner=inner, darkloft=stretches, section_records=record_names,
                 height_records=height_names, north=north, flips=encode(flips), only=encode(only),
                 held=encode(held), values=encode(values),
                 arcs=encode(dict(arcs.ARCS)), arc_sun=encode(dict(arcs.ARC_SUN)))
    return order, tokens


# ----------------------------------------------------------------------
# unpack - in the kitchen
# ----------------------------------------------------------------------

def unpack(order):
    """dict(outer_walls, inner_walls, records, darkloft, height_records, north, flips, only, held, values, tokens,
    arcs, arc_sun)."""
    if order.get('format') != FORMAT:
        raise ValueError('order format {0}, this kitchen reads {1}'.format(order.get('format'), FORMAT))
    tokens = Tokens()
    by = {}
    for row in order['records']:
        by[row['token']] = Record(dict((k, decode(v)) for k, v in row.items()))
    for row in order['segs']:
        by[row['token']] = Seg(row, by[row['record']])
    for row in order['runs']:
        by[row['token']] = Run(row, by[row['seg']])
    for name, obj in list(by.items()):
        tokens.by_id[id(obj)] = (name, obj)
    darkloft = []
    for n, (name, a, b, hours) in enumerate(order['darkloft']):
        st = Stretch(by[name], a, b, hours)
        tokens.by_id[id(st)] = (u'd{0}'.format(n), st)
        darkloft.append(st)
    return dict(outer_walls=[by[n] for n in order['outer']], inner_walls=[by[n] for n in order['inner']],
                records=[by[n] for n in order['section_records']], darkloft=darkloft,
                height_records=[by[n] for n in order['height_records']], north=order['north'], flips=decode(order['flips']),
                only=decode(order['only']), held=decode(order['held']), values=decode(order['values']),
                tokens=tokens, arcs=decode(order.get('arcs')) or {}, arc_sun=decode(order.get('arc_sun')) or {})


# ----------------------------------------------------------------------
# plain data both ways - tuples, sets and dicts keyed by anything survive JSON
# ----------------------------------------------------------------------

def encode(value):
    if value is None or isinstance(value, (bool, int, float)) or _is_text(value):
        return value
    if isinstance(value, tuple):
        return {'__t': [encode(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {'__s': [encode(v) for v in sorted(value, key=_sort_key)]}
    if isinstance(value, dict):
        if all(_is_text(k) and not k.startswith('__') for k in value):
            return dict((k, encode(v)) for k, v in value.items())
        return {'__d': [[encode(k), encode(v)] for k, v in sorted(value.items(), key=lambda kv: _sort_key(kv[0]))]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if type(value).__name__ in ('long', 'Int64', 'Int32'):
        return int(value)
    raise TypeError('cannot put {0!r} in an order'.format(type(value).__name__))


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        if '__t' in value:
            return tuple(decode(v) for v in value['__t'])
        if '__s' in value:
            return set(decode(v) for v in value['__s'])
        if '__d' in value:
            return dict((_hashable(decode(k)), decode(v)) for k, v in value['__d'])
        return dict((k, decode(v)) for k, v in value.items())
    return value


def _plain(value):
    """`encode`, or the value's text where it is not plain data (an exception kept as a record's error)."""
    try:
        return encode(value)
    except TypeError:
        return u'{0}'.format(value)


def _hashable(value):
    return tuple(value) if isinstance(value, list) else value


def _is_text(value):
    try:
        return isinstance(value, basestring)        # IronPython 2.7
    except NameError:
        return isinstance(value, str)


def _sort_key(value):
    return (type(value).__name__ != 'NoneType', u'{0}'.format(value))


# ----------------------------------------------------------------------
# digest - a laid-out file, number for number
# ----------------------------------------------------------------------

# how long it took is not what was laid; and `apartments.Floor`'s lookups keyed by id() - addresses, a new one
# every run - index the order's own runs, which the digest names anyway
DIGEST_SKIP = ('seconds', 'dark_by_run', 'inner_ids')


def digest(laid, tokens):
    """The kitchen's answer (`engine.run`) as JSON-ready data. The order's own objects are written by their names;
    every other object by its fields, the second time it is met as {'@': n} - the n-th object written."""
    seen = {}
    counter = [0]

    def walk(value, depth):
        if value is None or isinstance(value, bool) or _is_text(value):
            return value
        if isinstance(value, (int, float)):
            return value
        if type(value).__name__ in ('long', 'Int64', 'Int32', 'Double'):
            return float(value) if type(value).__name__ == 'Double' else int(value)
        name = tokens.get(value)
        if name is not None:
            return {'#': name}
        if type(value).__name__ == 'ElementId':
            return element_id(value)            # the twin's level_id is this int
        if isinstance(value, Point) or type(value).__name__ == 'XYZ':
            return [value.X, value.Y, value.Z]
        if depth > 60:
            return u'<deep>'
        if isinstance(value, (list, tuple)):
            return [walk(v, depth + 1) for v in value]
        if isinstance(value, (set, frozenset)):
            items = [walk(v, depth + 1) for v in value]
            return {'set': sorted(items, key=_dump_key)}
        key = id(value)
        if key in seen and seen[key][1] is value:
            return {'@': seen[key][0]}
        if isinstance(value, dict):
            seen[key] = (counter[0], value)
            counter[0] += 1
            rows = [[walk(k, depth + 1), walk(v, depth + 1)] for k, v in value.items()
                    if not (_is_text(k) and k in DIGEST_SKIP)]
            return {'dict': sorted(rows, key=lambda kv: _dump_key(kv[0]))}
        if isinstance(value, type) or type(value).__name__ in (
                'function', 'instancemethod', 'method', 'builtin_function_or_method', 'module'):
            return u'<{0}>'.format(type(value).__name__)
        fields = getattr(value, '__dict__', None)
        if fields is None:
            module = getattr(type(value), '__module__', '') or ''
            return u'<{0}.{1}>'.format(module, type(value).__name__)
        seen[key] = (counter[0], value)
        counter[0] += 1
        out = {'class': getattr(value, '_kind', None) or type(value).__name__}    # a `wire.Bag` as what it was
        for k in sorted(fields):
            if k in DIGEST_SKIP or k == '_kind':
                continue
            out[k] = walk(fields[k], depth + 1)
        return out

    return walk(laid, 0)


def _dump_key(value):
    return json.dumps(value, sort_keys=True)


# ----------------------------------------------------------------------
# files
# ----------------------------------------------------------------------

def capture_folder(title=None):
    """A new folder for this run's order and dishes, or None - only where %APPDATA%\\Tadpole\\capture exists.

    Made by hand on the machine that should write them; nothing is written anywhere else."""
    import time
    base = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'Tadpole', 'capture')
    if not os.path.isdir(base):
        return None
    name = u'{0}_{1}'.format(u''.join(ch if ch.isalnum() or ch in u'-_' else u'_' for ch in u'{0}'.format(title or u'run')),
                             time.strftime('%Y%m%d-%H%M%S'))
    return os.path.join(base, name)


def save(path, data):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    # ensure_ascii=False: IronPython's json cannot escape Cyrillic ("'ascii' codec can't encode", `chat.send`) -
    # the text stays unicode and goes to the file as UTF-8
    text = json.dumps(data, sort_keys=True, ensure_ascii=False)
    if not isinstance(text, type(u'')):
        text = text.decode('utf-8')
    with io.open(path, 'w', encoding='utf-8') as handle:
        handle.write(text)


def load(path):
    with io.open(path, encoding='utf-8') as handle:
        return json.loads(handle.read())
