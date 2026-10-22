# -*- coding: utf-8 -*-
"""Dialogs and progress reporting, shared by the model-changing buttons."""

import time

from pyrevit import forms

from tadpole import splash
from tadpole.collect import NO_SECTION


def choose_sections(result, title=u'Which sections?', counter=None):
    """Pick which sections to build.

    Returns a set of section keys, or None if the user cancelled. When
    the model has only one section the dialog is skipped — there is
    nothing to choose.

    `counter` is an optional callable taking a list of ElementRecords and
    returning a number to show beside each section, so a button can say
    how many elements it would actually create.
    """
    if not result.sections:
        return None

    if len(result.sections) == 1:
        return set([result.sections[0].key])

    labels = []
    lookup = {}
    for section in result.sections:
        extra = u''
        if counter is not None:
            try:
                extra = u' — {0} to build'.format(counter(section.records))
            except Exception:
                extra = u''
        label = u'{0}   ({1} elements, {2} storeys){3}'.format(
            section.key, len(section.records), section.storey_count, extra)
        labels.append(label)
        lookup[label] = section.key

    chosen = forms.SelectFromList.show(
        labels,
        title=title,
        multiselect=True,
        button_name=u'Use these sections')

    if not chosen:
        return None
    if not isinstance(chosen, (list, tuple, set)):
        chosen = [chosen]
    return set([lookup[c] for c in chosen if c in lookup])


def section_of(record):
    return record.section if record.section else NO_SECTION


def keep(record, sections):
    """True when a record belongs to one of the chosen sections."""
    if sections is None:
        return True
    return section_of(record) in sections


# ----------------------------------------------------------------------
# one progress bar for a whole run
# ----------------------------------------------------------------------

class Progress(object):
    """A single bar across a whole run, divided into WEIGHTED phases.

    Phases differ by orders of magnitude — the sun study is minutes, a
    type lookup is milliseconds — so an equal split would park the bar at
    90% for the entire study and tell nobody anything. Each phase
    declares a weight and the bar advances in proportion to it.

    Weights are relative, not percentages: they are normalised, so they
    can be re-tuned without having to make them add up to anything.

        PHASES = [('collect', 'Collecting', 12), ('sun', 'Sun study', 55)]

        with Progress(PHASES) as bar:
            bar.phase('collect')
            bar.tick(i, total)
            if bar.cancelled:
                ...

    Redraws are THROTTLED BY TIME, not by a fixed step. Each one is a
    dispatcher round trip, and a caller that ticks once per element would
    otherwise spend longer drawing the bar than doing the work — the
    collect phase alone ticks several thousand times. A time budget
    adapts on its own: a fast loop skips almost every tick, a slow one
    draws on nearly all of them, and neither has to know its own size.

    The same budget is what keeps **Cancel** responsive, because pyRevit
    pumps its UI only when the bar is updated. So the interval sets both
    the redraw cost and the worst-case delay before a cancel is noticed —
    at the default, about a tenth of a second each.

    Phase changes always draw, throttle or no, so the label never lags
    behind what the run is actually doing.

    Every call into the underlying bar is guarded. Losing the progress
    display is a cosmetic failure, and it must never be the thing that
    takes down a run that is otherwise fine.
    """

    # Seconds between redraws. Low enough to look continuous and to keep
    # Cancel responsive, high enough that a tight loop is not throttled
    # by its own progress bar.
    MIN_INTERVAL = 0.1

    def __init__(self, phases, cancellable=True, min_interval=None):
        self.phases = list(phases)
        self.min_interval = (self.MIN_INTERVAL if min_interval is None
                             else min_interval)
        self._last_draw = 0.0
        self._drawn = 0
        self._skipped = 0
        total = float(sum([p[2] for p in self.phases]) or 1)

        self._label = {}
        self._start = {}
        self._span = {}
        running = 0.0
        for key, label, weight in self.phases:
            self._label[key] = label
            self._start[key] = running / total
            self._span[key] = weight / total
            running += weight

        self._key = self.phases[0][0]
        self._bar = None
        self._cancellable = cancellable

    def __enter__(self):
        # A button normally puts the card up at its very first line, before
        # any dialog; this is the fallback for one that does not. Whoever
        # started it takes it down - see __exit__.
        self._own_splash = splash.ensure()
        self.splash_ok = splash.active()
        self.splash_error = splash.last_error
        self._bar = forms.ProgressBar(title=self._title_for(None),
                                      cancellable=self._cancellable)
        self._bar.__enter__()
        self._draw(0.0, None, force=True)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # the card goes whatever happened - a run that fell over must not
        # leave something floating on top of Revit - unless the button put
        # it up itself, in which case the button is still running
        if getattr(self, '_own_splash', False):
            splash.stop()
        if self._bar is None:
            return False
        return self._bar.__exit__(exc_type, exc_value, traceback)

    @property
    def cancelled(self):
        # the x on the "Work in Frogress" card stops the run as the bar's own cancel does (2026-10-20)
        if splash.cancel_requested:
            return True
        try:
            return bool(self._bar.cancelled)
        except Exception:
            return False

    def phase(self, key, note=None):
        """Move to a phase and redraw at its starting position."""
        if key in self._label:
            self._key = key
        self._draw(0.0, note, force=True)
        return self.cancelled

    def tick(self, index=0, total=1, note=None):
        """Position WITHIN the current phase. Returns True to stop.

        Shaped to be passed straight in as the `progress` callback that
        collect.run, roofs.place and inputs.study accept.
        """
        if total and total > 0:
            fraction = index / float(total)
        else:
            fraction = 1.0
        self._draw(min(1.0, max(0.0, fraction)), note)
        return self.cancelled

    def done(self, note=None):
        """Finish the current phase — for a step with nothing to count."""
        self._draw(1.0, note, force=True)
        return self.cancelled

    # -- internals -----------------------------------------------------

    def _title_for(self, note):
        label = self._label.get(self._key, u'Working')
        if note:
            label = u'{0} — {1}'.format(label, note)
        # {value} is filled in by pyRevit; the braces have to survive
        # this format() call, hence the doubling.
        return u'{0}  {{value}}%'.format(label)

    def _draw(self, fraction, note, force=False):
        now = time.time()
        if not force and (now - self._last_draw) < self.min_interval:
            self._skipped += 1
            return
        self._last_draw = now
        self._drawn += 1

        overall = self._start.get(self._key, 0.0) \
            + self._span.get(self._key, 1.0) * fraction
        # the splash card grows on the same redraws (spawn -> frog with the share done) - the run owns the UI
        # thread, so this is the only moment anything gets drawn at all
        splash.blink(overall)
        try:
            self._bar.title = self._title_for(note)
        except Exception:
            pass
        try:
            self._bar.update_progress(int(round(overall * 100.0)), 100)
        except Exception:
            pass

    @property
    def stats(self):
        """(redrawn, skipped) — what the throttle saved, for the report."""
        return (self._drawn, self._skipped)
