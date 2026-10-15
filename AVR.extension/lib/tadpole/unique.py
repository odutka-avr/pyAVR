# -*- coding: utf-8 -*-
"""Unique floors per section, by wall LENGTHS.

Stage 3-4 of the cutter ("Поділ на унікальні поверхи"), computed from what
the input collection already holds - no model elements are read or written:

    1. per residential floor (section, storey): the sum of the base lengths
       of its OUTER wall runs. Floors of one section with the same sum form
       one SET.
    2. every non-insolated (dark) stretch belongs to the floor of the run it
       came off - `Stretch.run` - so no geometric matching is needed here.
    3. each set is split further by the sum of the dark stretch lengths on
       the floor. What is left are the UNIQUE FLOORS.

Named S1, S2 ... in storey order per section; a set that splits is S1.1,
S1.2 ... Lengths are compared rounded to PRECISION_M, so a 1 mm drawing
difference does not create a new floor type.

Runs on IronPython 2.7 and CPython 3 alike - no f-strings, no type hints.
"""

FT_TO_M = 0.3048
PRECISION_M = 2          # decimals of a metre -> 10 mm


class UniqueFloor(object):
    """One unique floor of one section."""

    def __init__(self, section, name, outer_sum, dark_sum, floors):
        self.section = section
        self.name = name
        self.outer_sum = outer_sum      # m
        self.dark_sum = dark_sum        # m
        self.floors = floors            # [Floor], bottom to top

    @property
    def levels(self):
        return [f.level_name for f in self.floors]

    @property
    def outer_lengths(self):
        return self.floors[0].outer_lengths

    @property
    def dark_lengths(self):
        return self.floors[0].dark_lengths


class Floor(object):
    """One residential storey of one section, with its walls."""

    def __init__(self, section, level_name, level_elev):
        self.section = section
        self.level_name = level_name
        self.level_elev = level_elev
        self.outer = []          # WallRun
        self.dark = []           # Stretch

    @property
    def outer_lengths(self):
        return [round(r.length_m, PRECISION_M) for r in self.outer]

    @property
    def dark_lengths(self):
        return [round(abs(s.b - s.a) * FT_TO_M, PRECISION_M) for s in self.dark]

    @property
    def outer_sum(self):
        return round(sum(self.outer_lengths), PRECISION_M)

    @property
    def dark_sum(self):
        return round(sum(self.dark_lengths), PRECISION_M)


def _section_key(value):
    try:
        return (0, float(value), u'')
    except (TypeError, ValueError):
        return (1, 0.0, u'{0}'.format(value))


def floors_of(outer_walls, darkloft):
    """{section: [Floor bottom to top]} from the collected runs/stretches."""
    by_key = {}
    for run in outer_walls:
        rec = run.record
        key = (rec.section, run.level_name)
        if key not in by_key:
            by_key[key] = Floor(rec.section, run.level_name, run.level_elev)
        by_key[key].outer.append(run)
    for stretch in darkloft or []:
        run = stretch.run
        key = (run.record.section, run.level_name)
        if key in by_key:
            by_key[key].dark.append(stretch)
    out = {}
    for floor in by_key.values():
        out.setdefault(floor.section, []).append(floor)
    for floors in out.values():
        floors.sort(key=lambda f: (f.level_elev if f.level_elev is not None else 0.0,
                                   f.level_name or u''))
    return out


def unique_floors(outer_walls, darkloft):
    """{section: [UniqueFloor]} - steps 1-3 of the module docstring."""
    result = {}
    for section, floors in floors_of(outer_walls, darkloft).items():
        sets = []                       # [(outer_sum, [Floor])], first-seen order
        for f in floors:
            for s in sets:
                if s[0] == f.outer_sum:
                    s[1].append(f)
                    break
            else:
                sets.append((f.outer_sum, [f]))
        uniq = []
        for n, (outer_sum, members) in enumerate(sets, 1):
            subs = []
            for f in members:
                for s in subs:
                    if s[0] == f.dark_sum:
                        s[1].append(f)
                        break
                else:
                    subs.append((f.dark_sum, [f]))
            for m, (dark_sum, group) in enumerate(subs, 1):
                name = u'S{0}'.format(n) if len(subs) == 1 else u'S{0}.{1}'.format(n, m)
                uniq.append(UniqueFloor(section, name, outer_sum, dark_sum, group))
        result[section] = uniq
    return result


def sorted_sections(result):
    return sorted(result.keys(), key=_section_key)


def as_dict(result):
    """Plain data - for JSON, the RndMCP route, or the cutter."""
    out = {}
    for section in sorted_sections(result):
        out[u'{0}'.format(section)] = [dict(
            name=u.name, outer_sum_m=u.outer_sum, dark_sum_m=u.dark_sum,
            levels=u.levels, outer_m=u.outer_lengths, dark_m=u.dark_lengths)
            for u in result[section]]
    return out
