# -*- coding: utf-8 -*-
"""A small "Work in Frogress" card that sits on top while a run works.

Cosmetic, and treated as such: every call is guarded and a card that fails
to build, show or redraw never takes a run down with it. What it does NOT
do is fail silently - `last_error` keeps the reason, and the reports print
it, because a swallowed exception and a card nobody noticed look identical
from the outside.

WinForms, not WPF: a form paints on demand through Refresh(), and this run
holds Revit's UI thread from start to finish, so anything that waits for a
dispatcher pump - a WPF window, a DispatcherTimer - may never draw at all.

IN THE COMPANY'S STYLE, AND IT GROWS (user, 2026-10-16: "лого work in frogress
теж треба перевести в стиль компанії, і щоб там була перше ікра, тоді
пуголовок ріс в жабу по мірі виконання процесу"): the AVR Design System's
black card (#171C21), white line drawing, condensed capitals, a hairline
progress line with the share done. The drawing is the run's progress: spawn,
a tadpole, a tadpole with legs, a froglet, a frog. It is redrawn off the
SAME redraws the progress bar uses (ui.Progress calls `blink` with the share
done on every draw).
"""

TEXT = u'WORK IN FROGRESS'
EYEBROW = u'TADPOLE'

# (from this share of the run, the stage, its name - for the code and the report; the card shows no name)
# ON THE RUN'S SLOW PHASES, not its first fifth (user, 2026-10-19: "ікринки ... всього 2 секунди і все" - the first
# 20 % of the bar are collecting and reading the walls, a couple of seconds; the sun study alone is a third of it).
# The shares are where the phases of script.py's PHASES (weights 159 in all) start and end:
#   eggs - collecting .. the middle of the sun study   (0.00 - 0.39)
#   tadpole - the rest of the sun study                 (0.39 - 0.56)
#   legs - the cores tried in other places             (0.56 - 0.69)
#   grown - the mix of the file, placing the model     (0.69 - 0.95)
#   frog - grouping, copying, the other unique floors  (0.95 - 1.00)
STAGES = ((0.00, 0, u'ІКРА'),
          (0.39, 1, u'ПУГОЛОВОК'),
          (0.56, 2, u'ЛАПКИ'),
          (0.69, 3, u'ЖАБЕНЯ'),
          (0.95, 4, u'ЖАБА'))

WIDTH, HEIGHT = 460, 132
ART = 96                      # the drawing's square, px
TILT_DEG = -22.0              # the tadpole's head lifted, as the icon has it

# the AVR Design System's ink and greys
INK = (23, 28, 33)            # #171C21 - the card
TRACK = (51, 54, 61)          # #33363D - the progress line's track
FAINT = (143, 148, 156)       # #8F949C - captions

_form = None
_picture = None
_timer = None
_shown = None                 # (stage, percent) on the card now
last_error = None             # why there is no card, when there is none

# A WAY TO CLOSE IT (user, 2026-10-20, Str200 V1: the run fell over and the card stayed on top - "тедпоул ... завис,
# треба якусь кнопку його виключити"). The x in its corner takes the card down and asks the run to stop; and a card
# nobody has redrawn for STALE_S while Windows is delivering its messages - which only Revit's own loop or a dialog
# does, never the run, which pumps them through `blink` - belongs to a run that is gone: it takes itself down.
CLOSE_BOX = 30                # px, the x's square in the top right corner
STALE_S = 10.0
cancel_requested = False      # the x was pressed during a run
_last = [0.0]                 # when the run last redrew the card


def stage_of(fraction):
    """(stage, caption) for a share of the run done."""
    got = STAGES[0]
    for item in STAGES:
        if fraction + 1e-9 >= item[0]:
            got = item
    return got[1], got[2]


def _font_name(names):
    """The first of `names` the machine has; WinForms would otherwise fall back quietly to Microsoft Sans Serif."""
    try:
        from System.Drawing.Text import InstalledFontCollection
        have = set(f.Name for f in InstalledFontCollection().Families)
        for name in names:
            if name in have:
                return name
    except Exception:
        pass
    return names[-1]


def _draw_art(g, stage, x0, y0):
    """The creature of `stage`, white on the card, in the ART square at (x0, y0)."""
    from System import Array
    from System.Drawing import Color, Pen, SolidBrush, PointF, RectangleF
    from System.Drawing.Drawing2D import GraphicsPath, LineCap

    white = Color.White
    ink = Color.FromArgb(*INK)
    pen = Pen(white, 2.0)
    pen.StartCap = pen.EndCap = LineCap.Round
    fill = SolidBrush(white)
    hole = SolidBrush(ink)

    def P(x, y):
        return PointF(float(x0 + x), float(y0 + y))

    def ellipse(brush, cx, cy, w, h):
        g.FillEllipse(brush, RectangleF(float(x0 + cx - w / 2.0), float(y0 + cy - h / 2.0), float(w), float(h)))

    def ring(cx, cy, r):
        g.DrawEllipse(pen, RectangleF(float(x0 + cx - r), float(y0 + cy - r), float(2 * r), float(2 * r)))

    def bezier(p0, p1, p2, p3, n=18):
        out = []
        for i in range(n + 1):
            k = i / float(n)
            a, b, c, d = (1 - k) ** 3, 3 * k * (1 - k) ** 2, 3 * k * k * (1 - k), k ** 3
            out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                        a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
        return out

    def limb(p0, p1, p2, p3, w0, w1, edge=False):
        """A LEG AS A SHAPE, not a line (user, 2026-10-16: "жаб'ячі лапки та лапки пуголовка - вони просто рівні і
        неохайні"): filled along a curve, w0 thick at the body and w1 at the end, rounded at both; `edge` - a thin
        line of the card's ink round it, so a thigh over the white body still reads as a leg"""
        from System import Array
        import math
        pts = bezier(p0, p1, p2, p3)
        left, right = [], []
        for i, (x, y) in enumerate(pts):
            ax, ay = pts[max(0, i - 1)]
            bx, by = pts[min(len(pts) - 1, i + 1)]
            dx, dy = bx - ax, by - ay
            norm = math.hypot(dx, dy) or 1.0
            half = (w0 + (w1 - w0) * i / float(len(pts) - 1)) / 2.0
            nx, ny = -dy / norm * half, dx / norm * half
            left.append(P(x + nx, y + ny))
            right.append(P(x - nx, y - ny))
        outline = Array[PointF](left + list(reversed(right)))
        if edge:
            g.DrawPolygon(Pen(ink, 2.6), outline)
            g.FillEllipse(SolidBrush(ink), RectangleF(float(x0 + p0[0] - w0 / 2.0 - 1.5), float(y0 + p0[1] - w0 / 2.0 - 1.5),
                                                    float(w0 + 3.0), float(w0 + 3.0)))
        g.FillPolygon(fill, outline)
        ellipse(fill, p0[0], p0[1], w0, w0)
        ellipse(fill, p3[0], p3[1], w1, w1)

    def oval(cx, cy, w, h, angle):
        """a thigh: an oval turned `angle` degrees, white, with a thin line of the card's ink round it - it lies on
        the white body and has to read as a leg"""
        from System.Drawing.Drawing2D import Matrix
        path = GraphicsPath()
        path.AddEllipse(RectangleF(float(x0 + cx - w / 2.0), float(y0 + cy - h / 2.0), float(w), float(h)))
        turn = Matrix()
        turn.RotateAt(float(angle), P(cx, cy))
        path.Transform(turn)
        g.DrawPath(Pen(ink, 3.0), path)
        g.FillPath(fill, path)

    def foot(x, y, toward, length, width=2.2):
        """three toes fanned out from (x, y) toward `toward` degrees (0 - right, 180 - left, y down), each ending
        in a round pad"""
        import math
        for turn in (-32.0, 0.0, 32.0):
            a = math.radians(toward + turn)
            reach = length * (1.0 if turn == 0.0 else 0.8)
            tx, ty = x + math.cos(a) * reach, y + math.sin(a) * reach
            limb((x, y), (x + (tx - x) / 3.0, y + (ty - y) / 3.0), (x + 2 * (tx - x) / 3.0, y + 2 * (ty - y) / 3.0),
                 (tx, ty), width, width * 0.8)
            ellipse(fill, tx, ty, width * 1.25, width * 1.25)

    if stage == 0:
        # SPAWN: a cluster of eggs, each with its dark seed (user, 2026-10-19: these rings, not filled eggs -
        # "попередні ікринки були кращі"; what was wrong was how long they stood, see STAGES)
        for cx, cy in ((28, 40), (50, 32), (70, 44), (38, 62), (60, 66), (80, 66)):
            ring(cx, cy, 11)
            ellipse(fill, cx + 2, cy + 1, 7, 7)
        for cx, cy in ((16, 24), (86, 28), (20, 80)):
            ring(cx, cy, 2.5)
        return

    def eye(cx, cy, r):
        """THE EYE OF THE ICON (user, 2026-10-19 - the green tadpole she sent): a dark ring, a light iris, a dark
        pupil toward the front"""
        ellipse(hole, cx, cy, 2.0 * r + 3.0, 2.0 * r + 3.0)
        ellipse(fill, cx, cy, 2.0 * r, 2.0 * r)
        ellipse(hole, cx - r * 0.25, cy, r * 1.15, r * 1.15)

    def fin(x_from, tip, top, bottom, rise=0.0):
        """THE TAIL OF THE ICON: a broad leaf-shaped fin from the body at x_from to the tip, `top`/`bottom` its edges
        at the widest, with the icon's striped band inside its edge and a line down its middle"""
        mid = (top + bottom) / 2.0
        outer_top = ((x_from, mid - 9.0), (x_from + (tip - x_from) * 0.30, top),
                     (x_from + (tip - x_from) * 0.70, top + 4.0), (tip, mid + rise))
        outer_bot = ((tip, mid + rise), (x_from + (tip - x_from) * 0.70, bottom - 2.0),
                     (x_from + (tip - x_from) * 0.30, bottom), (x_from, mid + 9.0))
        path = GraphicsPath()
        path.AddBezier(*[P(*q) for q in outer_top])
        path.AddBezier(*[P(*q) for q in outer_bot])
        path.CloseFigure()
        g.FillPath(fill, path)
        # the striped band: the inner edge a thin dark line, short dark bars across the band
        inset = 6.0
        inner_top = tuple((x, y + inset * (1.0 if i in (1, 2) else 0.6)) for i, (x, y) in enumerate(outer_top))
        inner_bot = tuple((x, y - inset * (1.0 if i in (1, 2) else 0.6)) for i, (x, y) in enumerate(outer_bot))
        band = Pen(ink, 1.7)
        for outer, inner in ((outer_top, inner_top), (outer_bot, inner_bot)):
            g.DrawBeziers(band, Array[PointF]([P(*q) for q in inner]))
            o_pts, i_pts = bezier(*outer, n=12), bezier(*inner, n=12)
            for k in range(2, 11, 2):
                g.DrawLine(band, P(*o_pts[k]), P(*i_pts[k]))
        # the spine
        g.DrawBezier(Pen(ink, 1.2), P(x_from + 2.0, mid), P(x_from + (tip - x_from) * 0.40, mid + 1.0),
                     P(x_from + (tip - x_from) * 0.70, mid + rise * 0.5 + 1.0), P(tip - 6.0, mid + rise * 0.8))

    # Stages 1-3 face LEFT here and are turned at render time to face up and to the right as the icon does
    if stage in (1, 2):
        # A TADPOLE (user, 2026-10-19: "коли пуголовок без лапок - він зараз на дещо інше схожий, треба його
        # зробити більше як оця іконка"): a big round head, a broad striped fin; at stage 2 its hind legs
        fin(36, 94, 34, 70, rise=4.0)
        if stage == 2:
            limb((40, 62), (44, 71), (50, 75), (57, 75), 5.0, 2.8)
            foot(57, 75, 15, 5.0, 1.8)
        ellipse(fill, 28, 50, 42, 36)
        eye(20, 45, 6.0)
        return

    if stage == 3:
        # A TADPOLE GROWN UP: the head as before, the fin shorter, the hind legs bigger, the small front legs out
        fin(38, 84, 38, 66, rise=3.0)
        limb((42, 62), (50, 72), (56, 76), (64, 76), 6.0, 3.2)
        foot(64, 76, 12, 5.5, 1.9)
        limb((22, 64), (20, 70), (18, 74), (16, 78), 3.6, 2.2)
        foot(16, 78, 165, 3.8, 1.6)
        ellipse(fill, 29, 50, 44, 36)
        eye(20, 44, 6.2)
        return

    # A FROG: sitting, the eye on top, a front leg to the ground, the hind leg folded beside it, no tail
    limb((31, 66), (22, 72), (23, 80), (26, 85), 6.0, 3.2)
    foot(26, 85, 188, 7.5, 2.2)
    path = GraphicsPath()
    path.AddBezier(P(18, 56), P(20, 36), P(48, 30), P(70, 42))
    path.AddBezier(P(70, 42), P(84, 50), P(84, 68), P(70, 72))
    path.AddBezier(P(70, 72), P(52, 78), P(26, 74), P(18, 56))
    path.CloseFigure()
    g.FillPath(fill, path)
    ellipse(fill, 30, 36, 20, 18)
    eye(29, 35, 5.5)
    g.DrawLine(Pen(ink, 1.5), P(14, 56), P(30, 58))
    # the hind leg as a frog sits (user, 2026-10-16: "задня нога в жаби - дуже викривлена"): the thigh an oval
    # on the body's side, inside its outline; the shin folded under it forward along the ground; the foot ahead
    limb((60, 73), (55, 79), (50, 82), (45, 83.5), 6.0, 3.4)
    foot(45, 83.5, 186, 8.0, 2.2)
    oval(67, 63, 27, 14, -45)


_FROG = []


def _frog_image():
    """frog.png beside this file - her drawing, white on transparent - or None (then the drawn frog stands in)."""
    if _FROG:
        return _FROG[0]
    image = None
    try:
        import os
        from System.Drawing import Bitmap
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frog.png')
        if os.path.exists(path):
            image = Bitmap(path)
    except Exception:
        image = None
    _FROG.append(image)
    return image


def render(fraction):
    """The card as a Bitmap for `fraction` of the run done (0..1)."""
    from System.Drawing import (Bitmap, Color, Font, FontStyle, Graphics, Pen, PointF, RectangleF,
                                SolidBrush, StringAlignment, StringFormat)
    from System.Drawing.Drawing2D import SmoothingMode
    from System.Drawing.Text import TextRenderingHint

    fraction = min(1.0, max(0.0, float(fraction or 0.0)))
    stage = stage_of(fraction)[0]
    bmp = Bitmap(WIDTH, HEIGHT)
    g = Graphics.FromImage(bmp)
    try:
        g.SmoothingMode = SmoothingMode.AntiAlias
        g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit
        g.Clear(Color.FromArgb(*INK))
        # the drawing alone, without the stage's name under it (user, 2026-10-16: "підпис лапки, жабеня і тд
        # забрати, решта хай буде"), centred on the card's height
        top = (HEIGHT - ART) // 2
        frog = _frog_image() if stage == 4 else None
        if frog is not None:
            # HER OWN FROG (user, 2026-10-21: "отаку жабу постав, це я зробила"): white on the card, facing right as
            # she drew it - not mirrored like the drawn stages
            from System.Drawing import Rectangle
            from System.Drawing.Drawing2D import InterpolationMode
            g.InterpolationMode = InterpolationMode.HighQualityBicubic
            g.DrawImage(frog, Rectangle(18, top, ART, ART))
        elif stage == 0:
            _draw_art(g, stage, 18, top)
        else:
            # AS THE ICON FACES (user, 2026-10-19): head to the right and a little up - drawn facing left, mirrored
            # and turned about the middle of the drawing's square
            state = g.Save()
            g.TranslateTransform(float(18 + ART / 2.0), float(top + ART / 2.0))
            g.RotateTransform(float(TILT_DEG if stage < 4 else 0.0))
            g.ScaleTransform(-1.0, 1.0)
            g.TranslateTransform(float(-ART / 2.0), float(-ART / 2.0))
            _draw_art(g, stage, 0, 0)
            g.Restore(state)

        cond = _font_name(('Roboto Condensed', 'Arial Narrow', 'Tahoma'))
        num = _font_name(('Roboto', 'Tahoma'))
        white = SolidBrush(Color.White)
        faint = SolidBrush(Color.FromArgb(*FAINT))
        right = StringFormat()
        right.Alignment = StringAlignment.Far

        left, edge = 140.0, float(WIDTH - 18)
        # the x that closes it, in the top right corner
        cross = Pen(Color.FromArgb(*FAINT), 1.5)
        cx, cy, half = float(WIDTH - CLOSE_BOX // 2), float(CLOSE_BOX // 2), 5.0
        g.DrawLine(cross, cx - half, cy - half, cx + half, cy + half)
        g.DrawLine(cross, cx - half, cy + half, cx + half, cy - half)
        g.DrawString(EYEBROW, Font(cond, 8.5), faint, PointF(left - 2.0, 22.0))
        g.DrawString(TEXT, Font(cond, 19.0, FontStyle.Bold), white, PointF(left - 3.0, 38.0))

        # the progress: a hairline track, the share done over it in white, the percent at its end
        g.FillRectangle(SolidBrush(Color.FromArgb(*TRACK)), RectangleF(left, 88.0, edge - left, 2.0))
        g.FillRectangle(white, RectangleF(left, 88.0, (edge - left) * fraction, 2.0))
        g.DrawString(u'{0:d} %'.format(int(round(fraction * 100.0))), Font(num, 9.0), white,
                     RectangleF(left, 96.0, edge - left, 18.0), right)
    finally:
        g.Dispose()
    return bmp


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
    """Show the card. True when it is up; see `last_error` when it is not.
    (`text` is kept for the callers that pass one; the card says TEXT.)"""
    global _form, _picture, _shown, last_error, cancel_requested, _timer
    stop()
    last_error = None
    cancel_requested = False
    try:
        import clr
        clr.AddReference('System.Windows.Forms')
        clr.AddReference('System.Drawing')
        from System.Drawing import Color, Point, Size
        from System.Windows.Forms import (
            Form, FormBorderStyle, FormStartPosition, PictureBox, Screen)

        form = Form()
        # `None` is a keyword, so the enum member has to be fetched by name
        form.FormBorderStyle = getattr(FormBorderStyle, 'None')
        form.StartPosition = FormStartPosition.Manual
        form.ShowInTaskbar = False
        form.TopMost = True
        form.BackColor = Color.FromArgb(*INK)
        form.Size = Size(WIDTH, HEIGHT)

        picture = PictureBox()
        picture.Size = Size(WIDTH, HEIGHT)
        picture.Location = Point(0, 0)
        picture.Image = render(0.0)
        form.Controls.Add(picture)

        area = Screen.PrimaryScreen.WorkingArea
        form.Location = Point(area.X + (area.Width - form.Width) // 2,
                              area.Y + int(area.Height * 0.10))
        picture.MouseClick += _clicked
        from System.Windows.Forms import Timer
        timer = Timer()
        timer.Interval = 2000
        timer.Tick += _tick
        form.Show()
        form.BringToFront()
        _form, _picture, _timer = form, picture, timer
        _last[0] = _now()
        timer.Start()
        _pump()                       # without this the form never appears
        _shown = (0, 0)
        return True
    except Exception as exc:
        _form = _picture = None
        last_error = u'{0}: {1}'.format(type(exc).__name__, exc)
        return False


def _now():
    import time
    return time.time()


def _clicked(sender, args):
    """The x: the card goes, and the run is asked to stop. Never lets an error out - this runs in Revit's own loop."""
    global cancel_requested
    try:
        if args.X >= WIDTH - CLOSE_BOX and args.Y <= CLOSE_BOX:
            cancel_requested = True
            stop()
    except Exception:
        pass


def _tick(sender, args):
    """A card the run has not redrawn for STALE_S, while Windows delivers messages, outlived its run."""
    try:
        if _now() - _last[0] > STALE_S:
            stop()
    except Exception:
        pass


def ensure(text=None):
    """Show a card only if there is none. True when THIS call put it up, so
    whoever started it is the one that takes it down."""
    if active():
        return False
    return start(text)


def blink(fraction=None):
    """Redraw the card for `fraction` of the run done - only when the stage or the
    whole percent has changed. Safe to spam; called on every progress redraw."""
    global _shown, last_error
    _last[0] = _now()
    if _form is None or fraction is None:
        if _form is not None:
            _pump()
        return
    fraction = min(1.0, max(0.0, float(fraction)))
    now = (stage_of(fraction)[0], int(round(fraction * 100.0)))
    if now == _shown:
        _pump()
        return
    try:
        old = _picture.Image
        _picture.Image = render(fraction)
        if old is not None:
            old.Dispose()
        _shown = now
        _form.Refresh()
        _pump()
    except Exception as exc:
        last_error = u'redraw: {0}'.format(exc)


def stop():
    """Take the card down. Safe to call when there is none."""
    global _form, _picture, _timer
    try:
        if _timer is not None:
            _timer.Stop()
            _timer.Dispose()
    except Exception:
        pass
    _timer = None
    if _form is None:
        return
    try:
        _form.Close()
        _form.Dispose()
    except Exception:
        pass
    _form = _picture = None


def active():
    return _form is not None


def status():
    """One line for a report: shown, or why not."""
    if active():
        return u'shown'
    return u'not shown ({0})'.format(last_error or u'no reason recorded')
