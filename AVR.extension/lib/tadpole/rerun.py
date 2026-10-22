# -*- coding: utf-8 -*-
"""Sections laid again, after the person has looked at the run: the core on the opposite side of the sections ticked.

THE WINDOW AT THE END, OPEN (user, 2026-10-16: "вікно де пропонує переставити сходову з'являється ще перед тим, як
юзер може оцінити результат генерації. треба щоб воно все розклало і зробило, користувач міг покрутити, і тоді вже
приймати рішення"; of the three ways, "вікно в кінці, відкрите"). A run places everything and leaves the window open -
Revit is free to turn the model round. Its button raises an ExternalEvent.

POINT BY POINT (user, 2026-10-16: "перегенерація відбувалась точково, не видаляючи інших секцій, працюючи лиш з тими,
що належить до житла і має той відповідний номер"). In Revit's own time the handler
  1. removes, of the sections whose core changes side, only what Tadpole made for them - their floor groups
     ('{section}_type_...') and the roofs (cores, corridors, flats) with that section number, all with ids above the
     first run's start: the masses, the walls, the other sections, anything older stay;
  2. runs the button's script again (`AGAIN`): no collecting and no sun study - the first run's inputs (`base`) are
     used as they were - and only those sections are laid, mixed (against the rest of the file as it stands, `held`)
     and placed.

The script runs on a persistent engine (bundle.yaml: engine: persistent: true), so this module - and the event -
outlive it.
"""

import os
import sys
import time

AGAIN = None            # dict(values, flips, only, fresh, latitude, output, base) for the run the handler starts
_EVENTS = []            # the ExternalEvent and its handler, kept alive while the window is open
last_error = None


def take():
    """The rerun's state, once: None for a run started from the ribbon."""
    global AGAIN
    state, AGAIN = AGAIN, None
    return state


def _id_value(element_id):
    value = getattr(element_id, 'Value', None)
    return value if value is not None else element_id.IntegerValue


def max_id(doc):
    """The highest element id in `doc` - a run's elements are the ids it hands out after this."""
    from Autodesk.Revit.DB import FilteredElementCollector
    best = 0
    for collector in (FilteredElementCollector(doc).WhereElementIsNotElementType(),
                      FilteredElementCollector(doc).WhereElementIsElementType()):
        for eid in collector.ToElementIds():
            v = _id_value(eid)
            if v > best:
                best = v
    return best


def _prefixes(sections):
    return tuple(u'{0}_type_'.format(s) for s in sections)


def _section_of(element, name):
    try:
        parameter = element.LookupParameter(name)
        if parameter is None or not parameter.HasValue:
            return None
        return parameter.AsString() or parameter.AsValueString()
    except Exception:
        return None


def section_elements(doc, sections, after_id, dark_walls=False):
    """What Tadpole made for `sections` (ids above `after_id`), in the order it can be deleted: the floor groups,
    their types, then the roofs with that section number that are in no group. `dark_walls` - and every
    non-insolated wall Tadpole placed (a run on the model's geometry now places them all again: a changed plate
    moves its own and may shade its neighbours'). [element]"""
    from Autodesk.Revit.DB import FilteredElementCollector, Group, GroupType, BuiltInCategory
    from tadpole.config import SECTION_PARAM
    prefixes = _prefixes(sections)
    names = set(u'{0}'.format(s) for s in sections)

    def ours(collector):
        return [e for e in collector if _id_value(e.Id) > after_id]

    def name_of(element_type):
        # NOT `.Name`: on an ElementType IronPython throws for it, and a swallowed throw read every group as
        # nobody's - a point run left the old sections standing (user, 2026-10-16: "залишило старі")
        from tadpole.roofs import type_name as read
        try:
            return read(element_type) or u''
        except Exception:
            return u''

    def held_by_section(group):
        """a member roof with the section's number - the group is the section's whatever its type is called"""
        try:
            for member_id in group.GetMemberIds():
                member = doc.GetElement(member_id)
                if member is not None and (_section_of(member, SECTION_PARAM) or u'').strip() in names:
                    return True
        except Exception:
            pass
        return False

    def group_type_of(group):
        try:
            return doc.GetElement(group.GetTypeId())
        except Exception:
            return None

    groups_ = [g for g in ours(FilteredElementCollector(doc).OfClass(Group))
               if name_of(group_type_of(g)).startswith(prefixes) or held_by_section(g)]
    type_ids = set(_id_value(g.GetTypeId()) for g in groups_)
    types_ = [t for t in ours(FilteredElementCollector(doc).OfClass(GroupType))
              if _id_value(t.Id) in type_ids or name_of(t).startswith(prefixes)]
    roofs_ = [r for r in ours(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Roofs)
                              .WhereElementIsNotElementType())
              if (r.GroupId is None or _id_value(r.GroupId) < 0)
              and (_section_of(r, SECTION_PARAM) or u'').strip() in names]
    walls_ = []
    if dark_walls:
        from tadpole.config import SUN_DARK_WALL_TYPE
        dark = (SUN_DARK_WALL_TYPE or u'').strip().lower()
        walls_ = [w for w in ours(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Walls)
                                  .WhereElementIsNotElementType())
                  if dark and name_of(doc.GetElement(w.GetTypeId())).strip().lower() == dark]
    return groups_ + types_ + roofs_ + walls_


def remove_sections(doc, sections, after_id, dark_walls=False):
    """Delete `section_elements` in one transaction, one element at a time (a group takes its members with it).
    Returns (deleted, failed, kinds) - kinds {'groups', 'group types', 'roofs', 'walls'} counted, for the report."""
    from Autodesk.Revit.DB import ElementId
    from System.Collections.Generic import List
    from tadpole import tx

    deleted = failed = 0
    kinds = {}
    label = u', '.join(sorted(u'{0}'.format(s) for s in sections))
    transaction = tx.start(doc, u'Tadpole - sections {0} removed'.format(label))
    try:
        for element in section_elements(doc, sections, after_id, dark_walls):
            try:
                if not element.IsValidObject or doc.GetElement(element.Id) is None:
                    continue
                ids = List[ElementId]()
                ids.Add(element.Id)
                kind = _kind_of(element)
                doc.Delete(ids)
                deleted += 1
                kinds[kind] = kinds.get(kind, 0) + 1
            except Exception:
                failed += 1
        tx.finish(transaction)
    except Exception:
        tx.abort(transaction)
        raise
    return deleted, failed, kinds


def _kind_of(element):
    name = type(element).__name__
    if name == 'Group' or getattr(element, 'kind', None) == 'group':
        return u'groups'
    if name == 'GroupType' or getattr(element, 'kind', None) == 'gtype':
        return u'group types'
    try:
        walls = element.Category is not None and _id_value(element.Category.Id) == -2000011    # OST_Walls
    except Exception:
        walls = getattr(element, 'kind', u'') == 'wall'
    return u'non-insolated walls, placed again for the file' if walls else u'roofs'


class _ConsoleOut(object):
    """THE REPORT OF A RUN LAID AGAIN (user, 2026-10-16: "звіт має доповнюватися даними про перегенерацію"): the
    script prints through sys.stdout, which pyRevit ties to its command - gone once the command has ended, so the
    ExternalEvent's prints went nowhere. This writes them into the first run's report window itself, the way
    pyRevit's own stream does (ScriptConsole.AppendText with its default block)."""
    BLOCK = '<div class="entry"></div>'

    def __init__(self, console):
        self.console = console

    def write(self, text):
        if not text or not text.strip():
            return
        try:
            self.console.AppendText(text, self.BLOCK)
        except Exception:
            pass

    def flush(self):
        pass


def _console_open(console):
    try:
        return console is not None and not console.ClosedByUser
    except Exception:
        return False


def applier(event, values, latitude, output, base, laid):
    """What the window's button calls: AGAIN set and the event raised. `laid` - the sections whose core is across
    now; the ones laid again are those whose tick changed. Made here, not in the script - pyRevit clears a script's
    globals when it ends (unless its engine is persistent), and a button calling into them crashed Revit
    (2026-10-16, NameError inside a WPF click)."""
    laid = set(laid or ())

    def apply_(chosen, regen=()):
        global AGAIN
        chosen = set(chosen)
        # `regen` - sections laid again on the model's geometry as it is now (a plate changed after the run): they
        # are collected and studied for the sun again, not taken from the first run
        fresh = set(regen or ())
        only = (chosen ^ laid) | fresh
        if not only:
            return
        AGAIN = dict(values=values, flips=chosen, only=only, fresh=fresh, laid=set(laid), latitude=latitude,
                     output=output, base=base, started=time.time())
        event.Raise()
    return apply_


def arm(script_path, namespace, doc):
    """An ExternalEvent that removes the sections of AGAIN['only'] and runs `script_path` again with AGAIN as it is
    set when it is raised. Returns the event, or None where it cannot be made (see `last_error`)."""
    global last_error
    # only pyRevit's own names (__revit__, __file__, ...): taken now - the script's scope may be emptied when it ends
    builtins_ = dict((k, v) for k, v in namespace.items() if k.startswith('__'))
    doc_path = doc.PathName
    try:
        from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent

        class _Again(IExternalEventHandler):
            def Execute(self, uiapp):
                global last_error
                try:
                    here = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
                    if here is None or here.PathName != doc_path:
                        last_error = u'the active document is not the one the run was made in'
                        from pyrevit import forms
                        forms.alert(u'Tadpole: активний файл не той, у якому був прогін.', title='Tadpole')
                        return
                    state = AGAIN or {}
                    if not state.get('only') or not state.get('base'):
                        return
                    state['removed'] = remove_sections(here, state['only'], state['base']['start_id'],
                                                       dark_walls=bool(state.get('fresh')))
                    space = dict(builtins_)
                    space['__name__'] = '__main__'
                    console = state['base'].get('console')
                    shown = _console_open(console)
                    was_out = sys.stdout
                    if shown:
                        sys.stdout = _ConsoleOut(console)
                    try:
                        execfile(script_path, space)
                    except SystemExit:
                        pass
                    finally:
                        sys.stdout = was_out
                    if not shown:
                        from pyrevit import forms
                        forms.alert(u'Секції {0} перегенеровано. Вікно звіту було закрите, тож звіт не показано.'
                                    .format(u', '.join(sorted(u'{0}'.format(s) for s in state['only']))),
                                    title='Tadpole')
                except Exception as error:
                    last_error = u'{0}: {1}'.format(type(error).__name__, error)
                    try:
                        from pyrevit import forms
                        forms.alert(u'Tadpole не зміг перегенерувати: {0}'.format(last_error), title='Tadpole')
                    except Exception:
                        pass

            def GetName(self):
                return 'Tadpole - sections laid again'

        handler = _Again()
        event = ExternalEvent.Create(handler)
        del _EVENTS[:]
        _EVENTS.extend([handler, event])
        return event
    except Exception as error:
        last_error = u'{0}: {1}'.format(type(error).__name__, error)
        return None


def script_path_of(namespace):
    path = namespace.get('__file__')
    return path if path and os.path.exists(path) else None
