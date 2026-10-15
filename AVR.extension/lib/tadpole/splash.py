# -*- coding: utf-8 -*-
"""A small "Work in Frogress <3" card that sits on top while a run works.

Cosmetic, and treated as such: every call is guarded and a card that fails
to build, show or blink never takes a run down with it. What it does NOT
do is fail silently - `last_error` keeps the reason, and the reports print
it, because a swallowed exception and a card nobody noticed look identical
from the outside.

WinForms, not WPF: a form paints on demand through Refresh(), and this run
holds Revit's UI thread from start to finish, so anything that waits for a
dispatcher pump - a WPF window, a DispatcherTimer - may never draw at all.

It blinks off the SAME redraws the progress bar uses (ui.Progress calls
`blink` on every draw), with its own interval, so the card breathes at one
pace however often the bar redraws.
"""

import time

TEXT = u'\U0001F438  Work in Frogress <3'
BLINK_SECONDS = 0.55          # between toggles
DIM_ALPHA = 0.45              # opacity of the "off" half of a blink

_form = None
_label = None
_visible = True
_last_toggle = 0.0
last_error = None             # why there is no card, when there is none


def _pump():
    """Let Windows deliver the messages that paint the card.

    The run owns Revit's UI thread for its whole length, so nothing is
    processed on its own: a form would be created, never painted, and the
    card would be invisible with no error to show for it.
    """
    try:
        from System.Windows.Forms import Application
        Application.DoEvents()
    except Exception:
        pass


def start(text=None):
    """Show the card. True when it is up; see `last_error` when it is not."""
    global _form, _label, _visible, _last_toggle, last_error
    stop()
    last_error = None
    try:
        import clr
        clr.AddReference('System.Windows.Forms')
        clr.AddReference('System.Drawing')
        from System.Drawing import (Color, ContentAlignment, Font, FontStyle,
                                    Point, Size)
        from System.Windows.Forms import (
            Form, FormBorderStyle, FormStartPosition, Label, Screen)

        form = Form()
        # `None` is a keyword, so the enum member has to be fetched by name
        form.FormBorderStyle = getattr(FormBorderStyle, 'None')
        form.StartPosition = FormStartPosition.Manual
        form.ShowInTaskbar = False
        form.TopMost = True
        form.BackColor = Color.FromArgb(31, 122, 77)
        form.Size = Size(420, 84)
        form.Opacity = 1.0

        label = Label()
        label.Text = text or TEXT
        label.ForeColor = Color.White
        label.BackColor = Color.Transparent
        label.Font = Font('Segoe UI Emoji', 18.0, FontStyle.Bold)
        label.AutoSize = False
        label.Size = Size(form.Width - 24, form.Height - 24)
        label.Location = Point(12, 12)
        # the real enum member, not its numeric value: IronPython will not
        # convert a plain int to a .NET enum
        label.TextAlign = ContentAlignment.MiddleCenter
        form.Controls.Add(label)

        area = Screen.PrimaryScreen.WorkingArea
        form.Location = Point(area.X + (area.Width - form.Width) // 2,
                              area.Y + int(area.Height * 0.10))
        form.Show()
        form.BringToFront()
        _pump()                       # without this the form never appears
        _form, _label = form, label
        _visible = True
        _last_toggle = time.time()
        return True
    except Exception as exc:
        _form = _label = None
        last_error = u'{0}: {1}'.format(type(exc).__name__, exc)
        return False


def ensure(text=None):
    """Show a card only if there is none. True when THIS call put it up, so
    whoever started it is the one that takes it down."""
    if active():
        return False
    return start(text)


def blink():
    """Toggle the card, at most once every BLINK_SECONDS. Safe to spam."""
    global _visible, _last_toggle, last_error
    if _form is None:
        return
    now = time.time()
    if (now - _last_toggle) < BLINK_SECONDS:
        return
    _last_toggle = now
    _visible = not _visible
    try:
        _form.Opacity = 1.0 if _visible else DIM_ALPHA
        _form.Refresh()
        _pump()
    except Exception as exc:
        last_error = u'blink: {0}'.format(exc)


def stop():
    """Take the card down. Safe to call when there is none."""
    global _form, _label
    if _form is None:
        return
    try:
        _form.Close()
        _form.Dispose()
    except Exception:
        pass
    _form = _label = None


def active():
    return _form is not None


def status():
    """One line for a report: shown, or why not."""
    if active():
        return u'shown'
    return u'not shown ({0})'.format(last_error or u'no reason recorded')
