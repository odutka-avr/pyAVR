# -*- coding: utf-8 -*-
"""Tadpole-split — the Tadpole button with the layout on a server (the kitchen).

The same run as the Tadpole button, one difference: the stair cores, corridors, flats, the mix and the changes of
the other unique floors are decided on the server (kitchen/, built from lib/tadpole by tools/build_split.py), not
here. This button collects, studies the sun, sends the order (`tadpole.order`) through the waiter
(`tadpole.waiter`; where the kitchen is: %APPDATA%/Tadpole/kitchen.txt), and places what comes back
(`tadpole.placing`). After O. Tarnavskyi's backend split (2026-10-20), on the Tadpole button of 2026-10-21.

What follows is the Tadpole button's own description.

Tadpole — input collection.

Assembles, in one run, every input the apartment cutter needs, and says
plainly where each one came from.

Two of them are written into the model, because a later stage reads them
back out of the document:

    commercial and parking roofs   the plates the cutter must not divide
    non-insolated walls            the facade it must not put a room on

Both go in without asking. Placing is what the button is for, and the
whole run is a single Ctrl+Z.

Section plates are deliberately NOT roofed. They are the thing the cutter
divides; roofing them now would pre-empt it.

Everything else is collected as a variable and reported — outer and inner
wall runs, the day/dark split, sun hours per run, floor plates, the raw
horizontal faces, the top storey of each section. The nine settings
values come from the window that opens the run. The report names what
still has NO producer, which is now `Valid inner` alone.

Every section, always: a per-section run would hand the cutter an input
set that silently describes only part of the building.

ONE PROGRESS BAR covers the whole run, weighted by phase. The sun study
is most of the wall-clock time, so it gets most of the bar; the rest
would otherwise flicker past and then appear to hang. Cancelling is
honoured at every phase boundary and inside every long loop.

MODIFIES THE MODEL. Both writes sit in one TransactionGroup, so a single
Ctrl+Z reverses the whole run.
"""

__title__ = 'Tadpole-split'
__author__ = 'Roksolana Kordyiaka'
# THE ENGINE STAYS: the window that offers the core on the other side stays open after the run, and its button lays
# the model again through an ExternalEvent (`tadpole.rerun`) - both need this engine alive after the script ends.
# pyRevit reads it from bundle.yaml (engine: persistent: true); this line alone was not taken (2026-10-16 crash)
__persistentengine__ = True


def _fresh_modules():
    """THE PERSISTENT ENGINE KEEPS THE OLD CODE (user, 2026-10-21, section 69 "у ревіт не спрацювало"): the tadpole
    modules imported once stay in sys.modules for as long as Revit runs, and a new version copied in is never read.
    When any lib/tadpole file is newer than the ones loaded, they are all dropped and imported afresh - unless the
    window of the last run is laying sections again (its state lives in `tadpole.rerun`)."""
    import os
    import sys
    try:
        lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), u'..', u'..', u'lib', u'tadpole')
        stamp = max(os.path.getmtime(os.path.join(lib, n)) for n in os.listdir(lib) if n.endswith(u'.py'))
    except Exception:
        return
    old = getattr(sys, '_tadpole_stamp', None)
    if 'tadpole' not in sys.modules:
        sys._tadpole_stamp = stamp          # the first run in this Revit: nothing loaded yet
        return
    if old is not None and stamp <= old:
        return
    rerun = sys.modules.get('tadpole.rerun')
    if rerun is not None and getattr(rerun, 'AGAIN', None) is not None:
        return
    sys._tadpole_stamp = stamp
    for name in [n for n in list(sys.modules) if n == 'tadpole' or n.startswith('tadpole.')]:
        del sys.modules[name]


_fresh_modules()

from pyrevit import script, revit, forms

from tadpole import (
    collect, report, roofs, build, sun, tx, ui, inputs, settings_ui,
    splash, groups, rerun, placing, waiter, wire, config,
)
from tadpole import order as orders
from tadpole.config import (
    SUN_DARK_WALL_TYPE, SUN_LATITUDE_FALLBACK, BUILDING_CLASSES,
    INPUT_ROOF_CLASSES, INPUT_SUN_CLASSES, SUN_CONTEXT_REACH_MM,
    STAIR_ROOF_TYPES, CORRIDOR_ROOF_TYPES, APARTMENT_ROOF_TYPES,
)


doc = revit.doc
import os as _os
import time as _time
run_started = _time.time()          # the run's time, for the summary sent to the chat (`tadpole.chat`)
# SECTIONS LAID AGAIN from the window at the end of the last run (`tadpole.rerun`): its settings, its sections with
# the core across, the sections to lay again (`only` - the rest of the model is not touched), the first run's inputs
# and the report window it printed into - the new report goes on below the old one
again = rerun.take()
point = bool(again and again.get('base') and again.get('only'))
base = again['base'] if point else None
only = set(again['only']) if point else None
# SECTIONS LAID AGAIN ON THE MODEL'S GEOMETRY NOW (user, 2026-10-16: "якщо архітектор ... схоче змінити трошки плиту
# секції в посадці, і перегенерити результат"): such a run collects and studies the sun again instead of taking the
# first run's inputs - and writes, as any point run, only its sections
fresh = set(again.get('fresh') or ()) if point else set()
if again and again.get('output') is not None:
    output = again['output']
else:
    output = script.get_output()
    output.close_others(all_open_outputs=True)
# the report in the company's style (AVR Design System) - before anything is printed
report.style(output)

def quit_run():
    """Leave, taking the card down. Every early exit goes through here."""
    splash.stop()
    script.exit()


# Relative, not percentages — they are normalised, so a weight can be
# re-tuned without having to make the column add up again. These are set
# from how the run actually spends its time on the current model, where
# the sun study is minutes and everything else is seconds.
PHASES = [
    ('collect',  u'Collecting elements',            12),
    ('types',    u'Resolving types',                 3),
    ('probe',    u'Testing roof placement',          3),
    ('walls',    u'Wall runs',                       8),
    ('plates',   u'Floor plates',                   10),
    ('sun',      u'Sun study',                      53),
    ('slides',   u'Trying the cores in other places', 20),
    ('mix',      u'Fitting the mix of the file',     15),
    ('roofs',    u'Placing roofs',                   6),
    ('darkwall', u'Placing non-insolated walls',     7),
    ('stairs',   u'Placing stair cores',             4),
    ('corridor', u'Placing corridors',               4),
    ('apartments', u'Laying out the flats',          6),
    ('group',    u'Grouping floors',                 2),
    ('copy',     u'Copying groups to sibling floors', 2),
    ('next',     u'Working the other unique floors', 4),
]

# TEST SCAFFOLDING. On, this places EVERY indexed outer run, every dark
# stretch and every floor plate, each stamped in Comments with its own
# record - so the indexing can be read off the model instead of taken on
# trust. It is a lot of elements and it is not what the button is for.
#
# It REPLACES the normal non-insolated wall pass rather than adding to
# it, because that pass places the dark stretches too and two walls on
# one stretch would be indistinguishable in the model.
PLACE_TEST_GEOMETRY = False

if PLACE_TEST_GEOMETRY:
    PHASES = [p for p in PHASES if p[0] != 'darkwall']
    PHASES.append(('test', u'Placing test geometry', 20))

# Every section, always. The filtering plumbing below is left intact, so
# restoring a picker is a matter of setting this from ui.choose_sections.
sections = None


def place_test_geometry(doc, outer_walls, inner_walls, darkloft, plates,
                        types, dark_type, progress=None):
    """Place everything that was indexed, stamped with its record.

    Places the runs `inputs.wall_runs` actually returned, NOT a fresh
    classification. Re-deriving them here would place walls that were
    never indexed and prove nothing about the indexes - the point is to
    put the exact objects that carry `(parent, ordinal)` into the model
    where they can be selected and read.

    Comments carries the record verbatim:

        outer run       {'index': (1, 0), 'hours': 4.53}
        inner run       {'index': None}
        dark stretch    {'index': (1, 0)}
        floor plate     {'position': 'top'}

    A face cut outer / inner / outer puts three walls in the model:
    `(1, 0)` and `(1, 1)` in the outer type, and between them one in the
    INNER type reading `{'index': None}`. Those three, in that order
    along one line, are the whole thing being checked.

    Inner runs are placed in their own type rather than left out. With
    the party-wall rule now a 1 m proximity band instead of coincidence,
    a missing wall is not evidence of anything - it could equally be a
    run that failed to build. A wall of the wrong type is unambiguous.

    Same shape as every other write here: one Transaction, a
    SubTransaction each so a rejection rolls back only itself.
    """
    counts = {'walls': 0, 'inner': 0, 'dark': 0, 'floors': 0}
    failed = {}
    total = (len(outer_walls) + len(inner_walls) + len(darkloft)
             + len(plates))
    seen = 0

    def note(exc):
        reason = u'{0}'.format(exc)[:120]
        failed[reason] = failed.get(reason, 0) + 1

    transaction = tx.start(doc, 'Tadpole - test geometry')
    try:
        for run in outer_walls:
            if progress is not None and progress(seen, total):
                break
            seen += 1
            wall_type = types['walls'].get((run.record.use_class, 'outer'))
            level = doc.GetElement(run.record.level_id)
            if wall_type is None or level is None:
                note('no wall type or no level')
                continue
            with tx.Attempt(doc) as attempt:
                attempt.result = build.make_wall_run(
                    doc, run.seg, run.t0, run.t1, wall_type, level,
                    run.record.level_elev)
                build.stamp_comment(attempt.result, run.comment)
            if attempt.ok:
                counts['walls'] += 1
            else:
                note(attempt.error)

        for run in inner_walls:
            if progress is not None and progress(seen, total):
                break
            seen += 1
            wall_type = types['walls'].get((run.record.use_class, 'inner'))
            level = doc.GetElement(run.record.level_id)
            if wall_type is None or level is None:
                note('no inner wall type or no level')
                continue
            with tx.Attempt(doc) as attempt:
                attempt.result = build.make_wall_run(
                    doc, run.seg, run.t0, run.t1, wall_type, level,
                    run.record.level_elev)
                build.stamp_comment(attempt.result, run.comment)
            if attempt.ok:
                counts['inner'] += 1
            else:
                note(attempt.error)

        for stretch in darkloft:
            if progress is not None and progress(seen, total):
                break
            seen += 1
            level = doc.GetElement(stretch.run.record.level_id)
            if dark_type is None or level is None:
                note('no dark wall type or no level')
                continue
            with tx.Attempt(doc) as attempt:
                attempt.result = build.make_wall_run(
                    doc, stretch.run.seg, stretch.a, stretch.b,
                    dark_type, level, stretch.run.record.level_elev)
                build.stamp_comment(attempt.result, stretch.comment)
            if attempt.ok:
                counts['dark'] += 1
            else:
                note(attempt.error)

        for part in plates:
            if progress is not None and progress(seen, total):
                break
            seen += 1
            floor_type = types['floors'].get(
                (part.record.use_class, part.position))
            level = doc.GetElement(part.record.level_id)
            if floor_type is None or level is None:
                note('no floor type or no level')
                continue
            with tx.Attempt(doc) as attempt:
                attempt.result = build.make_floor_part(
                    doc, part, floor_type, level, part.record.level_elev)
                build.stamp_comment(
                    attempt.result,
                    inputs.dict_text([(u'position',
                                       u"'{0}'".format(part.position))]))
            if attempt.ok:
                counts['floors'] += 1
            else:
                note(attempt.error)

        tx.finish(transaction)
    except Exception:
        tx.abort(transaction)
        raise

    return counts, failed


def cancelled(bar):
    """Stop cleanly, before anything has been written. The x on the "Work in Frogress" card asks for it too."""
    if not bar.cancelled and not splash.cancel_requested:
        return False
    output.print_md('## Cancelled')
    output.print_md('_Stopped before anything was written to the model._')
    return True


# ======================================================================
# The one question, asked BEFORE the bar opens
#
# It needs nothing but the document, so there is no reason to make
# someone watch a bar start and then stop to answer it.
# ======================================================================

latitude, is_default = sun.site_latitude(doc)
north = sun.true_north_angle(doc)

if again:
    latitude = again.get('latitude', latitude)
elif is_default:
    choice = forms.alert(
        'The site is still at Revit\'s Boston default '
        '({0:.2f} deg).\n\nThe sun study needs a real latitude.'.format(
            latitude),
        title='Tadpole',
        options=['Use Lviv (49.84)', 'Keep Boston', 'Cancel'])
    if choice is None or choice == 'Cancel':
        quit_run()
    if choice.startswith('Use Lviv'):
        latitude = SUN_LATITUDE_FALLBACK

vectors = sun.sun_vectors(latitude, north_angle=north)
if not vectors:
    forms.alert('No sun vectors — the sun never rises for that latitude '
                'and date.', title='Tadpole', warn_icon=True)
    quit_run()

# The settings window, before the bar opens. Its values live for this
# run only - nothing is written anywhere - and they are printed with the
# result, so the report cannot drift from what produced it.
_first_look = collect.run(doc, with_geometry=False)
section_keys = [k for k in _first_look.section_keys() if k != collect.NO_SECTION]
# EVERY SLAB OF THE HOUSING NEEDS ITS SECTION NUMBER (user, 2026-10-20, Str200 V1: 43 slabs, none numbered - the run
# took them for one section and fell over in the middle with a NullReference). Said at once, before the sun study.
_unnumbered = _first_look.unnumbered()
# a run again from the window after the run: the first run went on past this window, so it goes on the same way
skip_unnumbered = bool(again and _unnumbered)
if _unnumbered and not again:
    # "треба щоб видавало помилку «бракує номера секцій», і щоб можна було виділити і подивитися що за блоки і щось
    # змінити, або проігнорити і натиснути «далі»" (user, 2026-10-20)
    _choice = forms.alert(
        u'Бракує номерів секцій: {0} плит(и) житла без «AVR_Номер Секції»{1}.'.format(
            len(_unnumbered), u'' if section_keys else u' (не пронумеровано жодної)'),
        sub_msg=u'Tadpole розкладає квартири посекційно (одна вертикальна стопка плит = одна секція).\n'
                u'«Виділити» - позначити ці плити в моделі й зупинитися, щоб заповнити номери.\n'
                u'«Далі» - продовжити без них: ці плити не розкладаються.',
        title='Tadpole', warn_icon=True,
        options=[u'Виділити', u'Далі', u'Скасувати'])
    if _choice == u'Виділити':
        try:
            from System.Collections.Generic import List
            from Autodesk.Revit.DB import ElementId
            revit.uidoc.Selection.SetElementIds(List[ElementId]([r.id for r in _unnumbered]))
        except Exception as _error:
            forms.alert(u'Не вдалося виділити: {0}'.format(_error), title='Tadpole')
        quit_run()
    if _choice != u'Далі':
        quit_run()
    if not section_keys:
        forms.alert(u'Без цих плит розкладати нічого - жодна плита житла не має номера секції.',
                    title='Tadpole', warn_icon=True)
        quit_run()
    skip_unnumbered = True
# A SLAB MODELLED TWICE (user, 2026-10-20, Str200 V1 section 1, floor 04: two slabs one on the other - its facade
# counted twice, the working floor laid on 16 modules where there were 8, the floors above relaid as if they had lost
# half their facade). "якщо на посадці є дубль, треба щоб теж з'являлось віконце з попередженням його прибрати".
_duplicates = _first_look.duplicates()
skip_duplicates = bool(again and _duplicates)
if _duplicates and not again:
    _where = sorted(set(u'секція {0}, {1}'.format(a.section, a.level_name or u'?') for a, _b in _duplicates))
    _choice = forms.alert(
        u'Дублі плит: {0} плит(и) житла лежать на іншій плиті тієї ж секції й поверху.'.format(len(_duplicates)),
        sub_msg=u'{0}.\n'
                u'Фасад такого поверху рахується двічі, і секція розкладається хибно - дубль треба прибрати.\n'
                u'«Виділити» - позначити зайві плити в моделі й зупинитися, щоб їх видалити.\n'
                u'«Далі» - продовжити, не зважаючи на зайві плити (по одній на поверх).'.format(
                    u'; '.join(_where[:8]) + (u'…' if len(_where) > 8 else u'')),
        title='Tadpole', warn_icon=True,
        options=[u'Виділити', u'Далі', u'Скасувати'])
    if _choice == u'Виділити':
        try:
            from System.Collections.Generic import List
            from Autodesk.Revit.DB import ElementId
            revit.uidoc.Selection.SetElementIds(List[ElementId]([b.id for _a, b in _duplicates]))
        except Exception as _error:
            forms.alert(u'Не вдалося виділити: {0}'.format(_error), title='Tadpole')
        quit_run()
    if _choice != u'Далі':
        quit_run()
    skip_duplicates = True
setting_values = again['values'] if again else settings_ui.ask(section_keys)
if setting_values is None:
    quit_run()
# every element this run creates has an id above this one: the window at the end may remove the run by it
run_start_id = base['start_id'] if point else rerun.max_id(doc)
# THE BANDS COME FROM THAT WINDOW, not from config.py. They decide how
# many rooms a flat is called and which split wins, so measuring against
# one set of numbers while the person typed another is not a reporting
# quirk - it is a different layout. The kitchen takes them with the order,
# and the dish brings back what it took, for the report (below).

# The card goes up HERE - the moment the last dialog is answered and the
# run actually starts working. Before that there is nothing to wait for:
# the window is waiting for the person, not the other way round.
splash.start()
if not splash.active():
    output.print_md('_No "Work in Frogress" card: {0}_'.format(splash.status()))

# The threshold the window was given. Everything downstream is judged
# against THIS, not against the config constant, so the number the
# report prints is the number that produced the result.
required_hours = float(setting_values[u'insolation'])


placements = []
roof_aborted = None
walls_placed = 0
walls_failed = {}
walls_aborted = None
test_counts = {}
test_failed = {}
stair_plans = []
stair_type_name = None
stairs_placed = 0
stairs_failed = {}
stairs_aborted = None
corridor_plans = []
corridor_type_name = None
section_building = {}
corridors_placed = 0
corridors_failed = {}
corridors_aborted = None
cutout_log = []

apartment_plans = []
apartments_placed = 0
apartments_failed = {}
apartments_aborted = None
stair_moves = []
module_changes = []
groups_made = []
groups_failed = []
group_objects = {}
copies_made = []
copies_failed = []
next_made = []
next_failed = []
arms_done = []
arm_reviews = []
big_done = []
dead_reviews_log = []
dead_done = []
fit_log = []
fit_done = []
blind_log = []
blind_done = []
l_log = []
l_done = []
mix_done = []
floor_outcomes = []
flip_offers, flip_chosen = [], set()     # the cores offered across; the window at the end reads them
run_numbers, band_now = None, {}         # the run in numbers - the window's first card and the chat
all_sections = []                        # every section of the file - the window's 02 laid again
section_plates = {}                      # section -> its largest plate, the window's 03 plan


with ui.Progress(PHASES) as bar:

    # ------------------------------------------------------------------
    # 1. collect
    # ------------------------------------------------------------------
    # THE FIRST RUN'S INPUTS: a point run (sections laid again from the window at the end) neither collects nor
    # studies the sun again - the model now holds the other sections' flats, and the inputs are the ones the
    # layout on the screen was made from. A run from the ribbon keeps them for its window (`base_inputs`).
    if point and not fresh:
        globals().update(base['inputs'])
        output.print_md(u'## Laid again: section(s) {0}'.format(u', '.join(sorted(u'{0}'.format(s) for s in only))))
        output.print_md(u'_Only these sections were removed and laid again - the collecting and sun '
                        u'study of the first run, the rest of the model as it stands._')
    else:
        if point:
            output.print_md(u'## Laid again on the model as it is now: section(s) {0}'.format(
                u', '.join(sorted(u'{0}'.format(s) for s in only))))
            output.print_md(u'_The model collected and the sun studied again; only these sections were removed '
                            u'and laid again, the rest of the model as it stands._')
        names_before = dict(globals())     # what the block below sets, new or set again, is the first run's input
        bar.phase('collect')
        result = collect.run(doc, with_geometry=True, progress=bar.tick)
        if cancelled(bar):
            quit_run()
        if skip_unnumbered:
            _left_out = result.drop_unnumbered()
            if _left_out:
                output.print_md(u'**{0} плит(и) житла без номера секції пропущено** - «Далі» у вікні '
                                u'«Бракує номерів секцій»; вони не розкладалися.'.format(_left_out))
        if skip_duplicates:
            _left_out = result.drop_records([b for _a, b in result.duplicates()])
            if _left_out:
                output.print_md(u'**{0} зайвих плит(и) - дублі на тому ж поверсі тієї ж секції - не враховано** '
                                u'(«Далі» у вікні «Дублі плит»).'.format(_left_out))

        report.inputs_brief(output, result)

        # ------------------------------------------------------------------
        # 2. resolve both type lookups BEFORE the slow part
        #
        # A missing type is the likeliest reason this run produces nothing,
        # and finding that out after the sun study would be unkind.
        # ------------------------------------------------------------------
        bar.phase('types')

        resolved_all, missing_all, available, type_stats = roofs.resolve_types(doc)
        resolved = inputs.roof_types_for_inputs(resolved_all)
        missing = dict([(k, v) for k, v in missing_all.items()
                        if k in INPUT_ROOF_CLASSES])

        if not resolved:
            output.print_md('## No roof type resolved')
            output.print_md(
                'Neither `commercial` nor `parking` could be matched. Scanned '
                '{0} roof types, read {1} names.'.format(
                    type_stats['scanned'], type_stats['named']))
            forms.alert('Neither roof type could be resolved, so nothing would '
                        'be placed.\n\nFix ROOF_TYPES in lib/tadpole/config.py '
                        '— the report lists every roof type in the document.',
                        title='Tadpole', warn_icon=True)
            quit_run()

        types = build.resolve_all(doc)
        dark_type = inputs.find_wall_type(types, SUN_DARK_WALL_TYPE)

        if dark_type is None:
            output.print_md('## Non-insolated wall type not found')
            output.print_md('`{0}` is not in this model. Available:'.format(
                SUN_DARK_WALL_TYPE))
            output.print_md('\n'.join(
                ['- `{0}`'.format(n)
                 for n in sorted(types['wall_available'])][:60]))
            forms.alert('The non-insolated wall type was not found.\n\nFix '
                        'SUN_DARK_WALL_TYPE in lib/tadpole/config.py.',
                        title='Tadpole', warn_icon=True)
            quit_run()

        bar.done()

        # ------------------------------------------------------------------
        # 3. probe one roof and roll it back
        # ------------------------------------------------------------------
        bar.phase('probe')
        probe_ok, probe_info = roofs.probe(doc, result, resolved)
        if not probe_ok and probe_info.get('candidates', 0) == 0:
            output.print_md('## No commercial or parking elements to probe')
            output.print_md(
                'Roof types resolved, but nothing in this model is classified '
                '`commercial` or `parking`, so there was nothing to test or '
                'place. Continuing without a roof step.')
        elif not probe_ok:
            report.probe_failure(output, probe_info)
            forms.alert('Test roof placement failed, so nothing was placed.\n\n'
                        'The model is unchanged. The report shows the real '
                        'exception and the footprint that was attempted.',
                        title='Tadpole', warn_icon=True)
            quit_run()
        bar.done()

        # ------------------------------------------------------------------
        # 4. the variables — still read only
        # ------------------------------------------------------------------
        records = inputs.sun_records(result, sections)
        if not records:
            forms.alert('No section elements matched, so there is no facade '
                        'to analyse.', title='Tadpole', warn_icon=True)
            quit_run()

        # Classify against EVERYTHING that can abut — commercial, parking and
        # context included — but keep only the section runs. A section wall
        # meeting a neighbour is a party wall, and it only reads as one if the
        # neighbour took part in the comparison.
        bar.phase('walls')
        abutting = inputs.abutment_records(result, sections)
        outer_walls, inner_walls = inputs.wall_runs(
            abutting, classes=INPUT_SUN_CLASSES, progress=bar.tick)
        if cancelled(bar):
            quit_run()

        if not outer_walls:
            forms.alert('No outer wall runs were found.',
                        title='Tadpole', warn_icon=True)
            quit_run()

        # classify_floors solves every storey in one call, so there is no
        # inner position to report — the bar sits at the phase start until
        # it returns.
        bar.phase('plates')
        plates = inputs.floor_plates(records)
        faces = inputs.horizontal_faces(records)
        section_top_regions = inputs.section_tops(faces)
        section_other_regions = inputs.section_non_tops(faces, section_top_regions)
        section_bottom_faces = inputs.section_bottoms(faces)
        valid = inputs.valid_inner(records, inner_walls)
        bar.done()

        tops = inputs.top_storeys(result, sections)

        roof_targets = 0
        for use_class in resolved:
            for rec in result.of_class(use_class):
                if ui.keep(rec, sections):
                    roof_targets += 1

        output.print_md('## Collecting')
        output.print_md(
            '{0} outer wall runs, {1} inner | {2} floor plates '
            '| {3} commercial/parking roofs eligible'.format(
                len(outer_walls), len(inner_walls), len(plates), roof_targets))
        if missing:
            output.print_md('**Skipped, roof type not found:** {0}'.format(
                ', '.join(sorted(missing))))

        # ------------------------------------------------------------------
        # 5. sun study — most of the bar, because it is most of the time
        # ------------------------------------------------------------------
        bar.phase('sun')
        shading_stats = {}
        plan_bounds, min_z = inputs.study_bounds(outer_walls)
        obstructions = sun.build_obstructions(result.records,
                                              stats=shading_stats,
                                              min_z=min_z,
                                              plan_bounds=plan_bounds)
        reach = sun.model_reach(obstructions)
        options = sun.intersection_options()

        context = shading_stats.get(u'other', {})
        culled = sum(
            per.get('below lowest sample point', 0) + per.get('beyond context reach', 0)
            for per in shading_stats.values())
        ref_mode = sum(per.get('ref intersector mode', 0)
                      for per in shading_stats.values())
        ref_unavailable = sum(per.get('ref intersector unavailable - used grid', 0)
                              for per in shading_stats.values())
        output.print_md(
            'Shading: **{0}** blockers, of which **{1}** context; {2} of those '
            'have to be tested face-first because their solids are not closed '
            'volumes. **{3}** elements skipped before any face work - too low '
            'or beyond {4:g} m.'.format(
                len(obstructions), context.get('shading', 0),
                context.get('faces mode', 0), culled,
                SUN_CONTEXT_REACH_MM / 1000.0))
        if ref_mode or ref_unavailable:
            output.print_md(
                '{0} dense mesh(es) handed to Revit\'s own ReferenceIntersector '
                'instead of the Python face grid{1}.'.format(
                    ref_mode,
                    '' if not ref_unavailable else
                    ' ({0} wanted to but fell back to the grid instead - see '
                    'below for why)'.format(ref_unavailable)))
        if ref_unavailable:
            # The REAL reason, not a guessed one - see sun._new_ref_intersector.
            errors = {}
            for per in shading_stats.values():
                for msg, count in (per.get('errors') or {}).items():
                    errors[msg] = errors.get(msg, 0) + count
            for msg, count in sorted(errors.items(), key=lambda kv: -kv[1]):
                output.print_md('- {0} x `{1}`'.format(count, msg))

        dayloft, darkloft = inputs.study(outer_walls, vectors, obstructions,
                                         reach, options, progress=bar.tick,
                                         required=required_hours)
        if cancelled(bar):
            quit_run()
        # the rounded walls' own sun (user, 2026-10-20) - curved faces are no wall runs
        from tadpole import arcs as _arcs
        _arcs.ARC_SUN.clear()
        arcs_studied = sun.arc_study(result.of_class(u'section'), vectors, obstructions, reach, options,
                                     required=required_hours)

        base_inputs = dict((n, v) for n, v in globals().items()
                           if n != 'names_before' and (n not in names_before or names_before[n] is not v))

    # CORES, CORRIDORS, FLATS AND THE MIX - the kitchen, on the server (`tadpole.waiter`): decided from the runs, the
    # records and the dark stretches alone (`tadpole.order`); the model is read before it and written after it.
    # The records a section's height is measured on go with them - the sections and the blocks under them.
    height_records = [r for r in result.records
                      if getattr(r, 'section', None) or getattr(r, 'use_class', None) in BUILDING_CLASSES]
    bar.phase('slides', u'the kitchen lays the file out')
    kitchen = None
    try:
        packed, order_tokens = orders.pack(outer_walls, inner_walls, records, darkloft, height_records=height_records,
                                           north=north, flips=(again or {}).get('flips'), only=only,
                                           held=base.get('held') if point else None, values=setting_values)
        # THE ORDER AND THE DISH WRITTEN DOWN where %APPDATA%\Tadpole\capture exists - a run that went wrong is
        # laid again by `tools/kitchen.py`, off Revit
        capture = None
        try:
            capture = orders.capture_folder(doc.Title)
            if capture:
                orders.save(_os.path.join(capture, u'order.json'), packed)
        except Exception as error:
            output.print_md(u'_Capture failed, the run goes on: {0}_'.format(u'{0}'.format(error)[:200]))
            capture = None
        kitchen = waiter.Kitchen()
        raw_dish = kitchen.lay(packed)
        if capture:
            try:
                orders.save(_os.path.join(capture, u'dish.json'), raw_dish)
                output.print_md(u'_Capture: the order and the dish are in `{0}`._'.format(capture))
            except Exception as error:
                output.print_md(u'_Capture of the dish failed: {0}_'.format(u'{0}'.format(error)[:200]))
    except waiter.KitchenError as error:
        output.print_md(u'## The kitchen did not lay the file out')
        output.print_md(u'```\n{0}\n```'.format(error))
        forms.alert(u'The layout server did not answer, so nothing was placed.\n\n{0}'.format(
            u'{0}'.format(error)[:400]), title='Tadpole', warn_icon=True)
        quit_run()
    # the kitchen's level ids back as the button's own ElementIds
    level_ids = {}
    for _name, order_object in order_tokens.by_id.values():
        if getattr(order_object, 'level_id', None) is not None:
            level_ids[orders.element_id(order_object.level_id)] = order_object.level_id

    def _element_id(n):
        if n in level_ids:
            return level_ids[n]
        from Autodesk.Revit.DB import ElementId
        return ElementId(n)
    decoder = wire.Decoder(order_tokens, element_id=_element_id)
    laid = decoder.decode(raw_dish)
    unique_floors, section_building = laid['unique_floors'], laid['section_building']
    stair_plans, corridor_plans = laid['stair_plans'], laid['corridor_plans']
    stair_moves, apartment_plans = laid['stair_moves'], laid['apartment_plans']
    flip_offers, flip_refused, flip_chosen = laid['flip_offers'], laid['flip_refused'], laid['flip_chosen']
    mix_result = laid['mix_result']
    module_changes = []
    # the bands and the shares the kitchen laid with - in place, so every module that imported them reads these
    config.APARTMENT_BANDS.update(laid['bands'] or {})
    config.APARTMENT_MIX.update(laid['mix'] or {})
    config.MAX_1K = bool(laid['max_1k'])
    for step in ('bend', 'slides', 'flips', 'mix'):
        if step == 'flips' and laid['cancelled'] != 'slides' and \
                not any(s == 'flips' for s, _note in laid['notes']):
            try:
                report.core_flips(output, flip_offers, flip_refused, flip_chosen)
            except Exception as error:
                output.print_md(u'_The cores were not offered to the opposite side: {0}_'.format(
                    u'{0}'.format(error)[:120]))
        for s, note in laid['notes']:
            if s == step:
                output.print_md(note)
    bar.done()

    # the flats of every section as they stand now - the mix of a later point run counts the others at these
    held_now = dict(base.get('held') or {}) if point else {}
    held_now.update((mix_result or {}).get('section_counts') or {})

    # A POINT RUN writes only its sections: their cores, corridors, flats and groups. The roofs of commercial and
    # parking, the non-insolated walls and every other section are in the model already.
    # every section of the file, for the window at the end - before a point run keeps only its own
    all_sections = sorted(set(p.section for p in stair_plans), key=collect.section_sort_key)
    # ... and each section's largest plate, for the plan of the sections in that window (03)
    section_plates = laid['section_plates'] or {}
    if point:
        stair_plans = [p for p in stair_plans if p.section in only]
        corridor_plans = [p for p in corridor_plans if p.section in only]
        apartment_plans = [p for p in apartment_plans if p.section in only]

    # ------------------------------------------------------------------
    # 6. write — both kinds in one group, so one Ctrl+Z undoes the lot
    #
    # Not asked about. Placing is what the button is for, and the whole
    # run is a single Ctrl+Z.
    # ------------------------------------------------------------------
    handle = tx.group(doc, 'Tadpole')
    try:
        bar.phase('roofs')
        placements, roof_aborted = ([], None) if point else roofs.place(
            doc, result, resolved,
            recipe=probe_info.get('recipe'),
            variant=probe_info.get('variant'),
            sections=sections,
            progress=bar.tick)

        if PLACE_TEST_GEOMETRY:
            bar.phase('test')
            test_counts, test_failed = place_test_geometry(
                doc, outer_walls, inner_walls, darkloft, plates, types,
                dark_type, progress=bar.tick)
            walls_placed = test_counts['dark']
            walls_failed = test_failed
        elif not point or fresh:
            # a run on the model's geometry now places them all again - the old ones were removed with its sections
            bar.phase('darkwall')
            if darkloft:
                walls_placed, walls_failed, walls_aborted = \
                    inputs.place_sun_walls(
                        doc, darkloft, dark_type, 'dark',
                        'Tadpole - non-insolated walls', progress=bar.tick,
                        required=required_hours)
        bar.phase('stairs')
        stair_type, stair_type_name = placing.find_stair_type(doc)
        if stair_type is None:
            output.print_md('## No stair roof type')
            output.print_md(
                'None of `{0}` is in this model, so no stair core was '
                'placed.'.format('`, `'.join(STAIR_ROOF_TYPES)))
        elif stair_plans:
            stairs_placed, stairs_failed, stairs_aborted = placing.place_stairs(
                doc, stair_plans, stair_type,
                variant=probe_info.get('variant'), progress=bar.tick)

        bar.phase('corridor')
        corridor_type, corridor_type_name = placing.find_corridor_type(doc)
        if corridor_type is None:
            output.print_md('## No corridor roof type')
            output.print_md(
                'None of `{0}` is in this model, so no corridor was '
                'placed.'.format('`, `'.join(CORRIDOR_ROOF_TYPES)))
        elif corridor_plans:
            corridors_placed, corridors_failed, corridors_aborted = placing.place_corridors(
                doc, corridor_plans, corridor_type,
                variant=probe_info.get('variant'), progress=bar.tick)

        # a core or corridor standing on a commercial or parking plate of its level cuts its roof (user, 2026-10-21)
        try:
            from tadpole import cutouts
            cutout_log = cutouts.cut(doc, placements, stair_plans, corridor_plans)
        except Exception as error:
            cutout_log = [dict(roof=None, what=u'-', section=u'-', level=u'-', m2=0.0,
                               why=u'{0}'.format(error)[:160])]

        bar.phase('apartments')
        apartment_types = placing.find_flat_types(doc)
        if not any(apartment_types.values()):
            output.print_md('## No apartment roof types')
            output.print_md(
                'None of `{0}` is in this model, so no flats were '
                'placed.'.format('`, `'.join(
                    sorted(APARTMENT_ROOF_TYPES.values()))))
        elif apartment_plans:
            apartments_placed, apartments_failed, apartments_aborted = \
                placing.place_flats(doc, apartment_plans, apartment_types,
                                 variant=probe_info.get('variant'),
                                 progress=bar.tick)
        bar.done()

        tx.assimilate(handle)
    except Exception:
        tx.abort_group(handle)
        raise

    # ------------------------------------------------------------------
    # 6b. group — one Model Group per floor that got at least one
    # apartment placed above (a floor with only the stair core and the
    # corridor, and no flats, is not a floor TYPE yet - nothing to
    # group it as). {section}_type_{label}.
    #
    # ITS OWN Transaction: every place() call above already opened and
    # finished its own, so by here nothing is open any more, and
    # NewGroup needs one. ITS OWN try/except too, outside `handle` -
    # a grouping failure must never roll back the placements that are
    # already safely committed above (user, 2026-10-01: a run that hit
    # a grouping error placed the flats and then "deleted the result" -
    # that was `tx.abort_group(handle)` undoing the WHOLE group because
    # grouping shared its try/except with placement).
    # ------------------------------------------------------------------
    try:
        bar.phase('group')
        groups._group_log(u'phase-start')
        floors_to_group = groups.group_by_floor(
            apartment_plans, stair_plans, corridor_plans)
        groups._group_log(u'floors-to-group', keys=list(floors_to_group.keys()))
        if floors_to_group:
            groups._group_log(u'tx-start')
            group_tx = tx.start(doc, 'Tadpole — group floors')
            groups._group_log(u'tx-started')
            try:
                # ONE FLOOR AT A TIME, its own try/except - section 1
                # throwing must not stop sections 2 through 14 from
                # ever being attempted (user, 2026-10-01: nothing got
                # grouped at all, one crash for the whole run).
                for (section, level_id), ids in floors_to_group.items():
                    groups._group_log(u'loop-top', section=section, level_id=level_id)
                    try:
                        label = groups.DEFAULT_LABEL
                        group, reason = groups.make_group(
                            doc, level_id, section, label, ids)
                    except Exception as exc:
                        groups._group_log(u'loop-exc', error=u'{0}'.format(exc)[:160])
                        group, reason = None, u'{0}'.format(exc)[:160]
                    if group is not None:
                        groups_made.append((section, level_id, len(ids), label))
                        group_objects[(section, level_id)] = group
                        if reason:
                            groups_failed.append(
                                (section, level_id, u'grouped, but {0}'.format(reason)))
                    else:
                        groups_failed.append(
                            (section, level_id, reason or u'unknown error'))
            finally:
                tx.finish(group_tx)
        bar.done()
    except Exception as exc:
        groups_failed.append((None, None,
                              u'grouping crashed: {0}'.format(exc)[:200]))

    # ------------------------------------------------------------------
    # 6c. copy — every sibling floor of the one just grouped (same
    # UNIQUE FLOOR bucket: identical outer wall lengths, identical
    # shading sums - see unique.py, already computed above) gets its
    # own standalone stair core and corridor removed, and a fresh copy
    # of that {section}_type_X group put there instead, offset by the
    # two levels' elevation difference. A floor that is not in the
    # SAME bucket as the one just built is left alone - its own flats
    # would not actually come out the same way (user, 2026-10-01).
    #
    # Delete-then-copy is ONE SubTransaction per sibling (`tx.Attempt`),
    # so a copy that fails rolls the delete back with it, rather than
    # leaving a floor with neither the old roofs nor the new group.
    # ------------------------------------------------------------------
    try:
        bar.phase('copy')
        if group_objects:
            stair_by_name = {}
            stair_by_id = {}
            for item in stair_plans or []:
                if (getattr(item, 'ok', False)
                        and getattr(item, 'roof', None) is not None):
                    stair_by_name[(item.section, item.level_name)] = item
                    stair_by_id[(item.section, item.level_id)] = item
            corridor_by_name = {}
            for item in corridor_plans or []:
                if (getattr(item, 'ok', False)
                        and getattr(item, 'roof', None) is not None):
                    corridor_by_name[(item.section, item.level_name)] = item

            copy_tx = tx.start(doc, 'Tadpole — copy floor groups')
            try:
                for (section, level_id), group in group_objects.items():
                    working = stair_by_id.get((section, level_id))
                    if working is None:
                        continue
                    for sibling_name in groups.sibling_level_names(
                            unique_floors, section, working.level_name):
                        sibling_stair = stair_by_name.get((section, sibling_name))
                        if sibling_stair is None:
                            copies_failed.append(
                                (section, sibling_name,
                                 u'no stair core placed there'))
                            continue
                        old_ids = [sibling_stair.roof.Id]
                        sibling_corridor = corridor_by_name.get(
                            (section, sibling_name))
                        if sibling_corridor is not None:
                            old_ids.append(sibling_corridor.roof.Id)
                        dz = sibling_stair.level_elev - working.level_elev
                        with tx.Attempt(doc) as attempt:
                            attempt.result = groups.copy_to_sibling(
                                doc, group, dz, old_ids)
                        if attempt.ok:
                            copies_made.append((section, sibling_name))
                        else:
                            copies_failed.append(
                                (section, sibling_name,
                                 u'{0}'.format(attempt.error)[:160]))
            finally:
                tx.finish(copy_tx)
        bar.done()
    except Exception as exc:
        copies_failed.append((None, None,
                              u'copy step crashed: {0}'.format(exc)[:200]))

    # ------------------------------------------------------------------
    # 6d. EVERY OTHER UNIQUE FLOOR of each section, one after another in
    # the working floor's own order (the largest plate, two outer walls
    # over one, then the lowest by Z). The first group stands on the working
    # floor and on its bucket already.
    #
    # Each floor is READ FIRST, without touching the model: the L-shaped
    # flats of the first group, its other big flats, and its dead spaces,
    # all against THIS floor's shade (user, 2026-10-05).
    #   - nothing changes: the floor is the first one's twin, and the first
    #     group is simply copied onto it - and onto its sibling levels,
    #     grouped as it is;
    #   - something changes: the first group is copied and UNGROUPED, the
    #     changes are made on the copy, the whole floor is grouped again
    #     under a NEW name ({section}_type_B, C, ...) and that group is
    #     copied onto the floor's sibling levels.
    # Each floor keeps its own SubTransactions: one failing takes nothing
    # from the others.
    # ------------------------------------------------------------------
    try:
        bar.phase('next')
        if group_objects:
            stair_by_id = {}
            stair_by_name = {}
            for item in stair_plans or []:
                if (getattr(item, 'ok', False)
                        and getattr(item, 'roof', None) is not None):
                    stair_by_id[(item.section, item.level_id)] = item
                    stair_by_name[(item.section, item.level_name)] = item
            corridor_by_name = {}
            for item in corridor_plans or []:
                if (getattr(item, 'ok', False)
                        and getattr(item, 'roof', None) is not None):
                    corridor_by_name[(item.section, item.level_name)] = item
            working_levels = {}
            for (section, level_id) in group_objects:
                working = stair_by_id.get((section, level_id))
                if working is not None:
                    working_levels[section] = working.level_name
            # THE OTHER UNIQUE FLOORS ARE READ ON THE KITCHEN (`engine.read_floors`), against the plans that stand in
            # the model now: {section: [recipe]} in the order they are worked, each saying what changes on its floor
            placed_now = [decoder.number_of(p) for p in list(stair_plans) + list(corridor_plans)
                          + list(apartment_plans)
                          if getattr(p, 'ok', False) and getattr(p, 'roof', None) is not None]
            order = decoder.decode(kitchen.floors(working_levels, [n for n in placed_now if n is not None]))
            for section in working_levels:
                if section not in order:
                    next_failed.append((section, None,
                                        u'no other unique floor'))

            def fan_out(section, group, from_elev, from_name):
                """`group` copied onto every sibling level of the unique
                floor `from_name` stands in: their own stair core and
                corridor go, the copy takes their place. Returns the names
                of the levels it reached."""
                reached = []
                for sibling_name in groups.sibling_level_names(
                        unique_floors, section, from_name):
                    sibling_stair = stair_by_name.get((section, sibling_name))
                    if sibling_stair is None:
                        copies_failed.append(
                            (section, sibling_name,
                             u'no stair core placed there'))
                        continue
                    old_ids = [sibling_stair.roof.Id]
                    sibling_corridor = corridor_by_name.get(
                        (section, sibling_name))
                    if sibling_corridor is not None:
                        old_ids.append(sibling_corridor.roof.Id)
                    dz = sibling_stair.level_elev - from_elev
                    with tx.Attempt(doc) as attempt:
                        attempt.result = groups.copy_to_sibling(
                            doc, group, dz, old_ids)
                    if attempt.ok:
                        copies_made.append((section, sibling_name))
                        reached.append(sibling_name)
                    else:
                        copies_failed.append(
                            (section, sibling_name,
                             u'{0}'.format(attempt.error)[:160]))
                return reached

            def change_text(fit, transfers, splits, dead_recs, blind_recs,
                            l_change, mix_overlay=None):
                text = (u'{0} cut to the plate, {1} arm transfer(s), '
                        u'{2} big split(s), {3} dead space(s) '
                        u'changed, {4} gone dark'.format(
                            len(fit['flats']), len(transfers),
                            len([s for s in splits if not s.get('gave')]),
                            len(dead_recs), len(blind_recs)))
                gave = len([s for s in splits if s.get('gave')])
                if gave:
                    text += u'; {0} flat(s) gave the corridor band back to give a cut flat its door'.format(gave)
                if l_change:
                    text = l_change['note']
                if mix_overlay:
                    text = u'{0}; for the mix of the file: {1}'.format(
                        text, mix_overlay.get('note') or u'-')
                return text

            if order:
                # ONE TRANSACTION PER STAGE. The first group is made in its
                # own transaction and copied in the next, and its instance
                # survives; a group made in the same transaction as the
                # roofs it holds and copied at once lost the instance it was
                # made as (user, 2026-10-06: the floor the group was built on
                # came out ungrouped, only its copies were groups).
                next_state = [tx.start(doc, 'Tadpole — other unique floors')]

                def recycle(name):
                    tx.finish(next_state[0])
                    next_state[0] = tx.start(doc, name)

                try:
                    for section, targets in order.items():
                        working_name = working_levels[section]
                        working = stair_by_name.get((section, working_name))
                        first_group = None
                        for (s, lid), g in group_objects.items():
                            if s == section:
                                first_group = g
                        if first_group is None or working is None:
                            continue
                        label = groups.DEFAULT_LABEL
                        # floors that change the same flats into the same
                        # flats are ONE typical floor: one group, one name
                        made_groups = {}
                        for target in targets:
                            target_name = target['level_name']
                            recycle('Tadpole — unique floor {0}'.format(
                                target_name))
                            target_stair = stair_by_name.get(
                                (section, target_name))
                            if target_stair is None:
                                next_failed.append(
                                    (section, target_name,
                                     u'no stair core placed there'))
                                continue
                            old_ids = [target_stair.roof.Id]
                            target_corridor = corridor_by_name.get(
                                (section, target_name))
                            if target_corridor is not None:
                                old_ids.append(target_corridor.roof.Id)
                            dz = target_stair.level_elev - working.level_elev

                            # THE FLOOR AS THE KITCHEN READ IT - nothing was touched (`engine.read_floors`)
                            logs = target.get('logs') or {}
                            for review in logs.get('l') or []:
                                l_log.append((section, target_name, review))
                            for review in logs.get('fit') or []:
                                fit_log.append((section, target_name, review))
                            for review in logs.get('arm') or []:
                                arm_reviews.append((section, target_name, review))
                            for review in logs.get('dead') or []:
                                dead_reviews_log.append((section, target_name, review))
                            for review in logs.get('blind') or []:
                                blind_log.append((section, target_name, review))
                            if target.get('error'):
                                next_failed.append((section, target_name, target['error']))
                                continue
                            fit, transfers, splits = target['fit'], target['transfers'], target['splits']
                            dead_recs, blind_recs = target['dead_recs'], target['blind_recs']
                            l_change, l_unread, reviews_l = target['l_change'], target['l_unread'], target['reviews_l']

                            # what the mix of the file changed on this floor
                            mix_overlay = target['mix_overlay']
                            changed = target['changed']
                            if not changed:
                                # THE FIRST GROUP'S TWIN: copied as it is
                                with tx.Attempt(doc) as attempt:
                                    attempt.result = groups.copy_to_sibling(
                                        doc, first_group, dz, old_ids)
                                if not attempt.ok:
                                    next_failed.append(
                                        (section, target_name,
                                         u'{0}'.format(attempt.error)[:160]))
                                    continue
                                reached = fan_out(section, first_group,
                                                  working.level_elev,
                                                  target_name)
                                floor_outcomes.append(
                                    (section, target_name,
                                     (u'L-shaped floor NOT worked: {0}'.format(
                                         u'; '.join(reviews_l))
                                      if (l_unread and reviews_l) else
                                      u'L-shaped floor laid again: the flats come '
                                      u'out the same' if l_unread
                                      else u'nothing changes on this floor'),
                                     u'the first group copied as it is',
                                     u'{0}_type_{1}'.format(
                                         section, groups.DEFAULT_LABEL),
                                     reached))
                                continue

                            # THE SAME CHANGES AS A FLOOR ALREADY WORKED:
                            # the same typical floor. Its group is copied
                            # here, and onto this floor's sibling levels -
                            # no group of its own (user, 2026-10-05).
                            signature = target['signature']
                            twin = made_groups.get(signature)
                            if twin is not None:
                                twin_group, twin_elev, twin_floor, twin_name = twin
                                with tx.Attempt(doc) as attempt:
                                    attempt.result = groups.copy_to_sibling(
                                        doc, twin_group,
                                        target_stair.level_elev - twin_elev,
                                        old_ids)
                                if not attempt.ok:
                                    next_failed.append(
                                        (section, target_name,
                                         u'{0}'.format(attempt.error)[:160]))
                                    continue
                                reached = fan_out(section, twin_group,
                                                  twin_elev, target_name)
                                # the elevation `fan_out` measures from is
                                # the one the group stands at
                                floor_outcomes.append(
                                    (section, target_name,
                                     u'the same changes as {0}'.format(
                                         twin_floor),
                                     u'the same group copied',
                                     twin_name, reached))
                                continue

                            # SOMETHING CHANGES: copy, ungroup, change,
                            # group again under a new name, copy that
                            with tx.Attempt(doc) as attempt:
                                attempt.result = groups.copy_ungrouped(
                                    doc, first_group, dz, old_ids,
                                    target_stair.level_id)
                            if not attempt.ok:
                                next_failed.append(
                                    (section, target_name,
                                     u'{0}'.format(attempt.error)[:160]))
                                continue
                            member_ids, moved, skipped, roof_count = \
                                attempt.result
                            next_made.append((section, working_name,
                                              target_name, roof_count,
                                              moved, skipped))
                            level = doc.GetElement(target_stair.level_id)
                            flats_in_copy = placing.CopiedFlats(
                                doc, member_ids)
                            with tx.Attempt(doc) as change_attempt:
                                done_fit = (placing.apply_plate_fit(
                                    doc, fit, member_ids, level,
                                    apartment_types, section=section,
                                    corridor_type=corridor_type,
                                    variant=probe_info.get('variant'),
                                    copy=flats_in_copy)
                                    if fit['flats'] or fit['corridor'] else [])
                                done_arms = (placing.apply_arm_transfers(
                                    doc, transfers, member_ids, level,
                                    apartment_types,
                                    variant=probe_info.get('variant'),
                                    copy=flats_in_copy, section=section,
                                    corridor_type=corridor_type)
                                    if transfers else [])
                                done_big = (placing.apply_big_splits(
                                    doc, splits, member_ids, level,
                                    apartment_types,
                                    variant=probe_info.get('variant'),
                                    copy=flats_in_copy, section=section,
                                    corridor_type=corridor_type)
                                    if splits else [])
                                done_dead = (placing.apply_dead_changes(
                                    doc, dead_recs, member_ids, level,
                                    apartment_types,
                                    variant=probe_info.get('variant'),
                                    copy=flats_in_copy)
                                    if dead_recs else [])
                                done_blind = (placing.apply_dead_changes(
                                    doc, blind_recs, member_ids, level,
                                    apartment_types,
                                    variant=probe_info.get('variant'),
                                    copy=flats_in_copy)
                                    if blind_recs else [])
                                done_l = (placing.apply_l_changes(
                                    doc, l_change, member_ids, level,
                                    apartment_types, section=section,
                                    corridor_type=corridor_type,
                                    variant=probe_info.get('variant'),
                                    copy=flats_in_copy)
                                    if l_change else [])
                                # the mix of the file last: it works on what
                                # the floor's own changes made
                                done_mix = (placing.apply_mix(
                                    doc, mix_overlay, member_ids, level,
                                    apartment_types, section=section,
                                    variant=probe_info.get('variant'),
                                    copy=flats_in_copy)
                                    if mix_overlay else [])
                                change_attempt.result = (done_arms, done_big,
                                                         done_dead, done_fit,
                                                         done_blind, done_l,
                                                         done_mix)
                            if not change_attempt.ok:
                                arms_done.append(
                                    (section, target_name, None,
                                     u'{0}'.format(change_attempt.error)[:160]))
                                next_failed.append(
                                    (section, target_name,
                                     u'the changes were not made, so the floor '
                                     u'stays ungrouped as the copy'))
                                continue
                            done_arms, done_big, done_dead, done_fit, \
                                done_blind, done_l, done_mix = change_attempt.result
                            for rec, why in done_mix:
                                mix_done.append((section, target_name,
                                                 rec, why))
                            for rec, why in done_l:
                                l_done.append((section, target_name,
                                               rec, why))
                            for rec, why in done_blind:
                                blind_done.append((section, target_name,
                                                   rec, why))
                            for rec, why in done_fit:
                                fit_done.append((section, target_name,
                                                 rec, why))
                            for rec, why in done_arms:
                                arms_done.append((section, target_name,
                                                  rec, why))
                            for rec, why in done_big:
                                big_done.append((section, target_name,
                                                 rec, why))
                            for rec, why in done_dead:
                                dead_done.append((section, target_name,
                                                  rec, why))

                            # THE WHOLE FLOOR, GROUPED AGAIN UNDER A NEW NAME -
                            # in a transaction of its own, after the changes
                            # are committed
                            recycle('Tadpole — group unique floor {0}'.format(
                                target_name))
                            label = groups.next_label(label)
                            roof_ids = groups.live_roofs(
                                doc, member_ids, flats_in_copy.created)
                            with tx.Attempt(doc) as group_attempt:
                                group_attempt.result = groups.make_group(
                                    doc, target_stair.level_id, section, label,
                                    roof_ids)
                            new_group = None
                            group_note = None
                            if group_attempt.ok:
                                new_group, group_note = group_attempt.result
                            else:
                                group_note = u'{0}'.format(
                                    group_attempt.error)[:160]
                            if new_group is None:
                                groups_failed.append(
                                    (section, target_stair.level_id,
                                     group_note or u'unknown error'))
                                floor_outcomes.append(
                                    (section, target_name,
                                     change_text(fit, transfers, splits, dead_recs, blind_recs, l_change, mix_overlay),
                                     u'changed, but NOT grouped',
                                     u'-', []))
                                continue
                            groups_made.append((section, target_stair.level_id,
                                                len(roof_ids), label))
                            made_groups[signature] = (
                                new_group, target_stair.level_elev,
                                target_name,
                                u'{0}_type_{1}'.format(section, label))
                            if group_note:
                                groups_failed.append(
                                    (section, target_stair.level_id,
                                     u'grouped, but {0}'.format(group_note)))
                            recycle('Tadpole — copy the group of {0}'.format(
                                target_name))
                            reached = fan_out(section, new_group,
                                              target_stair.level_elev,
                                              target_name)
                            # IS THE GROUP STILL THERE? Said in the report,
                            # not assumed.
                            alive, instances = False, None
                            try:
                                alive = (new_group.IsValidObject and
                                         doc.GetElement(new_group.Id)
                                         is not None)
                                instances = new_group.GroupType.Groups.Size
                            except Exception:
                                alive = False
                            if not alive:
                                groups_failed.append(
                                    (section, target_stair.level_id,
                                     u'the group of this floor was LOST when '
                                     u'it was copied'))
                            elif instances is not None and \
                                    instances != 1 + len(reached):
                                groups_failed.append(
                                    (section, target_stair.level_id,
                                     u'{0} instance(s) of the group stand, '
                                     u'{1} expected'.format(
                                         instances, 1 + len(reached))))
                            floor_outcomes.append(
                                (section, target_name,
                                 change_text(fit, transfers, splits, dead_recs, blind_recs, l_change, mix_overlay),
                                 u'grouped again under a new name',
                                 u'{0}_type_{1}'.format(section, label),
                                 reached))
                finally:
                    tx.finish(next_state[0])
        bar.done()
    except Exception as exc:
        next_failed.append((None, None,
                            u'next-floor step crashed: {0}'.format(exc)[:200]))
    finally:
        if kitchen is not None:
            kitchen.close()


# ======================================================================
# 7. report everything — the bar is closed by now
# ======================================================================

report.inputs_funnel(output, result, records, outer_walls, inner_walls,
                     darkloft, walls_placed=walls_placed,
                     required=required_hours)

if PLACE_TEST_GEOMETRY:
    output.print_md('## Test geometry')
    output.print_md(
        '**{0}** outer walls, **{1}** inner walls, **{2}** dark '
        'stretches, **{3}** floor plates placed. Each carries its record '
        'in Comments - select one and read it, or schedule Comments to '
        'see them all at once. Inner walls are in the `inner` type from '
        'WALL_TYPES and read `{{\'index\': None}}`.'
        .format(test_counts.get('walls', 0), test_counts.get('inner', 0),
                test_counts.get('dark', 0), test_counts.get('floors', 0)))
    if test_failed:
        output.print_md('**Not placed:**')
        for reason in sorted(test_failed, key=lambda r: -test_failed[r]):
            output.print_md('- {0} x `{1}`'.format(test_failed[reason],
                                                   reason))

report.inputs_placed(output, placements, roof_aborted,
                     walls_placed, walls_failed, walls_aborted)

report.unique_floors(output, unique_floors, by_building=section_building)

report.stairs(output, stair_plans, placed=stairs_placed, failed=stairs_failed,
              aborted=stairs_aborted, roof_type=stair_type_name)

# counted on the kitchen, over a point run's sections only
whole_mix = laid['whole_mix']
for s, note in laid['notes']:
    if s == 'whole':
        output.print_md(note)
report.apartments(output, apartment_plans, placed=apartments_placed,
                  failed=apartments_failed, aborted=apartments_aborted,
                  moves=stair_moves, whole=whole_mix)

if module_changes:
    output.print_md(u'## Sections laid with a bigger module - to bring the flats into their bands')
    output.print_table(
        table_data=[[section, u'{0:.0f} mm'.format(laid.get('base_module_mm') or 0), u'{0:.0f} mm'.format(mm),
                     u'{0} flat(s), {1:.1f} m2 beyond their bands, {2} dead space(s)'.format(*before),
                     u'{0} flat(s), {1:.1f} m2 beyond their bands, {2} dead space(s)'.format(*after)]
                    for section, mm, before, after in module_changes],
        columns=['section', 'module was', 'module now', 'working floor with the old one', 'with the new one'])

report.mix_pass(output, mix_result)
for section, level_name, rec, why in mix_done:
    if why:
        output.print_md(u'- _the mix of the file NOT made on section {0} {1}: {2}_'.format(
            section, level_name, why))

report.corridors(output, corridor_plans, placed=corridors_placed,
                 failed=corridors_failed, aborted=corridors_aborted,
                 roof_type=corridor_type_name)

if cutout_log:
    output.print_md(u'## Openings under cores and corridors')
    output.print_md(u'Where a core or a corridor stands on a commercial or parking plate of its level, the roof '
                    u'placed on it is opened under it.')
    output.print_table(
        table_data=[[u'{0}'.format(r['section']), r['level'], r['what'], u'{0:.1f}'.format(r['m2']),
                     u'{0}'.format(r['roof']) if r['roof'] is not None else u'-',
                     r['why'] or u'cut'] for r in cutout_log],
        columns=['section', 'level', 'under', 'm2', 'roof', 'result'])

if groups_made or groups_failed:
    output.print_md('## Floor groups')
    if groups_made:
        output.print_table(
            table_data=[[section,
                        getattr(doc.GetElement(level_id), 'Name', u'?'),
                        count, u'{0}_type_{1}'.format(section, label)]
                       for section, level_id, count, label in groups_made],
            columns=['section', 'level', 'elements', 'group name'])
    if groups_failed:
        output.print_md('**Not grouped, or grouped with a note:**')
        for section, level_id, reason in groups_failed:
            level_name = (getattr(doc.GetElement(level_id), 'Name', u'?')
                          if level_id is not None else u'-')
            output.print_md(u'- section {0}, {1}: {2}'.format(
                section if section is not None else u'?', level_name, reason))

if copies_made or copies_failed:
    output.print_md('## Groups copied to sibling floors')
    if copies_made:
        output.print_table(
            table_data=[[section, level_name]
                       for section, level_name in copies_made],
            columns=['section', 'level'])
    if copies_failed:
        output.print_md('**Not copied:**')
        for section, level_name, reason in copies_failed:
            output.print_md(u'- section {0}, {1}: {2}'.format(
                section if section is not None else u'?',
                level_name if level_name is not None else u'-', reason))

if floor_outcomes:
    output.print_md('## The other unique floors')
    output.print_table(
        table_data=[[section, level_name, found, done, name,
                     u', '.join(reached) if reached else u'-']
                    for section, level_name, found, done, name, reached
                    in floor_outcomes],
        columns=['section', 'floor', 'what was found', 'what was done',
                 'group', 'also copied onto'])

if next_made or next_failed:
    output.print_md('## First group copied and ungrouped on a unique floor that changes')
    if next_made:
        output.print_table(
            table_data=[[section, source, level_name, members,
                         u'{0} re-seated'.format(moved)
                         + (u', {0} not'.format(skipped) if skipped else u'')]
                        for section, source, level_name, members, moved,
                        skipped in next_made],
            columns=['section', 'copied from', 'onto', 'pieces', 'level'])
    if next_failed:
        output.print_md('**Not copied:**')
        for section, level_name, reason in next_failed:
            output.print_md(u'- section {0}, {1}: {2}'.format(
                section if section is not None else u'?',
                level_name if level_name is not None else u'-', reason))

if big_done:
    output.print_md('## Big flats cut on the next unique floor')

    def big_text(part):
        return u'{0} {1:.1f} m2{2} - {3}'.format(
            u'dead' if part['dead'] else u'{0}k'.format(part['rooms']),
            part['raw'],
            u'' if part['dead'] or part['fits'] else u' (out of band)',
            (u'{0:.1f} m lit'.format(part['lit']) if part['lit_ok']
             else u'NO LIT MODULE ({0:.1f} m lit)'.format(part['lit'])))

    output.print_table(
        table_data=[[s, lv,
                     u'{0}k {1:.1f} m2'.format(rec['plan'].rooms,
                                               rec['plan'].raw_m2),
                     u'; '.join(big_text(p) for p in rec['parts']),
                     rec['note'] if why is None else u'NOT CUT: ' + why]
                    for s, lv, rec, why in big_done],
        columns=['section', 'floor', 'big flat was', 'now', 'how'])

if arm_reviews:
    output.print_md('## L-shaped flats looked at on the next unique floor')
    output.print_table(
        table_data=[[s, lv,
                     u'{0}k, {1:.1f} m2'.format(r['plan'].rooms, r['plan'].raw_m2),
                     (u'{0:.1f} lit / {1:.1f} dark'.format(r['lit'], r['dark'])
                      if r['lit'] is not None else u'-'),
                     (u'arm cut' if r['moved'] else u'left as it was'),
                     r['why']]
                    for s, lv, r in arm_reviews],
        columns=['section', 'floor', 'L-shaped flat', 'body facade, m',
                 'decision', 'why'])

if arms_done:
    output.print_md('## L-shaped flats on the next unique floor')
    rows_done = [(s, lv, rec) for s, lv, rec, why in arms_done if why is None]

    def flat_text(part):
        return u'{0} {1:.1f} m2{2}'.format(
            u'dead' if part['dead'] else u'{0}k'.format(part['rooms']),
            part['raw'],
            u'' if part['dead'] or part['fits'] else u' (out of band)')

    def sun_text(part):
        return (u'{0:.1f} m lit'.format(part['lit']) if part['lit_ok']
                else u'NO LIT MODULE ({0:.1f} m lit)'.format(part['lit']))

    if rows_done:
        output.print_table(
            table_data=[[s, lv,
                         u'{0}k {1:.1f} m2'.format(rec['body'].rooms,
                                                   rec['body'].raw_m2),
                         u'; '.join(u'{0} - {1}'.format(flat_text(p), sun_text(p))
                                    for p in rec['body_parts']),
                         rec.get('split_note') or u'-',
                         u'{0} {1:.1f} m2 -> {2} - {3}'.format(
                             u'dead space' if rec['neighbour'].dead
                             else u'{0}k'.format(rec['neighbour'].rooms),
                             rec['neighbour'].raw_m2,
                             flat_text(rec['neighbour_read']),
                             sun_text(rec['neighbour_read'])),
                         u'{0:.1f}'.format(rec['arm_m2'])]
                        for s, lv, rec in rows_done],
            columns=['section', 'level', 'L-shaped flat was',
                     'its body now', 'the body split', 'flat beside it',
                     'arm, m2'])
    for s, lv, rec, why in arms_done:
        if why is not None:
            output.print_md(u'- section {0}, {1}: arm not moved - {2}'.format(
                s, lv, why))

if fit_log:
    output.print_md('## Flats cut to a smaller plate')
    output.print_table(
        table_data=[[s, lv,
                     (u'{0}k, {1:.1f} m2'.format(r['plan'].rooms,
                                                 r['plan'].raw_m2)
                      if r['plan'] is not None else u'-'),
                     (u'{0:.1f} lit / {1:.1f} dark'.format(r['lit'], r['dark'])
                      if r['lit'] is not None else u'-'),
                     r['why']]
                    for s, lv, r in fit_log],
        columns=['section', 'floor', 'flat was', 'facade, m', 'what happened'])

if fit_done:
    output.print_md('## What the smaller plate did to the copy')

    def fit_text(part):
        return u'{0} {1:.1f} m2{2} - {3}'.format(
            u'dead' if part['dead'] else u'{0}k'.format(part['rooms']),
            part['raw'],
            u'' if part['dead'] or part['fits'] else u' (out of band)',
            (u'{0:.1f} m lit'.format(part['lit']) if part['lit_ok']
             else u'NO LIT MODULE ({0:.1f} m lit)'.format(part['lit'])))

    output.print_table(
        table_data=[[s, lv, rec['kind'],
                     u'; '.join(fit_text(p) for p in rec['parts']) or u'-',
                     rec['note'] if why is None else u'NOT DONE: ' + why]
                    for s, lv, rec, why in fit_done],
        columns=['section', 'floor', 'what', 'now', 'how'])

if dead_reviews_log:
    output.print_md('## Dead spaces read again on the other unique floors')
    output.print_table(
        table_data=[[s, lv, u'{0:.1f} m2'.format(r['plan'].raw_m2),
                     (u'{0:.1f} lit / {1:.1f} dark'.format(r['lit'], r['dark'])
                      if r['lit'] is not None else u'-'),
                     u'changed' if r['moved'] else u'left as it was',
                     r['why']]
                    for s, lv, r in dead_reviews_log],
        columns=['section', 'floor', 'dead space', 'facade, m', 'decision',
                 'why'])

if dead_done:
    output.print_md('## Dead spaces that became flats or went to a neighbour')

    def dead_text(part):
        return u'{0} {1:.1f} m2{2} - {3}'.format(
            u'dead' if part['dead'] else u'{0}k'.format(part['rooms']),
            part['raw'],
            u'' if part['dead'] or part['fits'] else u' (out of band)',
            (u'{0:.1f} m lit'.format(part['lit']) if part['lit_ok']
             else u'NO LIT MODULE ({0:.1f} m lit)'.format(part['lit'])))

    output.print_table(
        table_data=[[s, lv, rec['kind'],
                     u'; '.join(dead_text(p) for p in rec['parts']),
                     rec['note'] if why is None else u'NOT DONE: ' + why]
                    for s, lv, rec, why in dead_done],
        columns=['section', 'floor', 'became', 'now', 'how'])

if blind_log:
    output.print_md('## Flats read again for the sun on the other unique floors')
    output.print_table(
        table_data=[[s, lv, u'{0}k, {1:.1f} m2'.format(r['plan'].rooms,
                                                       r['plan'].raw_m2),
                     (u'{0:.1f} lit / {1:.1f} dark'.format(r['lit'], r['dark'])
                      if r['lit'] is not None else u'-'),
                     u'changed' if r['moved'] else u'left as it was',
                     r['why']]
                    for s, lv, r in blind_log],
        columns=['section', 'floor', 'flat', 'facade, m', 'decision', 'why'])

if blind_done:
    output.print_md('## Flats that went dark and were given to a neighbour')
    output.print_table(
        table_data=[[s, lv,
                     u'; '.join(u'{0}{1} {2:.1f} m2 - {3:.1f} m lit'.format(
                         u'dead ' if p['dead'] else u'',
                         u'' if p['dead'] else u'{0}k'.format(p['rooms']),
                         p['raw'], p['lit']) for p in rec['parts']),
                     rec['note'] if why is None else u'NOT DONE: ' + why]
                    for s, lv, rec, why in blind_done],
        columns=['section', 'floor', 'now', 'how'])

if l_log:
    output.print_md('## L-shaped floors that could not be laid again')
    output.print_table(table_data=[[s, lv, r] for s, lv, r in l_log],
                       columns=['section', 'floor', 'why'])

if l_done:
    output.print_md('## L-shaped floors laid again on their own sun')

    def l_text(part):
        return u'{0} {1:.1f} m2 - {2}'.format(
            u'dead' if part['dead'] else u'{0}k'.format(part['rooms']),
            part['raw'], (part.get('rule') or u'')[:90])

    output.print_table(
        table_data=[[s, lv, rec['kind'],
                     u'; '.join(l_text(p) for p in rec['parts']) or u'-',
                     rec['note'] if why is None else u'NOT DONE: ' + why]
                    for s, lv, rec, why in l_done],
        columns=['section', 'floor', 'what', 'now', 'how'])

if not getattr(bar, 'splash_ok', False):
    output.print_md('_No "Work in Frogress" card this run: {0}_'.format(
        getattr(bar, 'splash_error', None) or 'no reason recorded'))

report.inputs_manifest(output, inputs.manifest_amounts(
    {
        'outer_walls': outer_walls,
        'inner_walls': inner_walls,
        'dayloft': dayloft,
        'darkloft': darkloft,
        'section_tops': section_top_regions,
        'section_non_tops': section_other_regions,
        'section_bottoms': section_bottom_faces,
        'horizontal_faces': faces,
        'valid_inner': valid,
        'top_storeys': tops,
        'unique_floors': unique_floors,
    },
    setting_values,
    roofs_placed=len([p for p in placements if p.ok])))

splash.stop()

# A RUN LAID AGAIN from the window: what it did, at the foot of the report it is printed into
if point:
    try:
        report.rerun_summary(output, again, base.get('held') or {}, held_now, (mix_result or {}).get('brief'))
    except Exception as error:
        output.print_md(u'_The summary of the sections laid again failed: {0}_'.format(u'{0}'.format(error)[:120]))

# THE RUN IN NUMBERS - for the summary sent to the chat and the first card of the window at the end (user,
# 2026-10-19: "найперше дамо короткий звіт, скільки вийшло квартир і яке було завдання, і скільки поза діапазоном і
# в яких секціях найбільше"). The whole file: after a run laid again from the window, the other sections are counted
# as they stand (their flats `held`, their flats beyond the bands `band`, both carried from run to run).
run_numbers = None
try:
    if point:
        run_counts = {1: 0, 2: 0, 3: 0}
        for section_counts in (held_now or {}).values():
            for k in run_counts:
                run_counts[k] += (section_counts or {}).get(k, 0)
        run_dead, run_floors = None, None
    else:
        run_counts = dict((whole_mix or {}).get('counts') or {})
        run_dead, run_floors = (whole_mix or {}).get('dead'), (whole_mix or {}).get('floors')
    # THE FLATS INSIDE THEIR BANDS over every floor (user, 2026-10-19: "квартири в діапазоні площ", not the working
    # floors): the working floor's as laid, every other unique floor's as the mix of the file read it (`finals`),
    # each counted as many times as it has levels - section by section
    run_levels = {}
    for run_section, run_ufs in (unique_floors or {}).items():
        for run_uf in run_ufs:
            for run_name in run_uf.levels:
                run_levels[(run_section, run_name)] = len(run_uf.levels)
    run_band = {}
    for p in apartment_plans:
        if p.dead or getattr(p, 'skipped', False) or not p.points:
            continue
        run_w = run_levels.get((p.section, p.level_name), 1)
        here = run_band.setdefault(p.section, [0, 0])
        here[1] += run_w
        here[0] += run_w if getattr(p, 'in_band', False) else 0
    for (run_section, run_name), run_flats in ((mix_result or {}).get('finals') or {}).items():
        if point and run_section not in only:
            continue
        run_w = run_levels.get((run_section, run_name), 1)
        here = run_band.setdefault(run_section, [0, 0])
        run_in_band = (laid['finals_in_band'] or {}).get((run_section, run_name)) or []
        for run_i, (run_rooms, run_raw, run_is_dead) in enumerate(run_flats):
            if run_is_dead or not run_rooms:
                continue
            here[1] += run_w
            here[0] += run_w if (run_i < len(run_in_band) and run_in_band[run_i]) else 0
    band_now = dict((base.get('band') or {}) if point else {})
    band_now.update(run_band)
    run_numbers = dict(counts=run_counts, dead=run_dead, floors=run_floors,
                       brief=(mix_result or {}).get('brief'), max_1k=bool((mix_result or {}).get('max_1k')),
                       band=band_now, sections=dict(held_now or {}))
except Exception as error:
    band_now = dict((base.get('band') or {}) if point else {})
    output.print_md(u'_The run was not counted for its summary: {0}_'.format(u'{0}'.format(error)[:160]))

# THE REPORT TO THE CHAT (user, 2026-10-19): the whole report window as text to the team's share, named
# <computer>_<date>_<time>_<model>.txt, and a summary with its path to the Google Chat space - only on a machine
# that holds the space's webhook (%APPDATA%\Tadpole\chat_webhook.txt); nothing is sent from any other.
try:
    from tadpole import chat
    if chat.webhook() and run_numbers:
        chat_ok = sum(v[0] for v in run_numbers['band'].values())
        chat_all = sum(v[1] for v in run_numbers['band'].values())
        chat_console = base.get('console') if point else getattr(output, 'window', None)
        chat_txt, chat_txt_why = chat.save_report(chat_console, doc.Title, doc.PathName)
        chat_text = chat.summary(doc.Title, run_started, run_numbers['counts'], dead=run_numbers['dead'],
                                 floors=run_numbers['floors'], brief=run_numbers['brief'],
                                 max_1k=run_numbers['max_1k'], in_band=(chat_ok, chat_all),
                                 rerun=again if point else None, txt=chat_txt, txt_why=chat_txt_why)
        chat_sent, chat_why = chat.send(chat_text)
        output.print_md(u'_Sent to Google Chat{0}._'.format(u'; the report: {0}'.format(chat_txt) if chat_txt else u'')
                        if chat_sent else u'_Not sent to Google Chat: {0}_'.format(chat_why))
except Exception as error:
    output.print_md(u'_Not sent to Google Chat: {0}_'.format(u'{0}'.format(error)[:160]))

# THE WINDOW AT THE END, OPEN: the sections whose core may go across, ticked where it is across now. Revit stays free
# while it stands; its button removes only the sections whose tick changed and lays them again (`rerun`).
if flip_offers or all_sections:
    try:
        from tadpole import flip_ui
        path = rerun.script_path_of(globals())
        if path is None:
            try:
                from pyrevit import EXEC_PARAMS
                import os as _os
                path = _os.path.join(EXEC_PARAMS.command_path, 'script.py')
            except Exception:
                path = None
        event = rerun.arm(path, globals(), doc) if path else None
        if event is None:
            output.print_md(u'_The window for the cores was not opened: {0}_'.format(
                rerun.last_error or u'the script was not found'))
        else:
            # the report window this run printed into: a run laid again from the window prints into it too
            console = base.get('console') if point else getattr(output, 'window', None)
            # the button that shows the flats beyond their bands in 3D - its own ExternalEvent
            show3d_request = None
            try:
                from tadpole import show3d
                show3d_event = show3d.arm(on_done=flip_ui.status_sink)
                show3d_request = show3d.requester(show3d_event) if show3d_event is not None else None
            except Exception:
                show3d_request = None
            base_next = dict(start_id=run_start_id, inputs=base['inputs'] if point and not fresh else base_inputs,
                             held=held_now, band=band_now, console=console)
            flip_ui.show_open(flip_offers, flip_chosen,
                              rerun.applier(event, setting_values, latitude, output, base_next, flip_chosen),
                              all_sections, section_plates, run_numbers, show3d_request,
                              labels=laid.get('section_labels') if laid else None)
    except Exception as error:
        output.print_md(u'_The window for the cores was not opened: {0}_'.format(u'{0}'.format(error)[:120]))
