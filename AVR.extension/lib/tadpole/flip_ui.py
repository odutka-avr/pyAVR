# -*- coding: utf-8 -*-
"""The window that offers to put a section's core on the opposite long side.

THE CORE TO THE OPPOSITE SIDE (user, 2026-10-16: "має після генерації з'являтися вікно, яке пропонуватиме
переставити сходову на протилежний бік в такі секції, але залишаючи для неї ті ж модулі і тд, щоб це не впливало на
відсоток квартирограії"). The offers come from `apartments.flip_candidates`: the sections where the core may go and
the mix stays. Nothing is ticked at the start; the run puts the core across only where a box is ticked.
"""

import os

from pyrevit import forms

from System.Windows import (Thickness, GridLength, VerticalAlignment, CornerRadius, Visibility, Point,
                            FontWeights, TextAlignment, HorizontalAlignment, GridUnitType, TextWrapping)
from System.Windows.Controls import Canvas, StackPanel, Orientation, ToolTip, ToolTipService
from System.Windows.Input import Cursors
from System.Windows.Media import PointCollection, PenLineJoin, FontFamily
from System.Windows.Shapes import Polygon
from System.Windows.Controls import TextBlock, CheckBox, Grid, ColumnDefinition, Border
from System.Windows.Media import SolidColorBrush, Color, Brushes


XAML = os.path.join(os.path.dirname(__file__), 'flip.xaml')

GRAPHITE = SolidColorBrush(Color.FromRgb(0x33, 0x35, 0x3D))
FAINT = SolidColorBrush(Color.FromRgb(0x8F, 0x94, 0x9C))
STRIPE = SolidColorBrush(Color.FromRgb(0xF4, 0xF5, 0xF6))
INK = SolidColorBrush(Color.FromRgb(0x17, 0x1C, 0x21))
SHADE_REGEN = SolidColorBrush(Color.FromRgb(0xC8, 0xCB, 0xCF))     # ticked in 02: laid again
SHADE_FLIP = SolidColorBrush(Color.FromRgb(0xE9, 0xEA, 0xEC))      # ticked in 01: the core across

LINE_STRONG = SolidColorBrush(Color.FromRgb(0xC8, 0xCB, 0xCF))


def _sections_word(n):
    """секція / секції / секцій"""
    if n % 10 == 1 and n % 100 != 11:
        return u'секція'
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return u'секції'
    return u'секцій'


# the badges: laid again on the model's geometry now (03), the core to the other side (02)
REGEN_GLYPH = u'\u21bb'
FLIP_GLYPH = u'\u21c4'


def _badge(glyph, outlined=False):
    """A small black square with a white glyph - the chip of the AVR Design System; `outlined` - white with a black
    line and a black glyph, for what comes with a warning"""
    badge = Border()
    badge.Height = 18.0
    badge.MinWidth = 18.0
    badge.Padding = Thickness(3, 0, 3, 0)
    badge.CornerRadius = CornerRadius(2)
    badge.Background = Brushes.White if outlined else INK
    if outlined:
        badge.BorderBrush = INK
        badge.BorderThickness = Thickness(1.5)
    badge.Margin = Thickness(1, 0, 1, 0)
    text = TextBlock()
    text.Text = glyph
    text.FontFamily = FontFamily(u'Segoe UI Symbol')
    text.FontSize = 12
    text.FontWeight = FontWeights.Bold if outlined else FontWeights.Normal
    text.Foreground = INK if outlined else Brushes.White
    text.HorizontalAlignment = HorizontalAlignment.Center
    text.VerticalAlignment = VerticalAlignment.Center
    badge.Child = text
    return badge


# the plan of 03, px before the Viewbox fits it to the card
PLAN_W, PLAN_H = 620.0, 340.0


def _section_key(section):
    try:
        return (0, float(section), u'')
    except (TypeError, ValueError):
        return (1, 0.0, u'{0}'.format(section))


def _ring_area(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts))) / 2.0


def _centroid(pts):
    a = _ring_area(pts)
    if abs(a) < 1e-9:
        return (sum(x for x, _y in pts) / len(pts), sum(y for _x, y in pts) / len(pts))
    cx = cy = 0.0
    for i in range(len(pts)):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % len(pts)]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (6.0 * a), cy / (6.0 * a))


def _label_point(pts, given=None):
    """Where a section's number goes: the middle of its biggest convex piece (an L plate's centroid can fall
    outside it). `given` - that point as the kitchen found it (the Tadpole-split button: `engine.lay`,
    `section_labels`)."""
    if given is not None and len(given) == 2:
        return (float(given[0]), float(given[1]))
    try:
        from tadpole import lpoly
        pieces = lpoly.decompose(list(pts))
        if pieces:
            return _centroid(max(pieces, key=lambda q: abs(_ring_area(q))))
    except Exception:
        pass
    return _centroid(pts)

# tick | section | from -> to | the flats over all floors | m2 beyond the bands
COLUMNS = (34, 70, 170, 220, 130)


def _mix_text(offer):
    """1к 42 · 2к 0 · 3к 3 - or, where the mix changes, 1к 42 → 44 · 2к 0 · 3к 3 → 2."""
    before = offer.get('mix') or {}
    after = offer.get('mix_after') or before
    parts = []
    for key, name in ((1, u'1к'), (2, u'2к'), (3, u'3к'), (0, u'dead')):
        a, b = before.get(key, 0), after.get(key, 0)
        if key == 0 and not a and not b:
            continue
        parts.append(u'{0} {1}'.format(name, a) if a == b else u'{0} {1} → {2}'.format(name, a, b))
    return u' · '.join(parts)


def _lost_text(before, after):
    if before is None or after is None:
        return u'—'
    return u'{0:.1f} → {1:.1f}'.format(before, after)


class FlipWindow(forms.WPFWindow):

    def __init__(self, offers, ticked=None, on_apply=None, sections=(), plates=None, numbers=None, on_show3d=None,
                 labels=None):
        forms.WPFWindow.__init__(self, XAML)
        self.offers = sorted(offers, key=lambda o: bool(o.get('warn')))
        self.warn_sections = set(o['section'] for o in self.offers if o.get('warn'))
        self.ticked = set(ticked or ())
        self.on_apply = on_apply
        self.chosen = set()
        self.regen = set()
        self.boxes = {}
        self.regen_boxes = {}
        # 02 ANY SECTION LAID AGAIN on the model's geometry now (user, 2026-10-16: "дати змогу перегенерювати заново
        # будь-яку секцію за номером ... якщо архітектор ... схоче змінити трошки плиту секції в посадці")
        for section in sections or ():
            box = CheckBox()
            box.Style = self.FindResource('Check')
            box.Content = u'{0}'.format(section)
            box.Margin = Thickness(0, 0, 22, 10)
            box.MinWidth = 44
            self.regen_panel.Children.Add(box)
            self.regen_boxes[section] = box
        if not sections:
            self.card_regen.Visibility = Visibility.Collapsed
        self.numbers = numbers or {}
        self.labels = labels or {}
        # THE FLATS BEYOND THEIR BANDS IN 3D (user, 2026-10-19): the button asks Revit (`show3d`) to select and
        # isolate them, and a second press puts the view back
        self.on_show3d = on_show3d
        self.shown_3d = False
        if on_show3d is None:
            self.btn_show3d.Visibility = Visibility.Collapsed
        try:
            self._numbers(numbers)
        except Exception:
            numbers = None
        if not numbers:
            self.card_summary.Visibility = Visibility.Collapsed
        self.plan_shapes = {}
        self.plan_badges = {}
        try:
            self._plan(plates or {})
        except Exception:
            self.plan_shapes = {}
        if not self.plan_shapes:
            self.card_plan.Visibility = Visibility.Collapsed
        if not self.offers:
            self.card_flip.Visibility = Visibility.Collapsed
        else:
            self.flip_count.Text = u'{0} {1}'.format(len(self.offers), _sections_word(len(self.offers))) + (
                u' · {0} не рекомендовано'.format(len(self.warn_sections)) if self.warn_sections else u'')
        changed = [o for o in self.offers if o.get('mix_changed')]
        self.tb_status.Text = (u'Де квартирографія зміниться, це видно в рядку: до → після' if changed
                               else u'Квартирографія не зміниться')
        self.rows_panel.Children.Add(self._header())
        for index, offer in enumerate(self.offers):
            if offer.get('warn') and (index == 0 or not self.offers[index - 1].get('warn')):
                self.rows_panel.Children.Add(self._warn_heading())
            self.rows_panel.Children.Add(self._row(offer, index))
        # a tick in 01 or 02 shades the section on the plan
        for box in list(self.boxes.values()) + list(self.regen_boxes.values()):
            box.Checked += self._repaint
            box.Unchecked += self._repaint
        self._repaint()

    def _numbers(self, numbers):
        """00 THE RUN IN NUMBERS: the flats of the whole file, the mix against the task, the flats beyond their area
        bands and the sections with the most of them."""
        if not numbers:
            return
        counts = numbers.get('counts') or {}
        total = sum(counts.get(k, 0) for k in (1, 2, 3))
        self.sum_flats.Text = u'{0}'.format(total)
        notes = []
        if numbers.get('floors'):
            notes.append(u'на {0} поверхах'.format(numbers['floors']))
        if numbers.get('dead') is not None:
            notes.append(u'dead space {0}'.format(numbers['dead']))
        self.sum_flats_note.Text = u' · '.join(notes) or u'увесь файл'
        if total:
            self.sum_mix.Text = u' · '.join(u'{0:.0f}'.format(100.0 * counts.get(k, 0) / total) for k in (1, 2, 3)) + u' %'
        else:
            self.sum_mix.Text = u'—'
        brief = numbers.get('brief') or {}
        if numbers.get('max_1k'):
            self.sum_mix_note.Text = u'1к · 2к · 3к; завдання: максимум 1к'
        elif brief:
            self.sum_mix_note.Text = u'1к · 2к · 3к; завдання {0} %'.format(
                u' · '.join(u'{0:.0f}'.format(100.0 * brief.get(k, 0)) for k in (1, 2, 3)))
        else:
            self.sum_mix_note.Text = u'1к · 2к · 3к'
        # IN THE BANDS, AS A SHARE AND A BAR (user, 2026-10-19: "там де поза діапазоном - воно виглядає гнітюче ...
        # треба якесь порівняння дати, наприклад, у вигляді стрічки, де заповнена частина - скільки в діапазоні")
        band = numbers.get('band') or {}
        ok = sum(v[0] for v in band.values())
        all_ = sum(v[1] for v in band.values())
        out = all_ - ok
        self.sum_band.Text = u'{0:.0f} %'.format(100.0 * ok / all_) if all_ else u'—'
        self.sum_bar_in.Width = GridLength(float(ok if all_ else 1), GridUnitType.Star)
        self.sum_bar_out.Width = GridLength(float(out if all_ else 0), GridUnitType.Star)
        self.sum_band_note.Text = (u'{0} з {1} квартир'.format(ok, all_) if all_ else u'—')
        worst = sorted(((v[1] - v[0], s) for s, v in band.items() if v[1] - v[0] > 0),
                       key=lambda pair: (-pair[0], _section_key(pair[1])))[:3]
        self.sum_worst.Text = (u'Найбільше поза діапазоном: ' + u' · '.join(
            u'секція {0} — {1} кв.'.format(s, n) for n, s in worst)) if worst else u'Усі квартири в діапазоні площ.'

    def _plan(self, plates):
        """03 THE SECTIONS ON A PLAN (user, 2026-10-19: "нам треба якусь схему, аби людина бачила нумерацію тих
        секцій - план в масах, суто контур і нумерація"): each section's largest plate, in the model's plan axes
        (as a plan view in Revit shows them), its number in the middle of its biggest convex piece. A click on a
        section ticks it in 02."""
        rings = dict((s, [(float(x), float(y)) for x, y in pts]) for s, pts in plates.items() if pts and len(pts) >= 3)
        if not rings:
            return
        xs = [x for pts in rings.values() for x, _y in pts]
        ys = [y for pts in rings.values() for _x, y in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        pad = 14.0
        scale = min(PLAN_W / max(x1 - x0, 1e-6), PLAN_H / max(y1 - y0, 1e-6))
        self.plan_canvas.Width = (x1 - x0) * scale + 2 * pad
        self.plan_canvas.Height = (y1 - y0) * scale + 2 * pad

        def at(x, y):
            return Point(pad + (x - x0) * scale, pad + (y1 - y) * scale)     # plan Y up, screen Y down

        for section in sorted(rings, key=_section_key):
            pts = rings[section]
            shape = Polygon()
            ring = PointCollection()
            for x, y in pts:
                ring.Add(at(x, y))
            shape.Points = ring
            shape.Stroke = INK
            shape.StrokeThickness = 1.2
            shape.StrokeLineJoin = PenLineJoin.Miter
            shape.Fill = Brushes.White
            shape.Cursor = Cursors.Hand
            shape.ToolTip = self._tip(section)
            ToolTipService.SetInitialShowDelay(shape, 150)
            ToolTipService.SetShowDuration(shape, 20000)
            shape.MouseLeftButtonDown += self._toggler(section)
            self.plan_canvas.Children.Add(shape)
            self.plan_shapes[section] = shape
            cx, cy = _label_point(pts, self.labels.get(section))
            p = at(cx, cy)
            label = TextBlock()
            label.Text = u'{0}'.format(section)
            label.FontFamily = self.FindResource('Cond')
            label.FontSize = 14
            label.FontWeight = FontWeights.Bold
            label.Foreground = INK
            label.TextAlignment = TextAlignment.Center
            label.Width = 40.0
            label.IsHitTestVisible = False
            Canvas.SetLeft(label, p.X - 20.0)
            Canvas.SetTop(label, p.Y - 10.0)
            self.plan_canvas.Children.Add(label)
            # WHAT HAPPENS TO IT (user, 2026-10-19: "коли обираєш секцію - була зрозуміла піктограма, які зміни там
            # відбуваються - чи то вона просто заново перегенеровується, чи то сходи на другий бік"): a badge under
            # its number for each tick - the same badges as on the titles of 01 and 02
            row = StackPanel()
            row.Orientation = Orientation.Horizontal
            row.HorizontalAlignment = HorizontalAlignment.Center
            row.IsHitTestVisible = False
            badges = {}
            for kind, glyph in ((u'regen', REGEN_GLYPH), (u'flip', FLIP_GLYPH)):
                warn = kind == u'flip' and section in self.warn_sections
                badge = _badge(glyph + (u'!' if warn else u''), outlined=warn)
                badge.Visibility = Visibility.Collapsed
                row.Children.Add(badge)
                badges[kind] = badge
            holder = Grid()
            holder.Width = 60.0
            holder.IsHitTestVisible = False
            holder.Children.Add(row)
            Canvas.SetLeft(holder, p.X - 30.0)
            Canvas.SetTop(holder, p.Y + 10.0)
            self.plan_canvas.Children.Add(holder)
            self.plan_badges[section] = badges

    def _tip(self, section):
        """THE NUMBERS OF A SECTION UNDER THE MOUSE (user, 2026-10-19: "коли наводиш на будь-яку, перш ніж клікати
        для змін, там показує скільки є в діапазоні, скільки поза діапазоном і тд"): its flats over all its floors,
        the mix, the flats in and out of their bands with a bar, and whether its core may go across."""
        panel = StackPanel()
        panel.MinWidth = 210.0

        def line(text, size=12, bold=False, brush=None, top=0.0):
            block = TextBlock()
            block.Text = text
            block.FontFamily = self.FindResource('Cond')
            block.FontSize = size
            if bold:
                block.FontWeight = FontWeights.Bold
            block.Foreground = brush or INK
            block.Margin = Thickness(0, top, 0, 0)
            panel.Children.Add(block)

        line(u'Секція {0}'.format(section), 15, True)
        counts = ((self.numbers or {}).get('sections') or {}).get(section) or {}
        total = sum(counts.get(k, 0) for k in (1, 2, 3))
        if total:
            line(u'Квартир {0}: 1к {1} · 2к {2} · 3к {3}'.format(
                total, counts.get(1, 0), counts.get(2, 0), counts.get(3, 0)), top=4.0)
        band = ((self.numbers or {}).get('band') or {}).get(section)
        if band and band[1]:
            ok, all_ = band
            line(u'У діапазоні площ: {0} з {1} ({2:.0f} %)'.format(ok, all_, 100.0 * ok / all_), top=2.0)
            bar = Grid()
            bar.Height = 5.0
            bar.Margin = Thickness(0, 4, 0, 2)
            for width, brush in ((ok, INK), (all_ - ok, LINE_STRONG)):
                column = ColumnDefinition()
                column.Width = GridLength(float(width), GridUnitType.Star)
                bar.ColumnDefinitions.Add(column)
                cell = Border()
                cell.Background = brush
                Grid.SetColumn(cell, bar.ColumnDefinitions.Count - 1)
                bar.Children.Add(cell)
            panel.Children.Add(bar)
            if all_ - ok:
                line(u'Поза діапазоном: {0}'.format(all_ - ok), brush=GRAPHITE)
        # from the offers, not the boxes of 02: the plan is drawn before those rows are
        offer = next((o for o in self.offers if o['section'] == section), None)
        if offer is None:
            line(u'Сходову переставити не можна', brush=FAINT, top=4.0)
        elif offer.get('warn'):
            line(u'! Сходову переставити можна, але не рекомендується (2 кліки):', bold=True, top=6.0)
            faces = offer.get('faces') or (u'?', u'?')
            line(u'довгі сторони на {0} і {1}: сходова перейде на сонячний бік'.format(*faces), brush=GRAPHITE)
            line(u'квартири: {0}'.format(_mix_text(offer)), brush=GRAPHITE)
            line(u'поза діапазоном, м²: {0}'.format(_lost_text(offer.get('lost_before'), offer.get('lost_after'))),
                 brush=GRAPHITE)
        else:
            line(u'Сходову можна переставити — 2 кліки', brush=FAINT, top=4.0)
        tip = ToolTip()
        tip.Content = panel
        tip.Background = Brushes.White
        tip.BorderBrush = LINE_STRONG
        tip.Padding = Thickness(10, 8, 10, 8)
        return tip

    def _toggler(self, section):
        """A CLICK ON A SECTION GOES ROUND (user, 2026-10-19: "один клік - то перегенерюється вся секція, два кліки -
        сходова, три кліки - назад без вибору"): nothing -> laid again (02) -> the core across (01) -> nothing. A
        section whose core cannot go across (no row in 01) goes back to nothing on the second click."""
        def toggle(sender, args):
            regen = self.regen_boxes.get(section)
            flip = self.boxes.get(section)
            regen_on = bool(regen is not None and regen.IsChecked)
            flip_on = bool(flip is not None and flip.IsChecked)
            if not regen_on and not flip_on:
                want_regen, want_flip = regen is not None, regen is None and flip is not None
            elif regen_on:
                want_regen, want_flip = False, flip is not None
            else:
                want_regen, want_flip = False, False
            if regen is not None:
                regen.IsChecked = want_regen
            if flip is not None:
                flip.IsChecked = want_flip
        return toggle

    def _repaint(self, *args):
        for section, shape in self.plan_shapes.items():
            regen = self.regen_boxes.get(section)
            flip = self.boxes.get(section)
            regen_on = bool(regen is not None and regen.IsChecked)
            flip_on = bool(flip is not None and flip.IsChecked)
            if regen_on:
                shape.Fill = SHADE_REGEN
            elif flip_on:
                shape.Fill = SHADE_FLIP
            else:
                shape.Fill = Brushes.White
            badges = self.plan_badges.get(section) or {}
            if u'regen' in badges:
                badges[u'regen'].Visibility = Visibility.Visible if regen_on else Visibility.Collapsed
            if u'flip' in badges:
                badges[u'flip'].Visibility = Visibility.Visible if flip_on else Visibility.Collapsed

    def _grid(self):
        grid = Grid()
        for width in COLUMNS:
            column = ColumnDefinition()
            column.Width = GridLength(width)
            grid.ColumnDefinitions.Add(column)
        return grid

    def _text(self, text, column, brush=None, size=13):
        block = TextBlock()
        block.Text = text
        block.FontFamily = self.FindResource('Cond')
        block.FontSize = size
        block.Foreground = brush or GRAPHITE
        block.VerticalAlignment = VerticalAlignment.Center
        # a long side's name ("північний схід → південний захід") wraps inside its column, not over the next one
        block.TextWrapping = TextWrapping.Wrap
        block.Margin = Thickness(0, 0, 8, 0)
        Grid.SetColumn(block, column)
        return block

    def _header(self):
        grid = self._grid()
        grid.Margin = Thickness(6, 0, 6, 4)
        for column, text in ((1, u'СЕКЦІЯ'), (2, u'ЗАРАЗ → ПІСЛЯ'), (3, u'КВАРТИРИ, УСІ ПОВЕРХИ'),
                             (4, u'ПОЗА ДІАПАЗОНОМ, М²')):
            grid.Children.Add(self._text(text, column, FAINT, 11))
        return grid

    def _row(self, offer, index):
        grid = self._grid()
        box = CheckBox()
        box.Style = self.FindResource('Check')
        box.IsChecked = offer['section'] in self.ticked
        Grid.SetColumn(box, 0)
        grid.Children.Add(box)
        self.boxes[offer['section']] = box
        if offer.get('warn'):
            mark = StackPanel()
            mark.Orientation = Orientation.Horizontal
            Grid.SetColumn(mark, 1)
            mark.Children.Add(self._text(u'{0}'.format(offer['section']), 1))
            sign = _badge(u'!', outlined=True)
            sign.Margin = Thickness(8, 0, 0, 0)
            mark.Children.Add(sign)
            grid.Children.Add(mark)
        else:
            grid.Children.Add(self._text(u'{0}'.format(offer['section']), 1))
        grid.Children.Add(self._text(u'{0} → {1}'.format(offer['side_from'], offer['side_to']), 2))
        grid.Children.Add(self._text(_mix_text(offer), 3))
        grid.Children.Add(self._text(_lost_text(offer.get('lost_before'), offer.get('lost_after')), 4))
        band = Border()
        band.Child = grid
        band.Padding = Thickness(6, 6, 6, 6)
        band.CornerRadius = CornerRadius(2)
        band.Background = STRIPE if index % 2 else Brushes.Transparent
        return band

    def _warn_heading(self):
        """the heading over the north-south sections in 02"""
        row = StackPanel()
        row.Orientation = Orientation.Horizontal
        row.Margin = Thickness(6, 14, 6, 6)
        row.Children.Add(_badge(u'!', outlined=True))
        text = TextBlock()
        text.Text = (u'МОЖНА, АЛЕ НЕ РЕКОМЕНДУЄТЬСЯ — довгі сторони на північ і південь: сходова перейде на '
                     u'сонячний бік, квартири там гіршають')
        text.FontFamily = self.FindResource('Cond')
        text.FontSize = 12
        text.Foreground = GRAPHITE
        text.TextWrapping = TextWrapping.Wrap
        text.MaxWidth = 560.0
        text.Margin = Thickness(8, 0, 0, 0)
        text.VerticalAlignment = VerticalAlignment.Center
        row.Children.Add(text)
        return row

    def show3d_click(self, sender, args):
        if self.on_show3d is None:
            return
        want = not self.shown_3d
        # an error out of a WPF click is fatal to Revit: it is shown, never let through
        try:
            self.on_show3d(want)
        except Exception as error:
            self.tb_status.Text = u'3D: {0}'.format(error)[:120]
            return
        self.shown_3d = want
        self.btn_show3d.Content = u'ЗНЯТИ ВИДІЛЕННЯ' if want else u'ПОКАЗАТИ В 3D'
        self.tb_status.Text = u'Виділяю…' if want else u'Знімаю виділення…'

    def keep_click(self, sender, args):
        self.chosen = set(self.ticked)
        self.Close()

    def flip_click(self, sender, args):
        self.chosen = set(key for key, box in self.boxes.items() if box.IsChecked)
        self.regen = set(key for key, box in self.regen_boxes.items() if box.IsChecked)
        if self.on_apply is not None:
            # OPEN AT THE END: the choice goes to the run that lays the sections again (`rerun`), only if there is one
            # A NORTH-SOUTH CORE GOING ACROSS IS ASKED ABOUT FIRST, with its flats before and after
            newly = sorted((self.chosen - self.ticked) & self.warn_sections, key=_section_key)
            if newly and not ConfirmWindow([o for o in self.offers if o['section'] in newly]).ask():
                return
            if self.chosen != self.ticked or self.regen:
                # an error out of a WPF click is fatal to Revit: it is shown, never let through
                try:
                    self.on_apply(self.chosen, self.regen)
                except Exception as error:
                    try:
                        from pyrevit import forms
                        forms.alert(u'Tadpole не зміг перегенерувати: {0}'.format(error), title='Tadpole')
                    except Exception:
                        pass
        self.Close()


CONFIRM_XAML = os.path.join(os.path.dirname(__file__), 'confirm.xaml')


class ConfirmWindow(forms.WPFWindow):
    """THE WARNING BEFORE A NORTH-SOUTH CORE GOES ACROSS (user, 2026-10-19): a row per section - the sides, the flats
    before and after, the metres beyond the bands before and after. `ask()` - True to go on."""

    def __init__(self, offers):
        forms.WPFWindow.__init__(self, CONFIRM_XAML)
        self.go = False
        for offer in offers:
            faces = offer.get('faces') or (u'?', u'?')
            for text, size, bold in ((u'Секція {0} · {1} → {2}'.format(offer['section'], offer['side_from'],
                                                                          offer['side_to']), 14, True),
                                     (u'квартири: {0}'.format(_mix_text(offer)), 13, False),
                                     (u'поза діапазоном, м²: {0}'.format(
                                         _lost_text(offer.get('lost_before'), offer.get('lost_after'))), 13, False)):
                block = TextBlock()
                block.Text = text
                block.FontFamily = self.FindResource('Cond')
                block.FontSize = size
                if bold:
                    block.FontWeight = FontWeights.Bold
                    block.Margin = Thickness(0, 8, 0, 2)
                block.Foreground = INK if bold else GRAPHITE
                block.TextWrapping = TextWrapping.Wrap
                self.warn_rows.Children.Add(block)

    def back_click(self, sender, args):
        self.go = False
        self.Close()

    def go_click(self, sender, args):
        self.go = True
        self.Close()

    def ask(self):
        self.ShowDialog()
        return self.go


_OPEN = []


def status_sink(text):
    """What a handler (`show3d`) tells the open window - its status line."""
    try:
        if _OPEN:
            _OPEN[0].tb_status.Text = text
    except Exception:
        pass


def show_open(offers, ticked, on_apply, sections=(), plates=None, numbers=None, on_show3d=None, labels=None):
    """THE WINDOW AT THE END, OPEN (user, 2026-10-16: "вікно в кінці, відкрите"): shown without holding Revit, so the
    model can be turned round while it stands; the boxes ticked where the core is across now. Its button hands the
    ticked set to `on_apply` (which lays the model again) when it differs from what is laid. Returns the window, or
    None where it could not be shown."""
    if not offers and not sections:
        return None
    window = FlipWindow(offers, ticked, on_apply, sections, plates, numbers, on_show3d, labels)
    window.btn_keep.Content = u'ЗАКРИТИ'
    window.btn_flip.Content = u'ЗАСТОСУВАТИ'
    window.tb_status.Text = u'Модель можна крутити, поки вікно відкрите'

    window.Topmost = True
    window.show(modal=False)
    del _OPEN[:]            # held here: an open window outlives the script that showed it
    _OPEN.append(window)
    return window


def ask(offers):
    """Show the window. The set of sections whose core goes across (empty when nothing is ticked or it is closed)."""
    if not offers:
        return set()
    window = FlipWindow(offers)
    window.ShowDialog()
    return set(window.chosen)
