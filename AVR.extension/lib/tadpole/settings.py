# -*- coding: utf-8 -*-
"""The cutter's settings: bounds, validation, storage.

The values themselves come from the Grasshopper DKUI payload, and so do
the bounds and the validation rules - they are the one part of that
5000-line component that is pure Python with no Grasshopper in it.

WHAT IS NOT HERE, deliberately:

  - the HTML/CSS/JS the DKUI component generated. A WPF window replaces
    it, and the reports go to pyRevit's output window.
  - StartPulse, WarningPulse, sticky memory, the Stream Freeze gates.
    All of that exists because Grasshopper is a dataflow graph that
    re-solves when anything upstream expires, and wiring the warnings
    back was a cycle that took Revit down. A pyRevit button runs top to
    bottom on click and stops. None of it is needed.
  - persistence of any kind. The window hands its values straight to
    the run that opened it and they are reported with the result.
    Nothing is written to pyRevit's config, so one model's module
    values can never turn up in another model.

Nothing here reads the model or makes geometry. It holds numbers.
"""

import re


# ----------------------------------------------------------------------
# bounds, straight from the payload
# ----------------------------------------------------------------------

INSOLATION_MIN = 1.0
INSOLATION_MAX = 3.0
INSOLATION_DEFAULT = 3.0

MODULE_MIN = 2850
MODULE_MAX = 4000
MODULE_DEFAULT = 3100

RATIO_SUM_REQUIRED = 100
RATIO_DEFAULTS = (50, 40, 10)

AREA_ABSOLUTE_MIN = 1.0
AREA_ABSOLUTE_MAX = 1000.0
AREA_DEFAULTS = (u'36-48', u'55-75', u'80-120')

MAX_SECTIONS = 20

_RANGE = re.compile(r'^\s*([+-]?\d+(?:[.,]\d+)?)\s*[-–]\s*'
                    r'([+-]?\d+(?:[.,]\d+)?)\s*$')


def number(text):
    """'36,5' and '36.5' both mean 36.5. None when it is not a number."""
    if text is None:
        return None
    try:
        return float(u'{0}'.format(text).strip().replace(',', '.'))
    except (ValueError, TypeError):
        return None


def parse_range(text):
    """'36-50' -> (36.0, 50.0). None when it will not parse."""
    if text is None:
        return None
    match = _RANGE.match(u'{0}'.format(text))
    if not match:
        return None
    lo = number(match.group(1))
    hi = number(match.group(2))
    if lo is None or hi is None:
        return None
    return (lo, hi)


# ----------------------------------------------------------------------
# the values
# ----------------------------------------------------------------------

def defaults(section_keys=None):
    values = {
        u'insolation': INSOLATION_DEFAULT,
        u'ratios': list(RATIO_DEFAULTS),
        u'max_1k': False,
        u'areas': list(AREA_DEFAULTS),
        u'sections': {},
    }
    for key in (section_keys or []):
        values[u'sections'][u'{0}'.format(key)] = {
            u'module': MODULE_DEFAULT,
            u'stairs': False,
            u'variability': False,
        }
    return values


def validate(values):
    """(ok, [messages]). All or nothing, as the payload rule was.

    A partially valid settings set is worse than none: it runs, and the
    result looks like an answer.
    """
    errors = []

    hours = number(values.get(u'insolation'))
    if hours is None:
        errors.append(u'Інсоляція: не число.')
    elif not (INSOLATION_MIN <= hours <= INSOLATION_MAX):
        errors.append(u'Інсоляція має бути від {0} до {1} год.'.format(
            INSOLATION_MIN, INSOLATION_MAX))

    ratios = []
    # THE MOST 1K: the shares are not asked for, so they are not checked
    for index, raw in enumerate([] if values.get(u'max_1k') else (values.get(u'ratios') or [])):
        value = number(raw)
        if value is None:
            errors.append(u'Співвідношення {0}к: не число.'.format(index + 1))
        else:
            ratios.append(value)
    if len(ratios) == 3 and abs(sum(ratios) - RATIO_SUM_REQUIRED) > 1e-9:
        errors.append(u'Сума співвідношень має бути {0}, зараз {1:g}.'.format(
            RATIO_SUM_REQUIRED, sum(ratios)))

    for index, raw in enumerate(values.get(u'areas') or []):
        pair = parse_range(raw)
        if pair is None:
            errors.append(u'Діапазон {0}к: очікується "36-50".'.format(
                index + 1))
            continue
        lo, hi = pair
        if lo >= hi:
            errors.append(u'Діапазон {0}к: початок не менший за кінець.'
                          .format(index + 1))
        if lo < AREA_ABSOLUTE_MIN or hi > AREA_ABSOLUTE_MAX:
            errors.append(u'Діапазон {0}к: поза межами {1:g}-{2:g} м2.'.format(
                index + 1, AREA_ABSOLUTE_MIN, AREA_ABSOLUTE_MAX))

    for key, per in sorted((values.get(u'sections') or {}).items()):
        module = number(per.get(u'module'))
        if module is None:
            errors.append(u'Секція {0}: модуль не число.'.format(key))
        elif not (MODULE_MIN <= module <= MODULE_MAX):
            errors.append(u'Секція {0}: модуль поза межами {1}-{2} мм.'.format(
                key, MODULE_MIN, MODULE_MAX))

    return (not errors), errors


def describe(values):
    """Rows for the report: [(setting, value)]."""
    rows = [
        (u'Інсоляція', u'{0:g} год'.format(
            number(values.get(u'insolation')) or 0.0)),
    ]
    ratios = values.get(u'ratios') or []
    if values.get(u'max_1k'):
        rows.append((u'Співвідношення 1к / 2к / 3к', u'максимально 1к, без коригування за відсотками'))
    elif len(ratios) == 3:
        rows.append((u'Співвідношення 1к / 2к / 3к',
                     u'{0:g} / {1:g} / {2:g} %'.format(
                         number(ratios[0]) or 0, number(ratios[1]) or 0,
                         number(ratios[2]) or 0)))
    for index, raw in enumerate(values.get(u'areas') or []):
        pair = parse_range(raw)
        rows.append((u'Діапазон {0}к'.format(index + 1),
                     u'-' if pair is None
                     else u'{0:g} – {1:g} м2'.format(pair[0], pair[1])))
    return rows


def section_rows(values):
    """Per-section rows for the report: [(section, module, stairs, var)]."""
    rows = []
    for key, per in sorted((values.get(u'sections') or {}).items(),
                           key=_section_sort_key):
        rows.append((
            key,
            u'{0:g} мм'.format(number(per.get(u'module')) or 0),
            u'Протилежне' if per.get(u'stairs') else u'Дефолтне',
            u'Більші' if per.get(u'variability') else u'Менші',
        ))
    return rows


def _section_sort_key(item):
    """Numeric where it can be, so 10 follows 2 rather than preceding it."""
    key = item[0]
    value = number(key)
    return (0, value, u'') if value is not None else (1, 0.0, key)
