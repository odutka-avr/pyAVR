# -*- coding: utf-8 -*-
"""The settings window.

Opened by the run that needs the values, and it hands them back in
memory. NOTHING IS STORED: no pyRevit config, no sticky, no file. The
numbers exist for the length of one run and are reported with its
result, so what the report says it was given is what it was given.

The XAML lives beside this module rather than beside a button, because
the window belongs to whichever run opens it.
"""

import os

from pyrevit import forms

from System.Windows import (
    Thickness, GridLength, VerticalAlignment, CornerRadius, Visibility,
)
from System.Windows.Controls import (
    TextBlock, TextBox, CheckBox, Grid, ColumnDefinition, Border,
)
from System.Windows.Media import SolidColorBrush, Color, Brushes

from tadpole import settings


XAML = os.path.join(os.path.dirname(__file__), 'settings.xaml')

# the AVR Design System's greys (settings.xaml holds the same as resources)
MUTED = SolidColorBrush(Color.FromRgb(0x5C, 0x63, 0x6B))
FAINT = SolidColorBrush(Color.FromRgb(0x8F, 0x94, 0x9C))

# no. | module | switch | word | switch | word.
# Each width is what its OWN content needs: the stair words are short,
# the mix words are not, and giving them the same column left a gutter
# in the middle of the row.
COLUMNS = (34, 92, 44, 96, 44, 124)

# Every other row gets a white band. The payload grouped sections in a
# collapsible accordion; this is not that, but sixteen identical rows
# need something to track the eye along.
STRIPE = SolidColorBrush(Color.FromRgb(0xF4, 0xF5, 0xF6))      # surface-2, on the white panel

# THE SECTIONS' ROWS ARE OUT OF THE WINDOW FOR NOW (user, 2026-10-16: "ту частину 2 - де є більші/менші квартири та
# положення сходової і тд поки збережи в пам'яті, але з UI його варто забрати"): every section runs on
# settings.defaults - the project's module, the stairs on their default side, the smaller flats. True brings the
# group (card_sections in the XAML) and its rows back as they were.
SHOW_SECTIONS = False

# (what OFF means, what ON means) - the payload's own labels
STAIRS_LABELS = (u'Дефолтні', u'Протилежні')
MIX_LABELS = (u'Менші квартири', u'Більші квартири')


class SettingsWindow(forms.WPFWindow):
    """One window. No panes, no accordion — the reports live elsewhere."""

    def __init__(self, section_keys):
        forms.WPFWindow.__init__(self, XAML)
        self.section_keys = section_keys
        self.accepted = False
        self.values = settings.defaults(section_keys)
        self.rows = {}

        self.tb_status.Text = u'Використовується лише для цього запуску'
        self.tb_sections_note.Text = (
            u'{0} секцій у моделі. Модуль {1}–{2} мм.'.format(
                len(section_keys), settings.MODULE_MIN, settings.MODULE_MAX)
            if section_keys else
            u'У моделі не знайдено жодної секції.')

        if SHOW_SECTIONS:
            self.card_sections.Visibility = Visibility.Visible
        self._fill(self.values)
        self._build_sections(self.values)

    # -- rendering -----------------------------------------------------

    def _fill(self, values):
        self.tb_insolation.Text = u'{0:g}'.format(
            float(values[u'insolation']))
        for box, value in zip(
                (self.tb_r1, self.tb_r2, self.tb_r3), values[u'ratios']):
            box.Text = u'{0:g}'.format(float(value))
        # THE MOST 1K: the XAML greys out and half-hides the shares (ratio_block) while the switch is on
        self.cb_max1k.IsChecked = bool(values.get(u'max_1k', False))
        for box, value in zip(
                (self.tb_a1, self.tb_a2, self.tb_a3), values[u'areas']):
            box.Text = u'{0}'.format(value)

    def _build_sections(self, values):
        self.sections_panel.Children.Clear()
        self.rows = {}
        if not SHOW_SECTIONS:
            return

        keys = list(self.section_keys)
        if keys:
            self.sections_panel.Children.Add(self._header_row())

        for index, key in enumerate(keys[:settings.MAX_SECTIONS]):
            per = (values.get(u'sections') or {}).get(key, {})
            self.sections_panel.Children.Add(self._row(key, per, index))

    def _grid(self):
        grid = Grid()
        for width in COLUMNS:
            column = ColumnDefinition()
            column.Width = GridLength(width)
            grid.ColumnDefinitions.Add(column)
        return grid

    def _header_row(self):
        grid = self._grid()
        grid.Margin = Thickness(6, 0, 6, 4)
        # A caption spans the switch AND its word, so it sits over the
        # control it names rather than over the word beside it.
        for index, span, text in ((0, 1, u'№'),
                                  (1, 1, u'МОДУЛЬ, ММ'),
                                  (2, 2, u'СХОДИ'),
                                  (4, 2, u'КВАРТИРОГРАФІЯ')):
            block = TextBlock()
            block.Text = text
            block.Foreground = FAINT
            block.FontSize = 11
            block.FontFamily = self.FindResource('Cond')
            Grid.SetColumn(block, index)
            Grid.SetColumnSpan(block, span)
            grid.Children.Add(block)
        return grid

    def _row(self, key, per, index):
        grid = self._grid()

        label = TextBlock()
        label.Text = u'{0}'.format(key)
        label.VerticalAlignment = VerticalAlignment.Center
        Grid.SetColumn(label, 0)
        grid.Children.Add(label)

        module = TextBox()
        module.Text = u'{0:g}'.format(
            float(per.get(u'module', settings.MODULE_DEFAULT)))
        module.Margin = Thickness(0, 0, 12, 0)
        Grid.SetColumn(module, 1)
        grid.Children.Add(module)

        stairs, stairs_text = self._toggle(
            bool(per.get(u'stairs', False)), STAIRS_LABELS)
        Grid.SetColumn(stairs, 2)
        Grid.SetColumn(stairs_text, 3)
        grid.Children.Add(stairs)
        grid.Children.Add(stairs_text)

        larger, larger_text = self._toggle(
            bool(per.get(u'variability', False)), MIX_LABELS)
        Grid.SetColumn(larger, 4)
        Grid.SetColumn(larger_text, 5)
        grid.Children.Add(larger)
        grid.Children.Add(larger_text)

        self.rows[key] = (module, stairs, larger)

        band = Border()
        band.Child = grid
        band.Padding = Thickness(6, 4, 6, 4)
        band.CornerRadius = CornerRadius(2)
        band.Background = STRIPE if index % 2 else Brushes.Transparent
        return band

    def _toggle(self, checked, labels):
        """A switch plus the word for the state it is in.

        The word changes with the switch rather than naming only the ON
        meaning, because a switch labelled "opposite" reads as a claim
        when it is off. Off says what off means.
        """
        off_text, on_text = labels

        text = TextBlock()
        text.Foreground = MUTED
        text.VerticalAlignment = VerticalAlignment.Center
        text.Text = on_text if checked else off_text

        toggle = CheckBox()
        toggle.Style = self.FindResource('Check')
        toggle.IsChecked = checked

        def changed(sender, args):
            text.Text = on_text if sender.IsChecked else off_text

        toggle.Checked += changed
        toggle.Unchecked += changed
        return toggle, text

    # -- reading back --------------------------------------------------

    def _collect(self):
        values = {
            u'insolation': self.tb_insolation.Text,
            u'ratios': [self.tb_r1.Text, self.tb_r2.Text, self.tb_r3.Text],
            u'max_1k': bool(self.cb_max1k.IsChecked),
            u'areas': [self.tb_a1.Text, self.tb_a2.Text, self.tb_a3.Text],
            u'sections': {},
        }
        if not SHOW_SECTIONS:
            # no rows on the window: each section on its defaults
            values[u'sections'] = settings.defaults(self.section_keys)[u'sections']
        for key, (module, stairs, larger) in self.rows.items():
            values[u'sections'][key] = {
                u'module': module.Text,
                u'stairs': bool(stairs.IsChecked),
                u'variability': bool(larger.IsChecked),
            }
        return values

    # -- handlers ------------------------------------------------------

    def reset_click(self, sender, args):
        self.values = settings.defaults(self.section_keys)
        self._fill(self.values)
        self._build_sections(self.values)
        self.tb_errors.Text = u''

    def ok_click(self, sender, args):
        values = self._collect()
        ok, errors = settings.validate(values)
        if not ok:
            # All or nothing: a partly valid set runs and looks like an
            # answer, which is worse than refusing.
            self.tb_errors.Text = u'\n'.join(errors)
            return

        # Hand back numbers, not the strings somebody typed.
        values[u'insolation'] = float(settings.number(values[u'insolation']))
        if values.get(u'max_1k'):
            values[u'ratios'] = [100.0, 0.0, 0.0]
        else:
            values[u'ratios'] = [float(settings.number(v))
                                 for v in values[u'ratios']]
        for per in values[u'sections'].values():
            per[u'module'] = float(settings.number(per[u'module']))

        self.values = values
        self.accepted = True
        self.Close()


def ask(section_keys):
    """Show the window. Returns the values, or None if it was cancelled."""
    window = SettingsWindow(section_keys)
    window.ShowDialog()
    return window.values if window.accepted else None
