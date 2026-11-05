# -*- coding: utf-8 -*-
"""Enter confirms a Revit pick, Esc cancels it.

Selection.PickObject only knows Esc (it raises OperationCanceledException). While a pick waits, a timer
watches the Enter key: when it is pressed and let go with Revit's window in front, `pressed` is set and Esc is sent
to Revit, so the pick ends; the caller tells Enter from a real Esc by `pressed` (see `confirmed`). Acting on the
RELEASE keeps the key from being still down when the dialog that follows the pick opens (its default button
would take the held Enter).

A problem anywhere in here is swallowed: an exception that escapes a timer tick would crash Revit, and the pick
works with Esc without this.
"""

VK_RETURN = 0x0D
_TICK_MS = 40


def step(armed, down, in_front):
    """One look at the key: (armed, finish). The key counts once it is down with Revit in front; the pick ends
    when it is then let go."""
    if down and in_front:
        return True, False
    if not down and armed:
        return False, True
    return False, False


def confirmed(watching, pressed):
    """Whether a finished pick is a confirmation: Enter confirms and Esc cancels. Without a working Enter watcher
    Esc is the only way to finish, so it confirms (as before)."""
    return bool(pressed) or not watching


class EnterEndsPick(object):
    def __init__(self, window_handle, log=None):
        self._handle = int(window_handle.ToInt64()) if hasattr(window_handle, "ToInt64") else int(window_handle or 0)
        self._log = log
        self._armed = False
        self._timer = None
        self._user32 = None
        self.pressed = False            # Enter (not Esc) ended the pick

    @property
    def watching(self):
        return self._timer is not None

    def start(self):
        """Start watching; False when the key cannot be watched here (the pick then ends with Esc only)."""
        try:
            import ctypes
            from System.Windows.Forms import Timer

            self._user32 = ctypes.windll.user32
            self._armed = False
            self._timer = Timer()
            self._timer.Interval = _TICK_MS
            self._timer.Tick += self._tick
            self._timer.Start()
            return True
        except Exception as error:
            self._note(u"Enter не відстежується ({}): завершувати вибір можна Esc.".format(error))
            self._timer = None
            return False

    def stop(self):
        timer, self._timer = self._timer, None
        if timer is not None:
            try:
                timer.Stop()
                timer.Dispose()
            except Exception:
                pass

    def _note(self, text):
        if self._log is not None:
            try:
                self._log(text)
            except Exception:
                pass

    def _tick(self, sender, args):
        try:
            down = bool(self._user32.GetAsyncKeyState(VK_RETURN) & 0x8000)
            in_front = int(self._user32.GetForegroundWindow() or 0) == self._handle
            self._armed, finish = step(self._armed, down, in_front)
            if finish:
                from System.Windows.Forms import SendKeys

                self.pressed = True
                SendKeys.Send("{ESC}")
        except Exception as error:
            self._note(u"Enter: {}".format(error))
            self.stop()
