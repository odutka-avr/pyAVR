# -*- coding: utf-8 -*-
"""Report rendering for both buttons.

§4 of the spec requires the tool to report what it rejected rather than
dropping it silently. That requirement is why this module exists.
"""

from tadpole import geom
from tadpole.config import (
    TARGET_CATEGORIES, TYPE_PARAM, SECTION_PARAM, ANGLE_TOL_DEG,
)

SQFT_TO_SQM = 0.09290304
FT_TO_M = 0.3048

CLASS_ORDER = [u'section', u'commercial', u'parking', u'other']


def _m(value_ft):
    if value_ft is None:
        return '-'
    return '{0:.2f}'.format(value_ft * FT_TO_M)


def _sqm(value_sqft):
    return '{0:.1f}'.format(value_sqft * SQFT_TO_SQM)


# THE REPORT IN THE COMPANY'S STYLE (user, 2026-10-16: "звіт теж у стиль компанії") - the AVR Design System's
# web-app layer: monochrome, black the only strong colour, 2 px corners, 1 px hairlines; Roboto for text, Roboto
# Condensed in capitals for headings and column names; the title on a black banner with white capitals at the right
# edge, every section opened by a numbered black chip. pyRevit's output window is Internet Explorer: plain CSS only
# (no variables, no grid), so the tokens are written out.
AVR_CSS = u"""
body { background: #E9EAEC; color: #171C21; font-family: Roboto, Tahoma, sans-serif; font-size: 10pt;
       counter-reset: avrsec; }
.entry { padding-left: 16px; padding-right: 16px; }
p, li { line-height: 1.45; }
h1 { background: #171C21; color: #FFFFFF; text-align: right; text-transform: uppercase; letter-spacing: 1px;
     font-family: "Roboto Condensed", "Arial Narrow", Tahoma, sans-serif; font-weight: normal; font-size: 13pt;
     padding: 9px 16px; margin: 0 0 14px 0; }
h2 { counter-increment: avrsec; color: #171C21; text-transform: uppercase; letter-spacing: 0.4px;
     font-family: "Roboto Condensed", "Arial Narrow", Tahoma, sans-serif; font-weight: normal; font-size: 11.5pt;
     border-bottom: 1px solid #C8CBCF; padding: 0 0 6px 0; margin: 26px 0 10px 0; }
h2:before { content: counter(avrsec, decimal-leading-zero); display: inline-block; background: #171C21;
            color: #FFFFFF; font-family: Roboto, Tahoma, sans-serif; font-weight: bold; font-size: 7.5pt;
            letter-spacing: 0; width: 18px; height: 16px; line-height: 16px; text-align: center;
            border-radius: 2px; margin-right: 8px; vertical-align: 1px; }
h3 { color: #5C636B; text-transform: uppercase; letter-spacing: 0.4px;
     font-family: "Roboto Condensed", "Arial Narrow", Tahoma, sans-serif; font-weight: normal; font-size: 10pt;
     margin: 16px 0 6px 0; }
h3:empty { display: none; }
table { border-collapse: collapse; width: 100%; background: #FFFFFF; border: 1px solid #E2E4E6;
        margin: 6px 0 14px 0; }
table, th, td { border: 0; }
th { background: #FFFFFF; color: #8F949C; text-align: left; text-transform: uppercase; letter-spacing: 0.4px;
     font-family: "Roboto Condensed", "Arial Narrow", Tahoma, sans-serif; font-weight: normal; font-size: 8.5pt;
     padding: 8px 10px; border-bottom: 1px solid #C8CBCF; }
td { color: #33363D; padding: 7px 10px; border-bottom: 1px solid #E2E4E6; vertical-align: top; }
tr:nth-child(odd) { background: #FFFFFF; }
tr:nth-child(even) { background: #F4F5F6; }
code { font-family: Consolas, monospace; font-size: 9pt; color: #171C21; background: #F4F5F6;
       border: 1px solid #E2E4E6; border-radius: 2px; padding: 0 3px; }
em { color: #5C636B; }
strong { color: #171C21; }
a, a:visited { color: #171C21; }
hr { border: 0; border-top: 1px solid #C8CBCF; }
"""


def style(output):
    """The output window in the AVR Design System's style. Cosmetic: a window that refuses it keeps pyRevit's."""
    try:
        output.add_style(AVR_CSS)
    except Exception:
        pass
    try:
        output.set_title(u'Tadpole')
    except Exception:
        pass


def core_flips(output, offers, refused, chosen):
    """THE CORE TO THE OPPOSITE SIDE (user, 2026-10-16): which straight sections were offered, which were taken, and
    why the others were not offered."""
    if not offers and not refused:
        return
    rows = []
    for o in offers:
        mix, after = o.get('mix') or {}, o.get('mix_after') or o.get('mix') or {}
        rows.append([o['section'], u'{0} -> {1}'.format(o['side_from'], o['side_to']), o.get('how', u''),
                     u', '.join(u'{0} {1}'.format(name, mix.get(k, 0)) if mix.get(k, 0) == after.get(k, 0) else
                                u'{0} {1} -> {2}'.format(name, mix.get(k, 0), after.get(k, 0))
                                for k, name in ((1, u'1k'), (2, u'2k'), (3, u'3k'))),
                     u'-' if o.get('lost_before') is None else u'{0:.1f} -> {1:.1f}'.format(
                         o['lost_before'], o['lost_after']),
                     (u'moved' if o['section'] in chosen else u'offered, kept where it was') + (
                         u' - possible, NOT RECOMMENDED: its long sides face {0} and {1}, the core goes to the sunny one'.format(
                             *(o.get('faces') or (u'?', u'?'))) if o.get('warn') else u'')])
    for section, why in refused:
        rows.append([section, u'-', u'-', u'-', u'-', u'not offered: {0}'.format(why)])
    rows.sort(key=lambda r: _section_sort(r[0]))
    output.print_md(u'## Cores to the opposite side')
    output.print_table(
        table_data=rows,
        columns=[u'section', u'side now -> then', u'how', u'flats over all unique floors (before -> after)',
                 u'm2 beyond the bands and dead, before -> after', u'decision'])


def _flats(counts):
    if not counts:
        return u'-'
    return u' · '.join(u'{0} {1:g}'.format(name, counts.get(k, 0)) for k, name in ((1, u'1k'), (2, u'2k'), (3, u'3k')))


def _shares(counts):
    total = float(sum((counts or {}).get(k, 0) for k in (1, 2, 3)))
    if not total:
        return u'-'
    return u' · '.join(u'{0} {1:.1f} %'.format(name, 100.0 * counts.get(k, 0) / total)
                       for k, name in ((1, u'1k'), (2, u'2k'), (3, u'3k')))


def rerun_summary(output, again, held_before, held_after, brief=None):
    """SECTIONS LAID AGAIN from the window after the run (user, 2026-10-16: "звіт має доповнюватися даними про
    перегенерацію"): which sections, why (the core across or back, the model's geometry now), their flats before and
    after over all their floors, what was removed for them, and the file's mix after."""
    import time
    only = sorted(again.get('only') or (), key=_section_sort)
    fresh = set(again.get('fresh') or ())
    flips = set(again.get('flips') or ())
    laid = set(again.get('laid') or ())
    rows = []
    for section in only:
        what = []
        if section in fresh:
            what.append(u'the model as it is now')
        if section in flips and section not in laid:
            what.append(u'core to the opposite side')
        elif section in laid and section not in flips:
            what.append(u'core back to its side')
        rows.append([section, u'; '.join(what) or u'-', _flats(held_before.get(section)),
                     _flats(held_after.get(section))])
    output.print_md(u'## Sections laid again')
    output.print_table(table_data=rows, columns=[u'section', u'why', u'flats before, all floors',
                                                 u'flats after, all floors'])
    removed = again.get('removed')
    if removed:
        deleted, failed, kinds = removed
        parts = u', '.join(u'{0} {1}'.format(n, k) for k, n in sorted(kinds.items()) if n)
        output.print_md(u'Removed first: **{0}** element(s) the last run made for these sections{1}{2}.'.format(
            deleted, u' ({0})'.format(parts) if parts else u'',
            u'; **{0}** could not be removed'.format(failed) if failed else u''))
    total_before, total_after = {1: 0, 2: 0, 3: 0}, {1: 0, 2: 0, 3: 0}
    for held, total in ((held_before, total_before), (held_after, total_after)):
        for counts in (held or {}).values():
            for k in total:
                total[k] += (counts or {}).get(k, 0)
    line = u'The file: {0} -> **{1}**'.format(_shares(total_before), _shares(total_after))
    if brief:
        line += u' (the settings: {0})'.format(u' · '.join(
            u'{0} {1:.0f} %'.format(name, 100.0 * brief.get(k, 0)) for k, name in ((1, u'1k'), (2, u'2k'), (3, u'3k'))))
    output.print_md(line)
    if again.get('started'):
        output.print_md(u'_Laid again in {0:.0f} s._'.format(time.time() - again['started']))


def _header(output, title):
    output.print_md('# ' + title)
    cats = ', '.join([str(c).replace('OST_', '') for c in TARGET_CATEGORIES])
    output.print_md(
        '`{0}` | classified on `{1}` | angle tolerance {2}deg'.format(
            cats, TYPE_PARAM, ANGLE_TOL_DEG))


def _class_table(output, result):
    rows = []
    for use_class in CLASS_ORDER:
        recs = result.of_class(use_class)
        if not recs:
            continue
        solids = sum([r.geometry['solids'] for r in recs if r.has_geometry])
        vertical = sum([r.geometry['counts'][geom.VERTICAL]
                        for r in recs if r.has_geometry])
        horizontal = sum([r.geometry['counts'][geom.UP] +
                          r.geometry['counts'][geom.DOWN]
                          for r in recs if r.has_geometry])
        rows.append([use_class, len(recs), solids, horizontal, vertical])
    output.print_table(
        table_data=rows,
        columns=['use class', 'elements', 'solids', 'horiz faces', 'vert faces'],
        title='By use class')


def _storey_table(output, result, detailed=False):
    rows = []
    for storey in result.storeys:
        counts = {}
        for rec in storey.records:
            counts[rec.use_class] = counts.get(rec.use_class, 0) + 1
        row = [storey.name, _m(storey.elevation), _m(storey.height),
               len(storey.records)]
        if detailed:
            for use_class in CLASS_ORDER:
                row.append(counts.get(use_class, 0))
        rows.append(row)

    columns = ['level', 'elevation m', 'height m', 'elements']
    if detailed:
        columns = columns + CLASS_ORDER
    output.print_table(table_data=rows, columns=columns,
                       title='Storeys, bottom to top')


def _rejects(output, result, linkify=None):
    output.print_md('## What was not used')
    rows = [
        ['no "{0}" parameter'.format(TYPE_PARAM), len(result.no_param)],
        ['parameter present but matched no class', len(result.unmatched)],
        ['no Level', len(result.no_level)],
        ['no solid geometry', len(result.no_geometry)],
        ['geometry errors', len(result.errors)],
    ]
    output.print_table(table_data=rows, columns=['reason', 'count'])

    if result.unmatched:
        output.print_md(
            '_Unmatched elements are in `other` and still act as shading._')

    derived = getattr(result, 'level_derived', None)
    if derived:
        output.print_md(
            '_**{0}** element(s) had no readable Level and were given the '
            'nearest one at or below their base. A Mass usually has none, '
            'and nothing can place a wall without a level to measure the '
            'base offset from. Set `LEVEL_FALLBACK_FROM_GEOMETRY = False` '
            'in `config.py` to drop them instead._'.format(len(derived)))


def _face_table(output, result):
    """Step 3 — how every face was classified."""
    output.print_md('## Step 3 — faces by classification')
    rows = []
    for use_class in CLASS_ORDER:
        recs = [r for r in result.of_class(use_class) if r.has_geometry]
        if not recs:
            continue
        agg = {}
        area = {}
        for kind in (geom.UP, geom.DOWN, geom.VERTICAL,
                     geom.DROPPED, geom.NONPLANAR):
            agg[kind] = sum([r.geometry['counts'][kind] for r in recs])
            area[kind] = sum([r.geometry['area'][kind] for r in recs])
        rows.append([
            use_class,
            agg[geom.UP], agg[geom.DOWN], agg[geom.VERTICAL],
            agg[geom.DROPPED], agg[geom.NONPLANAR],
            _sqm(area[geom.UP] + area[geom.DOWN]),
            _sqm(area[geom.VERTICAL]),
        ])
    output.print_table(
        table_data=rows,
        columns=['use class', 'up', 'down', 'vertical', 'dropped',
                 'non-planar', 'slab m2', 'facade m2'],
        title='Up = slab top, down = underside, vertical = facade')

    dropped = sum([r.geometry['counts'][geom.DROPPED]
                   for r in result.records if r.has_geometry])
    nonplanar = sum([r.geometry['counts'][geom.NONPLANAR]
                     for r in result.records if r.has_geometry])
    if dropped:
        output.print_md(
            ':warning: **{0}** faces were dropped — tilted more than '
            '{1}deg from both horizontal and vertical.'.format(
                dropped, ANGLE_TOL_DEG))
    if nonplanar:
        output.print_md(
            ':warning: **{0}** non-planar faces were routed out before '
            'classification.'.format(nonplanar))


def _section_table(output, result, only=None):
    """Sections, each a stack ordered by height."""
    if not result.sections:
        return
    output.print_md('## Sections')
    rows = []
    for section in result.sections:
        if only is not None and section.key not in only:
            continue
        row = [section.key, len(section.records), section.storey_count,
               _m(section.min_elev), _m(section.max_elev)]
        for use_class in CLASS_ORDER:
            row.append(section.count_of_class(use_class))
        rows.append(row)
    output.print_table(
        table_data=rows,
        columns=(['section', 'elements', 'storeys', 'lowest m', 'highest m']
                 + CLASS_ORDER),
        title='Ordered by section number, each stacked bottom to top')


def basic(output, result):
    """Button 1 — steps 1 and 2, headline numbers only."""
    _header(output, 'Tadpole — collect')

    total = len(result.records)
    with_geo = len([r for r in result.records if r.has_geometry])
    output.print_md(
        '**{0}** elements collected, **{1}** with solid geometry, '
        '**{2}** storeys.'.format(total, with_geo, len(result.storeys)))

    if total == 0:
        output.print_md(
            ':warning: Nothing collected. Check that the model contains '
            'elements in the target categories.')
        return

    _class_table(output, result)
    _face_table(output, result)
    _storey_table(output, result, detailed=False)

    _section_table(output, result)
    _rejects(output, result)


def detailed(output, result, placements=None, missing_types=None,
             available_types=None, type_stats=None, aborted=None,
             title=None):
    """Button 2 — everything button 1 shows, plus face detail and roofs."""
    _header(output, title or 'Tadpole — collect and place roofs')

    total = len(result.records)
    with_geo = len([r for r in result.records if r.has_geometry])
    output.print_md(
        '**{0}** elements collected, **{1}** with solid geometry, '
        '**{2}** storeys.'.format(total, with_geo, len(result.storeys)))

    _class_table(output, result)
    _face_table(output, result)
    _section_table(output, result)
    _storey_table(output, result, detailed=True)

    # --- roof types ----------------------------------------------------
    if missing_types:
        output.print_md('## Roof types not found')
        rows = [[k, v] for k, v in sorted(missing_types.items())]
        output.print_table(table_data=rows,
                           columns=['use class', 'name configured'])

        if type_stats is not None:
            output.print_md(
                'Scan: **{0}** roof types found, **{1}** names read, '
                '**{2}** unreadable.'.format(
                    type_stats.get('scanned', 0),
                    type_stats.get('named', 0),
                    type_stats.get('unnamed', 0)))
            if type_stats.get('unnamed'):
                output.print_md(
                    ':warning: Some type names could not be read at all. '
                    'That is the bug, not the configured name — use '
                    '`ROOF_TYPE_IDS` in config.py as a workaround.')
            for err in type_stats.get('errors', []):
                output.print_md(':warning: `{0}`'.format(err))

        output.print_md(
            'Fix the name in `lib/tadpole/config.py` -> `ROOF_TYPES`. '
            'Roof types found in this model:')
        if available_types:
            output.print_md('\n'.join(
                ['- `{0}`'.format(n) for n in sorted(available_types.keys())]))
        else:
            output.print_md(
                '_None — the scan returned nothing, so the lookup itself '
                'failed rather than the names being wrong._')

    # --- placement -----------------------------------------------------
    if placements is not None:
        output.print_md('## Roofs placed')
        ok = [p for p in placements if p.ok]
        bad = [p for p in placements if not p.ok]

        try:
            from tadpole import roofs as _roofs
            output.print_md(
                '_Engine: {0} | call path: `{1}`_'.format(
                    'CPython 3' if _roofs.IS_CPYTHON else 'IronPython 2.7',
                    _roofs.CALL_MODE or 'none succeeded'))
        except Exception:
            pass

        if aborted:
            output.print_md(':warning: **Run stopped early** — {0}'.format(
                aborted))

        rows = []
        for use_class in CLASS_ORDER:
            n_ok = len([p for p in ok if p.record.use_class == use_class])
            n_bad = len([p for p in bad if p.record.use_class == use_class])
            if n_ok or n_bad:
                rows.append([use_class, n_ok, n_bad])
        output.print_table(table_data=rows,
                           columns=['use class', 'placed', 'failed'])

        multiloop = [p for p in ok if p.loops_found > 1]
        if multiloop:
            output.print_md(
                ':warning: **{0}** floors had more than one boundary loop. '
                'Only the outer one was used — openings are ignored.'.format(
                    len(multiloop)))

        if bad:
            output.print_md('### Failures')
            reasons = {}
            for p in bad:
                reasons[p.reason] = reasons.get(p.reason, 0) + 1
            rows = [[k, v] for k, v in sorted(reasons.items(),
                                              key=lambda kv: -kv[1])]
            output.print_table(table_data=rows, columns=['reason', 'count'])

            output.print_md('First 20 failing elements:')
            for p in bad[:20]:
                try:
                    link = output.linkify(p.record.id)
                except Exception:
                    link = str(p.record.id)
                output.print_md('- {0} {1} — {2}'.format(
                    link, p.record.name, p.reason))

    _rejects(output, result)


def probe_failure(output, info):
    """Everything known about why the single test roof did not work."""
    output.print_md('## Probe failed')
    output.print_md(
        'One roof was attempted and rolled back. **The model is '
        'unchanged.**')

    rows = [
        ['reflection method found', info.get('reflection_found')],
        ['use class', info.get('use_class')],
        ['element id', info.get('element')],
        ['roof type', info.get('roof_type')],
        ['level', info.get('level')],
    ]
    output.print_table(table_data=rows, columns=['', ''])

    output.print_md('### What each call path said')
    for name, ok, message in info.get('attempts', []):
        output.print_md('**{0}** — {1}'.format(name, 'ok' if ok else 'failed'))
        output.print_md('```\n{0}\n```'.format(message))

    fp = info.get('footprint')
    if fp:
        output.print_md('### The footprint that was attempted')
        rows = [
            ['curves', fp.get('curves')],
            ['curve kinds', ', '.join(
                ['{0} x{1}'.format(k, v)
                 for k, v in sorted(fp.get('kinds', {}).items())])],
            ['shortest curve mm', '{0:.3f}'.format(fp['shortest_mm'])
             if fp.get('shortest_mm') is not None else '-'],
            ['perimeter mm', '{0:.0f}'.format(fp['total_mm'])
             if fp.get('total_mm') is not None else '-'],
            ['loop is open', fp.get('is_open')],
            ['loop has plane', fp.get('has_plane')],
            ['face normal Z', fp.get('face_normal_z')],
            ['face Z mm', '{0:.1f}'.format(fp['face_z_mm'])
             if fp.get('face_z_mm') is not None else '-'],
        ]
        output.print_table(table_data=rows, columns=['', ''])

        shortest = fp.get('shortest_mm')
        if shortest is not None and shortest < 1.0:
            output.print_md(
                ':warning: The shortest curve is **{0:.3f} mm**, below '
                "Revit's ~0.8 mm tolerance. Revit refuses sketches "
                'containing curves that short — this is very likely the '
                'cause.'.format(shortest))
        kinds = fp.get('kinds', {})
        exotic = [k for k in kinds if k not in ('Line', 'Arc')]
        if exotic:
            output.print_md(
                ':warning: The footprint contains {0}. A roof sketch '
                'accepts lines and arcs; other curve types have to be '
                'approximated first.'.format(', '.join(exotic)))
        if fp.get('is_open'):
            output.print_md(
                ':warning: The loop reports as **open**. A roof footprint '
                'must be closed.')


def plan_summary(output, counts):
    """Steps 5 and 6 — what the classification decided, before building."""
    output.print_md('## Steps 5 and 6 — classification')

    wall_rows = []
    floor_rows = []
    for use_class in CLASS_ORDER:
        outer = counts.get((use_class, 'wall', 'outer'), 0)
        inner = counts.get((use_class, 'wall', 'inner'), 0)
        if outer or inner:
            wall_rows.append([use_class, outer, inner, outer + inner])
        top = counts.get((use_class, 'floor', 'top'), 0)
        bottom = counts.get((use_class, 'floor', 'bottom'), 0)
        between = counts.get((use_class, 'floor', 'between'), 0)
        if top or bottom or between:
            floor_rows.append([use_class, top, bottom, between,
                               top + bottom + between])

    if wall_rows:
        output.print_table(
            table_data=wall_rows,
            columns=['use class', 'outer', 'inner', 'walls'],
            title='Walls — outer is exposed, inner is buried against a '
                  'neighbour')
    if floor_rows:
        output.print_table(
            table_data=floor_rows,
            columns=['use class', 'top', 'bottom', 'between', 'floors'],
            title='Floors — top is uncovered, between is a storey interface')

    total = sum(counts.values())
    output.print_md('**{0}** elements planned.'.format(total))

    # what the floor classifier could not resolve
    try:
        from tadpole import classify
        stats = dict(classify.LAST_FLOOR_STATS or {})
    except Exception:
        stats = {}
    messages = stats.pop('messages', [])
    losses = [(k, v) for k, v in sorted(stats.items()) if v]
    if losses:
        output.print_md('### Regions the floor classifier could not resolve')
        output.print_table(table_data=[[k, v] for k, v in losses],
                           columns=['what happened', 'count'])
        output.print_md(
            'A failed boolean means that face produced **no floor**, or '
            'fell back to a whole uncut one. Coincident planes are the '
            'usual cause.')
        for message in messages[:5]:
            output.print_md('`{0}`'.format(message))
    if total and not any(k[2] == 'inner' for k in counts):
        output.print_md(
            ':warning: No **inner** walls were found at all. Either the '
            'blocks genuinely never touch, or coincident faces are not '
            'landing in the same bucket — check `classify.POINT_TOL`.')
    if total and not any(k[2] == 'between' for k in counts):
        output.print_md(
            ':warning: No **between** floors were found. Storeys are not '
            'meeting at a shared elevation, which for a stacked massing '
            'would be surprising.')


def build_probe(output, info):
    """One test wall and one test floor, measured then rolled back."""
    output.print_md('## Test placement')
    output.print_md(
        'One wall and one floor were created, measured, and rolled back. '
        '**The model is unchanged.**')

    for kind in ('wall', 'floor'):
        item = info.get(kind)
        output.print_md('### {0}'.format(kind))
        if item is None:
            output.print_md('_not attempted — nothing eligible_')
            continue
        if not item.get('ok'):
            output.print_md('Failed for `{0}` / `{1}`:'.format(
                item.get('use_class'), item.get('position')))
            output.print_md('```\n{0}\n```'.format(item.get('error')))
            continue

        want = item.get('want_z_mm')
        got = item.get('got_z_mm')
        rows = [
            ['use class', item.get('use_class')],
            ['position', item.get('position')],
            ['type', item.get('type')],
            ['face bottom mm', '{0:.1f}'.format(want)
             if want is not None else '-'],
            ['element bottom mm', '{0:.1f}'.format(got)
             if got is not None else '-'],
        ]
        if want is not None and got is not None:
            rows.append(['delta mm', '{0:.1f}'.format(got - want)])
        output.print_table(table_data=rows, columns=['', ''])

        if want is not None and got is not None and abs(got - want) > 1.0:
            output.print_md(
                ':warning: The element landed **{0:.0f} mm** from the face '
                'it was built on. Adjust the offset before building the '
                'rest.'.format(got - want))


def build_result(output, made, aborted=None, types=None):
    """What actually got built, by class and position."""
    output.print_md('## Placed')

    ok = [m for m in made if m.ok]
    bad = [m for m in made if not m.ok]

    rows = []
    for use_class in CLASS_ORDER:
        for kind, positions in (('wall', ['outer', 'inner']),
                                ('floor', ['top', 'bottom', 'between'])):
            for position in positions:
                n_ok = len([m for m in ok
                            if m.record.use_class == use_class
                            and m.kind == kind and m.position == position])
                n_bad = len([m for m in bad
                             if m.record.use_class == use_class
                             and m.kind == kind and m.position == position])
                if n_ok or n_bad:
                    rows.append([use_class, kind, position, n_ok, n_bad])
    output.print_table(
        table_data=rows,
        columns=['use class', 'kind', 'position', 'placed', 'failed'])

    if types:
        rows = []
        for use_class in CLASS_ORDER:
            for kind, positions in (('wall', ['outer', 'inner']),
                                    ('floor', ['top', 'bottom', 'between'])):
                for position in positions:
                    element = types.get(kind + 's', {}).get(
                        (use_class, position))
                    if element is not None:
                        rows.append([use_class, kind, position,
                                     _tname(element)])
        if rows:
            output.print_table(
                table_data=rows,
                columns=['use class', 'kind', 'position', 'type'],
                title='Types used — every row should differ')

    if aborted:
        output.print_md(':warning: **Run stopped early** — {0}'.format(
            aborted))

    if bad:
        output.print_md('### Failures')
        reasons = {}
        for m in bad:
            key = '{0} {1}: {2}'.format(m.kind, m.position, m.reason)
            reasons[key] = reasons.get(key, 0) + 1
        rows = [[k, v] for k, v in sorted(reasons.items(),
                                          key=lambda kv: -kv[1])]
        output.print_table(table_data=rows[:15], columns=['reason', 'count'])


def _tname(element):
    if element is None:
        return '-'
    try:
        from tadpole.roofs import type_name
        return type_name(element) or '-'
    except Exception:
        return '-'


# ----------------------------------------------------------------------
# Step 8 — insolation
# ----------------------------------------------------------------------

def _north_note(north):
    """0.00 deg is ambiguous on its own — say which kind it is."""
    text = '{0:.2f} deg'.format(north)
    if abs(north) < 1e-9:
        text += '  (site is not rotated, so the sun path is not turned)'
    return text


def sun_setup(output, latitude, north, vectors, run_count, is_default):
    from tadpole.config import (
        SUN_DAY_OF_YEAR, SUN_START_HOUR, SUN_END_HOUR, SUN_TIMESTEP,
        SUN_REQUIRED_HOURS, SUN_COLUMN_MM, SUN_SAMPLE_HEIGHT_MM,
    )
    output.print_md('## Step 8 — sun study setup')
    rows = [
        ['latitude', '{0:.3f} deg'.format(latitude)],
        ['true north', _north_note(north)],
        ['day of year', SUN_DAY_OF_YEAR],
        ['solar hours', '{0} - {1}'.format(SUN_START_HOUR, SUN_END_HOUR)],
        ['timestep', '{0} per hour'.format(SUN_TIMESTEP)],
        ['sun vectors above horizon', len(vectors)],
        ['required hours', SUN_REQUIRED_HOURS],
        ['column spacing', '{0:.0f} mm'.format(SUN_COLUMN_MM)],
        ['sample height', '{0:.0f} mm above wall base'.format(
            SUN_SAMPLE_HEIGHT_MM)],
        ['outer wall runs to test', run_count],
    ]
    output.print_table(table_data=rows, columns=['', ''])

    if is_default:
        output.print_md(
            ':warning: The model still carries a default site location.')

    if vectors:
        # Exactly `steps` samples, one per step, taken at its middle.
        # This used to add 1 for the endpoint-sampled version and then
        # warn that a sample had been discarded when none had.
        expected = int(round((SUN_END_HOUR - SUN_START_HOUR) * SUN_TIMESTEP))
        if len(vectors) != expected:
            output.print_md(
                ':warning: {0} of {1} samples have the sun above the '
                'horizon — the rest were discarded.'.format(
                    len(vectors), expected))
        output.print_md(
            '_Times are **solar**, not clock. Mean solar time puts noon '
            'at 12:00 by construction, so longitude, time zone and DST '
            'do not enter the calculation._')


def sun_results(output, runs, findings):
    from tadpole.config import SUN_REQUIRED_HOURS
    output.print_md('## Sun study results')

    output.print_md(
        '**{0}** outer wall runs tested, **{1}** under-lit stretches '
        'found below {2} h.'.format(
            len(runs), len(findings), SUN_REQUIRED_HOURS))

    if not findings:
        output.print_md(
            'Every outer section wall meets the threshold. If that looks '
            'too good, check the north angle and that context geometry '
            'is being collected — with no obstructions everything passes.')
        return

    buckets = {}
    for _seg, _a, _b, hours in findings:
        key = int(hours * 2) / 2.0      # half-hour bands
        buckets[key] = buckets.get(key, 0) + 1
    rows = [['{0:.1f} - {1:.1f} h'.format(k, k + 0.5), v]
            for k, v in sorted(buckets.items())]
    output.print_table(table_data=rows, columns=['sun hours', 'stretches'],
                       title='How dark the failing stretches are')

    by_level = {}
    for seg, _a, _b, _h in findings:
        name = seg.record.level_name or '?'
        by_level[name] = by_level.get(name, 0) + 1
    rows = [[k, v] for k, v in sorted(by_level.items(),
                                      key=lambda kv: -kv[1])]
    output.print_table(table_data=rows[:20],
                       columns=['level', 'stretches'],
                       title='Where they are')


def sun_placed(output, placed, failed):
    output.print_md('## Non-insolated walls placed')
    output.print_md('**{0}** placed.'.format(placed))
    if failed:
        rows = [[k, v] for k, v in sorted(failed.items(),
                                          key=lambda kv: -kv[1])]
        output.print_table(table_data=rows, columns=['reason', 'count'])
    output.print_md(
        '_Each carries its measured hours in **Comments**, so the result '
        'survives a save and can be scheduled or read by another script._')


# ======================================================================
# Button 5 — Tadpole
# ======================================================================

def inputs_manifest(output, amounts=None):
    """Every cutter input: what produces it, and how much of it there is.

    One table, because the question is single - did the cutter get its
    inputs, and what were they. Counts in one place and settings in
    another made the reader hold three tables at once to answer it.
    """
    from tadpole import inputs

    amounts = amounts or {}
    output.print_md('## Inputs for the cutter')

    rows = []
    for wanted, produced, status, _note in inputs.MANIFEST:
        rows.append([wanted, produced or '-',
                     amounts.get(wanted, '-'), status])
    output.print_table(
        table_data=rows,
        columns=['cutter wants', 'produced as', 'amount', 'status'])

    gaps = [r for r in inputs.MANIFEST if r[2] == inputs.MISSING]
    for wanted, _produced, _status, note in gaps:
        output.print_md('**{0}** — {1}'.format(wanted, note))
    if gaps:
        output.print_md(
            '_{0} of {1} have no producer. They are named rather than '
            'shipped as empty lists, because an empty list reads '
            'downstream as "measured and found nothing"._'.format(
                len(gaps), len(inputs.MANIFEST) - 1))

    output.print_md('### Structure of the lists above')
    output.print_md(
        '_The table above says how many; this says of what. None of '
        'these are dicts — they are the real Python objects, or plain '
        'tuples where a class would be overkill._')
    output.print_table(
        table_data=[[produced, shape, fields]
                    for produced, shape, fields in inputs.STRUCTURE],
        columns=['produced as', 'one item is', 'fields'])

def inputs_placed(output, roof_placements, roof_aborted,
                  walls_placed, walls_failed, walls_aborted):
    """What actually went into the model."""
    from tadpole.config import SUN_DARK_WALL_TYPE

    output.print_md('## Placed in the model')

    ok = len([p for p in (roof_placements or []) if p.ok])
    bad = len(roof_placements or []) - ok

    output.print_table(
        table_data=[
            ['commercial + parking roofs', ok, bad, ''],
            ['non-insolated walls', walls_placed,
             sum(walls_failed.values()) if walls_failed else 0,
             SUN_DARK_WALL_TYPE],
        ],
        columns=['what', 'placed', 'failed', 'type'],
        title='Model changes')

    if roof_aborted:
        output.print_md('**Roofs: {0}**'.format(roof_aborted))
    if walls_aborted:
        output.print_md('**Non-insolated walls: {0}**'.format(walls_aborted))

    if bad:
        reasons = {}
        for item in roof_placements:
            if not item.ok and item.reason:
                key = str(item.reason)[:120]
                reasons[key] = reasons.get(key, 0) + 1
        output.print_table(
            table_data=[[k, v] for k, v in sorted(reasons.items(),
                                                  key=lambda kv: -kv[1])],
            columns=['why a roof failed', 'count'])

    if walls_failed:
        output.print_table(
            table_data=[[k, v] for k, v in sorted(walls_failed.items(),
                                                  key=lambda kv: -kv[1])],
            columns=['why a wall failed', 'count'])

    output.print_md('_One **Ctrl+Z** undoes the whole run._')


def inputs_sources(output, result, abutting=None):
    """Which categories supplied what, and what was deliberately skipped.

    Collection is by CATEGORY; use class is decided afterwards from
    AVR_Тип юніта. So every wall, floor, roof, generic model and mass in
    the model is looked at, and each lands in section / commercial /
    parking / other on its own merits.
    """
    output.print_md('## Where the geometry came from')

    rows = []
    for category in sorted(result.by_category.keys()):
        recs = result.by_category[category]
        per = {}
        for rec in recs:
            per[rec.use_class] = per.get(rec.use_class, 0) + 1
        rows.append([
            category,
            len(recs),
            per.get(u'section', 0),
            per.get(u'commercial', 0),
            per.get(u'parking', 0),
            per.get(u'other', 0),
        ])
    if rows:
        output.print_table(
            table_data=rows,
            columns=['category', 'total', 'section', 'commercial',
                     'parking', 'other'],
            title='Collected by category and use class')

    if abutting is not None:
        per = {}
        for rec in abutting:
            per[rec.use_class] = per.get(rec.use_class, 0) + 1
        output.print_md(
            'Inner/outer is decided against **{0}** elements — {1}. Only '
            'the section runs are returned, but a section wall meeting a '
            'commercial block, a parking deck or a neighbour is a party '
            'wall, and it only reads as one if that neighbour took part '
            'in the comparison.'.format(
                len(abutting),
                ', '.join(['{0} {1}'.format(v, k)
                           for k, v in sorted(per.items())])))

    if result.excluded:
        per_type = {}
        for rec in result.excluded:
            key = rec.type_name or '(unnamed)'
            per_type[key] = per_type.get(key, 0) + 1
        output.print_table(
            table_data=[[k, v] for k, v in sorted(per_type.items(),
                                                  key=lambda kv: -kv[1])],
            columns=['type name', 'skipped'],
            title='Skipped — this tool placed these on an earlier run')
        output.print_md(
            '_Without this, a second run would collect its own output: '
            'the diagnostic walls and roofs would shade the building they '
            'came from, and every non-insolated wall would sit facing the '
            'face it was stamped over, turning that face into a party '
            'wall. Edit `EXCLUDE_TYPE_NAMES` in `config.py` if one of '
            'these is real geometry._')


def inputs_funnel(output, result, study_records, outer_walls, inner_walls,
                  darkloft, walls_placed=None, required=None):
    """Where section elements drop out, gate by gate.

    "It did not place walls there" has about six possible causes and they
    all look identical from the model. This counts what survives each
    gate, so the answer is a row in a table rather than a guess.
    """
    from tadpole.config import SUN_CLASSES, SUN_REQUIRED_HOURS
    from tadpole import geom

    if required is None:
        required = SUN_REQUIRED_HOURS

    in_class = []
    for use_class in SUN_CLASSES:
        in_class.extend(result.of_class(use_class))

    no_geo = [r for r in in_class if not r.has_geometry]
    with_geo = [r for r in in_class if r.has_geometry]
    no_level = [r for r in with_geo if r.level_id is None]
    derived = [r for r in with_geo if getattr(r, 'level_derived', False)]

    vertical = 0
    for rec in study_records:
        try:
            vertical += rec.geometry['counts'][geom.VERTICAL]
        except Exception:
            pass

    studied = [r for r in outer_walls if r.columns]
    unstudied = len(outer_walls) - len(studied)

    rows = [
        ['collected in {0}'.format('/'.join(SUN_CLASSES)), len(in_class), ''],
        ['  dropped: no solid geometry', len(no_geo),
         'family with no 3D, or an import'],
        ['  dropped: no Level', len(no_level),
         'even after the geometry fallback'],
        ['  (of the rest, Level was inferred)', len(derived),
         'no Level parameter to read'],
        ['reached the sun study', len(study_records), ''],
        ['vertical faces on them', vertical,
         'zero here means curved or meshed geometry'],
        ['outer wall runs', len(outer_walls),
         'what gets sun-tested'],
        ['inner wall runs', len(inner_walls),
         'party walls, never tested'],
        ['outer runs actually sampled', len(studied),
         'the rest produced no column'],
        ['  produced no sample column', unstudied,
         'run shorter than the column spacing'],
        ['under-lit stretches found', len(darkloft),
         'below {0:g} h'.format(required)],
    ]
    if walls_placed is not None:
        rows.append(['walls placed', walls_placed, ''])

    output.print_table(
        table_data=rows,
        columns=['gate', 'count', 'what a zero here means'],
        title='Why a block did or did not get walls')

    if in_class and not study_records:
        output.print_md(
            ':warning: **Everything was dropped before the study.** The two '
            'rows above the fold say which gate did it.')

    if len(outer_walls) and not len(darkloft):
        output.print_md(
            '_Every outer run met the threshold, so there was nothing to '
            'place. If that looks wrong, check the north angle and that '
            'the neighbours are being collected — with no obstructions '
            'everything passes._')

    if no_geo:
        output.print_md(
            '_Elements with no solid geometry are usually families whose '
            'geometry lives in a nested GeometryInstance that returns '
            'nothing at Fine detail, or a linked/imported form. They are '
            'collected but cannot be measured._')


def _extent_table(output, stats):
    """Where each class's blockers actually sit, in model space.

    If the context does not overlap the sections, no ray can reach it
    and it cannot shade anything, however many blockers were built.
    """
    rows = []
    boxes = {}
    for use_class in CLASS_ORDER:
        box = stats.get(use_class, {}).get('extent')
        if box is None:
            continue
        boxes[use_class] = box
        rows.append([
            use_class,
            '{0:.0f} .. {1:.0f}'.format(box[0] * FT_TO_M, box[3] * FT_TO_M),
            '{0:.0f} .. {1:.0f}'.format(box[1] * FT_TO_M, box[4] * FT_TO_M),
            '{0:.0f} .. {1:.0f}'.format(box[2] * FT_TO_M, box[5] * FT_TO_M),
        ])
    if not rows:
        return
    output.print_table(
        table_data=rows,
        columns=['use class', 'X m', 'Y m', 'Z m'],
        title='Where the blockers sit, in model space')

    section = boxes.get(u'section')
    context = boxes.get(u'other')
    if section and context:
        overlaps = all([section[i] <= context[i + 3] and
                        context[i] <= section[i + 3] for i in (0, 1, 2)])
        if not overlaps:
            output.print_md(
                ':warning: **The context does not overlap the sections.** '
                'Its geometry is somewhere else in model space, so no ray '
                'from a facade can ever reach it. That is a coordinate '
                'problem in how the geometry is read, not a shading one — '
                'a DirectShape whose solids come back untransformed sits '
                'at the origin while the building is elsewhere.')


def inputs_obstructions(output, stats, total, reach):
    """What is actually casting shadows, per use class.

    "It ignores generic models" is answerable here and nowhere else: if
    the context class does not appear in the shading column, nothing it
    contains blocked a single ray.
    """
    output.print_md('## What is casting shadows')

    # Every collected element is visited when the obstructions are built
    # and lands under exactly one key, so the per-class totals are the
    # class sizes.
    # Every element lands under exactly one of these. `box disagreed`,
    # `no element box` and `extent` are annotations ON a shading element,
    # not outcomes of their own, so counting them here would double-count.
    OUTCOMES = ('shading', 'no solids', 'no geometry', 'no bounds')
    # 'solid mode', 'faces mode', 'blocks nothing', 'errors' and 'extent'
    # are annotations on a shading element, not outcomes of their own.

    def size_of(use_class):
        per = stats.get(use_class, {})
        return sum([per.get(k, 0) for k in OUTCOMES])

    def shading_of(use_class):
        return stats.get(use_class, {}).get('shading', 0)

    other_n = size_of(u'other')
    all_n = sum([size_of(c) for c in stats])

    output.print_md(
        '**{0}** elements in `other` (context), of which **{1}** are '
        'casting shadows.'.format(other_n, shading_of(u'other')))
    output.print_md(
        '**{0}** elements collected in all, of which **{1}** are casting '
        'shadows.'.format(all_n, total))

    rows = []
    for use_class in CLASS_ORDER:
        per = stats.get(use_class)
        if not per:
            continue
        rows.append([
            use_class,
            size_of(use_class),
            per.get('shading', 0),
            per.get('faces mode', 0),
            per.get('blocks nothing', 0),
            per.get('no solids', 0),
            per.get('no geometry', 0),
            per.get('no bounds', 0),
        ])
    output.print_table(
        table_data=rows,
        columns=['use class', 'elements', 'shading', 'face mode',
                 'blocks nothing', 'no solids', 'no geometry', 'no bounds'],
        title='Obstructions built, per use class')

    _extent_table(output, stats)

    disagreed = sum([per.get('box disagreed', 0) + per.get('no element box', 0)
                     for per in stats.values()])
    if disagreed:
        output.print_md(
            '**{0}** element(s) report a bounding box that does not enclose '
            'their own solids — a family instance whose box is in its own '
            'coordinates while its geometry is out on the site. The ray '
            'test uses bounds derived from the solids, so these shade '
            'correctly; before that they were counted as shading and '
            'blocked nothing.'.format(disagreed))

    output.print_md(
        '_Rays are cast **{0:.0f} m** before giving up._'.format(
            reach * FT_TO_M))

    context = stats.get(u'other', {})
    if not context.get('shading'):
        output.print_md(
            ':warning: **No context is shading anything.** Every neighbour '
            'was dropped before the study, so the facade is being lit as '
            'though the site were empty. The columns above say which gate '
            'did it.')

    dropped = sum([per.get('no solids', 0) + per.get('no bounds', 0)
                   for per in stats.values()])
    if dropped:
        output.print_md(
            '_{0} element(s) could not be made into a blocker. They are '
            'still collected and still classified — they just cast no '
            'shadow._'.format(dropped))


def inputs_occlusion_probe(output, rows):
    """Does a blocker actually block? The sun is not involved.

    Reached only when the obstruction table already says the blockers are
    present, correctly bounded and in the right place — at which point
    the only remaining question is whether the intersection test reports
    anything on these particular solids.
    """
    output.print_md('## Does a blocker actually block?')
    output.print_md(
        'A ray fired straight through the middle of each blocker, on the '
        'X axis, ignoring the sun entirely. A closed solid **must** report '
        'a hit. One that does not cannot shade anything, however solid it '
        'looks in the model.')

    table = []
    errors = {}
    for (label, eid, size_m, box_hit, solid_hit, face_hit,
         volume, faces, mode, error) in rows:
        table.append([
            label or '?',
            eid if eid is not None else '-',
            '{0:.1f}'.format(size_m),
            'yes' if box_hit else 'NO',
            'yes' if solid_hit else 'NO',
            'yes' if face_hit else 'no',
            '{0:.1f}'.format(volume),
            faces,
            mode,
        ])
        if error:
            errors[error] = errors.get(error, 0) + 1
    output.print_table(
        table_data=table,
        columns=['use class', 'element', 'size m', 'box hit', 'solid hit',
                 'face hit', 'volume m3', 'faces', 'tested as'],
        title='Ray fired through the centre of each blocker')

    if errors:
        output.print_table(
            table_data=[[k, v] for k, v in sorted(errors.items(),
                                                  key=lambda kv: -kv[1])],
            columns=['exception from IntersectWithCurve', 'blockers'])
        output.print_md(
            'A thrown intersection test is indistinguishable from a clean '
            'miss unless it is caught and printed, which is why this row '
            'exists. If it is populated, the volume test is not returning '
            '"no" — it is failing.')

    rescued = [r for r in rows if not r[4] and r[5]]
    if rescued:
        output.print_md(
            '**{0} of {1}** blockers fail the volume test but DO cross a '
            'face. Those are now tested face-first, so they shade. '
            'Crossing a face needs no closed shell and is the question '
            'occlusion actually asks — is something in the way.'.format(
                len(rescued), len(rows)))

    missed = [r for r in rows if not r[4] and not r[5]]
    box_missed = [r for r in rows if not r[3]]

    if box_missed:
        output.print_md(
            ':warning: **{0}** blocker(s) failed even the BOUNDING BOX '
            'test on a ray through their own centre. That is arithmetic, '
            'not geometry — the bug is in `_ray_hits_box`.'.format(
                len(box_missed)))

    if missed and not box_missed:
        output.print_md(
            ':warning: **{0} of {1}** blockers cross NEITHER the volume nor '
            'any face on a ray straight through the middle, while passing '
            'the box test. Their geometry is not where their bounds say it '
            'is. Next step is `ReferenceIntersector`, which asks Revit '
            'rather than the geometry objects.'.format(
                len(missed), len(rows)))

    if not missed:
        output.print_md(
            '_Every blocker sampled reports a hit, one way or the other, '
            'so occlusion works on these solids. If shadows are still '
            'missing, the fault is in which rays get cast — orientation, '
            'sample height, or the self-shading skip — not in the '
            'blockers._')


def inputs_brief(output, result, shading=None, skipped_types=None):
    """The whole of "what was collected", in a handful of lines.

    `detailed` prints a storey table that runs to 47 rows on this model,
    plus faces-by-classification and a section stack. All of it is real,
    and none of it is what someone opens THIS report to find out - they
    want to know whether the cutter got its inputs. The Collect and
    Place Roofs buttons still print the long form.
    """
    _header(output, u'Tadpole')

    total = len(result.records)
    with_geo = len([r for r in result.records if r.has_geometry])
    derived = len(getattr(result, 'level_derived', None) or [])
    sections = len([s for s in result.sections if s.records])

    line = (u'**{0}** elements, **{1}** with geometry, **{2}** storeys, '
            u'**{3}** sections.').format(total, with_geo,
                                         len(result.storeys), sections)
    losses = []
    if result.no_geometry:
        losses.append(u'{0} without geometry'.format(len(result.no_geometry)))
    if result.no_level:
        losses.append(u'{0} without a Level'.format(len(result.no_level)))
    if result.errors:
        losses.append(u'{0} geometry errors'.format(len(result.errors)))
    if derived:
        losses.append(u'{0} levels inferred'.format(derived))
    if losses:
        line += u' ' + u', '.join(losses) + u'.'
    output.print_md(line)

    if shading:
        per = shading.get(u'other', {})
        output.print_md(
            u'Context: **{0}** of {1} shading, {2} tested face-first.'.format(
                per.get('shading', 0),
                sum([per.get(k, 0) for k in
                     ('shading', 'no solids', 'no geometry', 'no bounds')]),
                per.get('faces mode', 0)))

    if skipped_types:
        output.print_md(
            u'_Skipped **{0}** element(s) this tool placed on an earlier '
            u'run._'.format(sum(skipped_types.values())))


# ----------------------------------------------------------------------
# unique floors (stage 3-4), see tadpole.unique
# ----------------------------------------------------------------------

def _lengths(values):
    return u' + '.join([u'{0:.2f}'.format(v) for v in values]) or u'-'


def _section_key(value):
    """Numeric sections first and in numeric order, then text - `unique._section_key`, here so that the report reads
    no module of the rules (the Tadpole-split button's build has none)."""
    try:
        return (0, float(value), u'')
    except (TypeError, ValueError):
        return (1, 0.0, u'{0}'.format(value))


def unique_floors(output, result, by_building=None):
    """Per section: the three steps with every length, then the result.

    The lengths are the whole argument - a unique floor is only as
    trustworthy as the sums that separated it from the next one, so they
    are printed, not just the verdict.

    `by_building` groups the sections the way the apartment stage works
    through them: one building at a time, and within it the lowest unique
    floor first, carried upward.
    """
    output.print_md(u'# Unique floors')
    if not result:
        output.print_md(u'_No residential outer walls - nothing to group._')
        return
    summary = []
    order = sorted(result.keys(), key=_section_key)          # `unique.sorted_sections`
    if by_building:
        order = sorted(order, key=lambda s: (by_building.get(s, 9999),
                                             _section_key(s)))
    seen_building = None
    for section in order:
        if by_building:
            number = by_building.get(section)
            if number != seen_building:
                seen_building = number
                inside = [s for s in order if by_building.get(s) == number]
                output.print_md(u'# Building {0}: sections {1}'.format(
                    number, u', '.join(inside)))
        uniq = result[section]
        floors = []
        for u in uniq:
            floors.extend(u.floors)
        floors.sort(key=lambda f: (f.level_elev if f.level_elev is not None else 0.0))
        output.print_md(u'## Section {0}: {1} residential floors, {2} unique'.format(
            section, len(floors), len(uniq)))
        output.print_table(
            table_data=[[f.level_name, _lengths(f.outer_lengths), u'{0:.2f}'.format(f.outer_sum),
                         _lengths(f.dark_lengths), u'{0:.2f}'.format(f.dark_sum)] for f in floors],
            columns=[u'floor', u'outer walls, m', u'outer sum', u'non-insolated, m', u'non-insolated sum'],
            title=u'Steps 1-2: lengths per floor')
        output.print_table(
            table_data=[[u.name, u'{0:.2f}'.format(u.outer_sum), u'{0:.2f}'.format(u.dark_sum),
                         len(u.floors), u', '.join(u.levels)] for u in uniq],
            columns=[u'unique floor', u'outer sum, m', u'non-insolated sum, m', u'floors', u'levels'],
            title=u'Step 3: sets by outer sum, split by non-insolated sum')
        summary.append([(by_building or {}).get(section, u'-'), section,
                        len(floors), len(uniq), u', '.join(
                            [u'{0} x{1}'.format(u.name, len(u.floors)) for u in uniq])])
    output.print_table(table_data=summary,
                       columns=[u'building', u'section', u'floors', u'unique', u'types'],
                       title=u'All sections, in the order the cutter takes them')

# ----------------------------------------------------------------------
# stair cores, see tadpole.stairs
# ----------------------------------------------------------------------

def stairs(output, plans, placed=None, failed=None, aborted=None, roof_type=None):
    """One row per section - the rule that decided it and how many plates -
    then what was actually built.

    The rule text carries the arithmetic (lengths, module counts, which end
    the bigger run starts from), because a stair position is only as
    trustworthy as the numbers that produced it.
    """
    output.print_md(u'# Stair cores')
    if not plans:
        output.print_md(u'_No section had a side to stand a stair on._')
        return
    per_section = {}
    for item in plans:
        row = per_section.setdefault(item.section, {'plates': 0, 'ok': 0, 'rule': item.rule})
        row['plates'] += 1
        if item.ok:
            row['ok'] += 1
    rows = []
    for section in sorted(per_section, key=lambda s: _section_sort(s)):
        row = per_section[section]
        rows.append([section, row['plates'], row['ok'], row['rule']])
    output.print_table(
        table_data=rows,
        columns=[u'section', u'floors', u'placed', u'rule and the numbers behind it'],
        title=u'One stair per section, the same position on every floor'
              + (u' - type "{0}"'.format(roof_type) if roof_type else u''))
    if placed is not None:
        output.print_md(u'**{0}** of **{1}** plates placed.'.format(placed, len(plans)))
    if failed:
        output.print_table(
            table_data=[[reason, count] for reason, count in sorted(
                failed.items(), key=lambda kv: -kv[1])],
            columns=[u'why a stair failed', u'count'])
    if aborted:
        output.print_md(u':warning: {0}'.format(aborted))
    l_stairs(output, plans)


def l_stairs(output, plans):
    """What the rules for the stair of an L-SHAPED section SAW, one row per
    section: the dark metres on each long face, the modules of each short
    face and what it hands the core, the core's sides and area - so that a
    placement can be judged against the numbers, not against a screenshot
    (user, 2026-10-06)."""
    seen = {}
    for item in plans:
        info = getattr(item, 'l_info', None)
        if info and item.section not in seen:
            seen[item.section] = info
    if not seen:
        return
    rows = []
    for section in sorted(seen, key=lambda s: _section_sort(s)):
        info = seen[section]
        variant = info.get('variant')
        dark = u' / '.join(u'{0:.1f} of {1:.1f}'.format(d, l) for d, l in zip(
            info.get('dark_m', []), info.get('long_m', []))) or u'-'
        short = u'-'
        node = u'-'
        if variant == 2 and info.get('node'):
            n = info['node']
            short = u' / '.join(
                u'{0:.2f} m = {1} mod{2}'.format(
                    f['length_ft'] * 0.3048, f['modules'],
                    u' (carries the core{0})'.format(
                        u', all the way' if f['take_ft'] >= f['length_ft'] - 1e-6
                        else u'') if i == n['face'] else u'')
                for i, f in enumerate(n['faces']))
            hrs = info.get('face_hours') or []
            # SAY WHICH RULE PICKED THE FACE (user, 2026-10-15, sections 52/54: "on the darker face" was printed for a
            # face the forced core took for being the SHORTER one, 54's lighter one)
            if n.get('gentle_pick'):
                pick = u' - a gentle bend: the {0} half of the inner facade carries it'.format(n['gentle_pick'])
            elif n.get('forced_short'):
                pick = u' - put to the bend from the dark long face: the shorter carries it'
            elif n.get('preferred'):
                pick = u' - the darker carries it'
            else:
                pick = u' - alike, the shorter carries it'
            node = (u'{0:.1f} m2, its wall on the face, {1:.2f} m from the corner '
                    u'to the end of the wing{2}').format(
                n['area_ft2'] * 0.09290304,
                n['faces'][n['face']]['take_ft'] * 0.3048,
                (u'; sun hours of the faces {0}, {1}'.format(
                    u'-' if hrs[0] is None else u'%.2f' % hrs[0],
                    u'-' if hrs[1] is None else u'%.2f' % hrs[1])
                 if len(hrs) == 2 and None not in hrs else u'') + pick)
        elif variant == 1:
            short = u' / '.join(u'{0:.2f} m'.format(v) for v in info.get('short_m', []))
            node = u'5.5 x 6.47 m, flush against the dark long face'
            if info.get('gentle'):
                node = u'5.5 x 6.47 m, flush on the outer facade at its break'
        label = {1: u'1: a long face is dark', 2: u'2: at the bend'}.get(
            variant, u'{0}'.format(variant))
        if info.get('gentle'):
            g = info['gentle']
            label = (u'gentle bend {0:.0f} deg: the {1} facade is the darker ({2:.1f} m inner / {3:.1f} m outer, '
                     u'dark over the floors)').format(g['angle'], g['facade'], g['inner_m'], g['outer_m'])
        rows.append([section, info.get('level', u'-'), label, dark, short, node])
    output.print_md(u'## L-shaped sections: what the stair rules saw')
    output.print_table(
        table_data=rows,
        columns=[u'section', u'read on', u'rule',
                 u'dark m of each long face (summed over floors) of its length',
                 u'short faces at the corner', u'the core'])


def _section_sort(value):
    try:
        return (0, float(value), u'')
    except (TypeError, ValueError):
        return (1, 0.0, u'{0}'.format(value))

def _ring_fault(ring):
    """A short diagnosis of why Revit would refuse this ring as a
    footprint - a crossing, a repeated corner, or a sliver edge - or
    'no fault found' when none of the three obvious ones apply and the
    footprint is bad for some other reason (not planar, a curve shorter
    than Revit's own tolerance after unit conversion, and so on)."""
    n = len(ring)
    if n < 3:
        return u'only {0} corner(s)'.format(n)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def segments_cross(p1, p2, p3, p4):
        d1, d2 = cross(p3, p4, p1), cross(p3, p4, p2)
        d3, d4 = cross(p1, p2, p3), cross(p1, p2, p4)
        if ((d1 > 1e-9) != (d2 > 1e-9)) and ((d3 > 1e-9) != (d4 > 1e-9)) \
                and min(abs(d1), abs(d2), abs(d3), abs(d4)) > 1e-9:
            return True
        if abs(d1) < 1e-9 and abs(d2) < 1e-9:
            for a, b, c in ((p1, p2, p3), (p1, p2, p4)):
                t = ((c[0] - a[0]) * (b[0] - a[0]) + (c[1] - a[1]) * (b[1] - a[1]))
                span = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
                if span > 1e-12 and 1e-9 < t < span - 1e-9:
                    return True
        return False

    for i in range(n):
        for j in range(i + 1, n):
            if j == i or (i == 0 and j == n - 1) or j == i + 1:
                continue
            if segments_cross(ring[i], ring[(i + 1) % n],
                              ring[j], ring[(j + 1) % n]):
                return u'edge {0}-{1} crosses edge {2}-{3}'.format(i, i + 1, j, j + 1)
    for i in range(n):
        for j in range(i + 1, n):
            if abs(ring[i][0] - ring[j][0]) < 0.01 \
                    and abs(ring[i][1] - ring[j][1]) < 0.01:
                return u'corner {0} repeats corner {1}'.format(j, i)
    for i in range(n):
        p, q = ring[i], ring[(i + 1) % n]
        length = ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5
        if length < 0.02:
            return u'edge {0}-{1} is only {2:.3f} m long'.format(i, i + 1, length)
    return u'no crossing, repeat or sliver found - the corners alone do ' \
           u'not say why'


def mix_pass(output, result):
    """What the mix of the file (`mix`) changed, and the shares before and after, against the settings window."""
    if not result:
        return
    brief = result.get('brief') or {}

    def shares(counts):
        total = float(sum(counts.values())) or 1.0
        return [u'{0:.1f}%'.format(100.0 * counts[k] / total) for k in (1, 2, 3)]
    before, after = result['before'], result['after']
    rows = []
    for k, b, a in zip((1, 2, 3), shares(before), shares(after)):
        rows.append([u'{0}k'.format(k), before[k], b, after[k], a, u'{0:.0f}%'.format(100.0 * brief.get(k, 0.0))])
    if result.get('max_1k'):
        # THE MOST 1K: no shares to reach - as many 1k as the rules allow
        title = u'The mix of the file - THE MOST 1K (the settings window\'s switch; no shares kept to)'
    else:
        title = u'The mix of the file fitted to the settings window (within {0:.0f} points) - {1}'.format(
            result.get('tolerance', 2.0), u'REACHED' if result.get('reached') else u'not reached')
    output.print_table(
        table_data=rows,
        columns=[u'type', u'flats before', u'share before', u'flats after', u'share after', u'settings window'],
        title=title)
    output.print_md(u'_Stopped: {0}; {1} change(s) in {2:.0f} s of {3:.0f}._'.format(
        result.get('why') or u'-', len(result.get('moves') or []), result.get('seconds', 0.0),
        result.get('budget', 0.0)))
    if result.get('moves'):
        output.print_table(
            table_data=[[m['section'], m['level'] + (u' (+{0} level(s))'.format(len(m['levels']) - 1)
                                                     if len(m['levels']) > 1 else u''),
                         u'working floor (first group)' if m['working'] else u'its own group',
                         m['was'], m['now'], u'{0:.1f}'.format(m['mix'][0])]
                        for m in result['moves']],
            columns=[u'section', u'floor', u'where', u'was, m2', u'now, m2', u'off the window after, points'],
            title=u'Changes made for the mix of the file')
    if result.get('refused'):
        output.print_table(
            table_data=[[m['section'], m['level'], m['was'], m['now'], m['why']] for m in result['refused']],
            columns=[u'section', u'working floor', u'was, m2', u'would be, m2', u'why not'],
            title=u'Changes of a working floor refused: a floor laid on it would get a flat beyond its band '
                  u'(over 2 m2 for a 1k, 3.5 for a 2k, 5 for a 3k)')


def mix_of_the_file(output, whole):
    """The share of 1k / 2k / 3k over every flat of every floor of the file, against the settings window."""
    from tadpole.config import APARTMENT_MIX
    counts = whole['counts']
    total = float(sum(counts.values()))
    if total <= 0:
        return
    rows = []
    for rooms in (1, 2, 3):
        want = APARTMENT_MIX.get(rooms, 0.0)
        rows.append([u'{0}k'.format(rooms), counts[rooms],
                     u'{0:.1f}%'.format(100.0 * counts[rooms] / total),
                     u'{0:.0f}%'.format(100.0 * want),
                     u'{0:+.0f}'.format(counts[rooms] - want * total)])
    output.print_table(
        table_data=rows,
        columns=[u'type', u'flats on all floors', u'share', u'brief (settings window)',
                 u'flats over (+) / short (-) of the brief'],
        title=u'The mix of the whole file - {0} flats on {1} floors, {2} dead spaces'.format(
            int(total), whole['floors'], whole['dead']))
    output.print_table(
        table_data=[[s, c[1], c[2], c[3], n] for s, c, n in whole['sections']],
        columns=[u'section', u'1k', u'2k', u'3k', u'floors counted'],
        title=u'... by section')
    if whole.get('unread'):
        output.print_md(u'_Floors whose other-floor changes could not be read, counted as the working '
                        u'floor: {0}_'.format(u', '.join(u'{0} {1}'.format(s, l) for s, l in whole['unread'])))


def apartments(output, plans, placed=None, failed=None, aborted=None,
               moves=None, whole=None):
    """The flats of every working floor: what each came out as, how it was
    arrived at, and how the mix compares with the brief."""
    from tadpole.config import (APARTMENT_BANDS, APARTMENT_MIX,
                                APARTMENT_BAND_EPS)
    output.print_md(u'# Apartments')
    if moves:
        told = [m for m in moves if m[2]]
        if told:
            output.print_table(
                table_data=[[m[0], u'{0:.0f} mm'.format(m[1]) if m[1] else u'-', m[2]]
                            for m in told],
                columns=[u'section', u'stair moved', u'why'],
                title=u'Cores slid to keep an L-shaped flat inside its band')
    if not plans:
        output.print_md(u'_No floor was laid out._')
        return
    live = [p for p in plans if not p.dead and p.points]
    dead = [p for p in plans if p.dead and not getattr(p, 'skipped', False)]
    left_out = [p for p in plans if getattr(p, 'skipped', False)]
    if left_out:
        output.print_table(
            table_data=[[p.section, p.level_name, p.rule] for p in left_out],
            columns=[u'section', u'floor', u'why it was left out'],
            title=u'Sections the apartment rules do not cover yet')
    rows = []
    for item in plans:
        if getattr(item, 'skipped', False):
            continue
        rows.append([item.section, item.level_name,
                     u'dead space' if item.dead else u'{0}k'.format(item.rooms),
                     u'{0:.1f}'.format(item.raw_m2),
                     u'-' if item.dead else u'{0:.1f}'.format(item.area_m2),
                     u'' if item.dead else (u'ok' if item.in_band else u'OUT'),
                     item.sunny, u'yes' if item.ok else u'-', item.rule])
    output.print_table(
        table_data=rows,
        columns=[u'section', u'floor', u'type', u'raw m2', u'flat m2', u'band',
                 u'sunny windows', u'placed', u'how it was arrived at'],
        title=u'One working floor per section - the largest, lowest of equals')
    if live:
        inside = [p for p in live if p.in_band]
        output.print_md(
            u'**{0}** flats and **{1}** dead spaces; **{2}** of the flats sit '
            u'inside their band ({3:.0f}%), with {4:.1f} m2 of slack '
            u'allowed.'.format(len(live), len(dead), len(inside),
                               100.0 * len(inside) / len(live), APARTMENT_BAND_EPS))
        mix = []
        for rooms in sorted(APARTMENT_BANDS):
            mine = [p for p in live if p.rooms == rooms]
            if not mine:
                continue
            mix.append([u'{0}k'.format(rooms), len(mine),
                        u'{0:.1f}%'.format(100.0 * len(mine) / len(live)),
                        u'{0:.0f}%'.format(100.0 * APARTMENT_MIX.get(rooms, 0)),
                        u'{0:.1f} - {1:.1f}'.format(min(p.area_m2 for p in mine),
                                                    max(p.area_m2 for p in mine)),
                        u'{0:.0f} - {1:.0f}'.format(*APARTMENT_BANDS[rooms]),
                        len([p for p in mine if not p.in_band])])
        output.print_table(
            table_data=mix,
            columns=[u'type', u'count', u'share', u'brief', u'areas, m2',
                     u'band, m2', u'out of band'],
            title=u'The mix against the brief (the working floors only)')
        if whole:
            mix_of_the_file(output, whole)
    if placed is not None:
        output.print_md(u'**{0}** of **{1}** plates placed.'.format(
            placed, len([p for p in plans if p.points])))
    if failed:
        output.print_table(
            table_data=[[reason, count] for reason, count in sorted(
                failed.items(), key=lambda kv: -kv[1])],
            columns=[u'why a flat failed', u'count'])
        # THE COUNT SAYS HOW MANY; THIS SAYS WHICH ONES. "Invalid
        # footprint: 9" sends the search through every section with a
        # guess; the section, the floor and the rule each one was cut by
        # - still on the object, `place_apartments` never throws it away
        # - send it straight to the one ring that broke.
        broken = [p for p in plans if getattr(p, 'reason', None)
                  and not getattr(p, 'skipped', False)]
        if broken:
            output.print_table(
                table_data=[[p.section, p.level_name,
                            u'dead space' if p.dead else u'{0}k'.format(p.rooms),
                            u'{0:.1f}'.format(p.raw_m2), p.rule[:70], p.reason]
                           for p in broken],
                columns=[u'section', u'floor', u'type', u'raw m2',
                         u'how it was cut', u'what Revit said'],
                title=u'Which flats failed, and why')
            # REVIT'S OWN MESSAGE FOR A BAD FOOTPRINT IS ALWAYS THE SAME
            # SENTENCE, whatever actually broke - a self-crossing ring, a
            # repeated corner, an edge shorter than its curve tolerance.
            # It says nothing a second attempt would say differently. The
            # corners themselves are not thrown away either, so this
            # checks them the same way the offline stands do - crossing
            # edges, repeated corners, slivers - and prints what it
            # finds, in metres, so a broken ring is read off the report
            # instead of guessed at from a section number.
            output.print_md(u'**The corners themselves, and what is wrong '
                            u'with them:**')
            for p in broken:
                ring = [(x * 0.3048, y * 0.3048) for x, y in p.points]
                why = _ring_fault(ring)
                output.print_md(u'- section {0}, {1}, {2:.1f} m2 - {3} - {4}'
                                .format(p.section, p.level_name, p.raw_m2,
                                       why, u'  '.join(
                                           u'({0:.2f}, {1:.2f})'.format(x, y)
                                           for x, y in ring)))
    if aborted:
        output.print_md(u':warning: {0}'.format(aborted))


def corridors(output, plans, placed=None, failed=None, aborted=None, roof_type=None):
    """One row per floor: how long the corridor is, its two wings, and what
    shortened it - the cap at 12 m, the edge of the plate, or nothing."""
    output.print_md(u'# Corridors')
    if not plans:
        output.print_md(u'_No floor had a stair to run a corridor off._')
        return
    rows = []
    for item in plans:
        rows.append([item.section, item.level_name,
                     u'yes' if item.ok else u'-', item.rule])
    output.print_table(
        table_data=rows,
        columns=[u'section', u'floor', u'placed', u'length, wings and what trimmed it'],
        title=u'Central band, joined to the stair'
              + (u' - type "{0}"'.format(roof_type) if roof_type else u''))
    if placed is not None:
        output.print_md(u'**{0}** of **{1}** corridors placed.'.format(placed, len(plans)))
    if failed:
        output.print_table(
            table_data=[[reason, count] for reason, count in sorted(
                failed.items(), key=lambda kv: -kv[1])],
            columns=[u'why a corridor failed', u'count'])
    if aborted:
        output.print_md(u':warning: {0}'.format(aborted))