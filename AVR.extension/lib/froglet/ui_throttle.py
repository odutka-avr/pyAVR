# -*- coding: utf-8 -*-
"""Two small pieces that keep the "Банк квартир" window responsive when many buttons are pressed in a row.

The window used to rebuild its whole apartment grid on EVERY click of a filter button (and on every
keystroke in a number box), and every rebuild made every card ask the file share for its picture again.
Pressing a dozen toggles queued a dozen rebuilds and hundreds of decodes behind each other on the UI
thread: the window looked frozen.

  Debouncer         does an action once, a moment after the LAST request - a burst of clicks is one
                    rebuild;
  ThumbnailLoader   the pictures of the cards: a picture already loaded is applied at once without
                    touching the file share, one that is being loaded is not loaded twice, and one
                    that no card shows any more is not loaded at all.

Plain Python, no WPF (the window hands in a timer, the decode and the thread pool), so it runs - and
is tested - without Revit. IronPython 2.7 compatible.
"""

import threading


class Debouncer(object):
    """action() runs once, `delay` after the last request(); a request made while one is waiting
    restarts the wait. `make_timer(on_tick)` returns an object with restart() and stop() that calls
    on_tick() once when the delay has passed (a DispatcherTimer in the window)."""

    def __init__(self, make_timer, action, on_error=None):
        self._action = action
        self._on_error = on_error
        self._pending = False
        self._timer = make_timer(self._tick)

    def request(self):
        self._pending = True
        self._timer.restart()

    def cancel(self):
        self._pending = False
        self._timer.stop()

    def run_now(self):
        """The action now, and whatever was waiting is done with it."""
        self.cancel()
        self._call()

    def _tick(self):
        self._timer.stop()
        if not self._pending:
            return
        self._pending = False
        self._call()

    def _call(self):
        try:
            self._action()
        except Exception as error:
            if self._on_error is None:
                raise
            self._on_error(error)


class ThumbnailLoader(object):
    """Pictures for the cards. `stat(path)` is the file's signature, `decode(path, width)` the frozen
    bitmap (or None), `run_in_background(work)` starts work() on a thread-pool thread. Both of them
    run only in the background."""

    def __init__(self, stat, decode, run_in_background):
        self._stat = stat
        self._decode = decode
        self._run = run_in_background
        self._lock = threading.Lock()
        self._cache = {}        # (path, width) -> (signature, bitmap)
        self._waiting = {}      # (path, width) -> [(generation, sticky, apply, post)]
        self._generation = 0

    def new_page(self):
        """The cards are being rebuilt: pictures asked for before no longer have a card to go to
        (except the sticky ones)."""
        with self._lock:
            self._generation += 1

    def request(self, path, width, apply, post, sticky=False):
        """On the UI thread: apply(bitmap) now when the picture is known (no file is touched),
        otherwise later, on the UI thread, through post(callback). `sticky` pictures (the big one
        of an open tab) are loaded even when the cards have been rebuilt since. Returns whether the
        picture was there."""
        key = (path, width)
        with self._lock:
            hit = self._cache.get(key)
            start = False
            if hit is None:
                waiting = self._waiting.get(key)
                if waiting is None:
                    waiting = self._waiting[key] = []
                    start = True
                waiting.append((self._generation, sticky, apply, post))
        if hit is not None:
            apply(hit[1])
            return True
        if start:
            self._run(lambda: self._work(key))
        return False

    def forget_changed(self, signatures):
        """{path: signature} of the files as they are now: pictures of files that changed are dropped."""
        with self._lock:
            for key in list(self._cache):
                current = signatures.get(key[0])
                if current is not None and self._cache[key][0] is not None and self._cache[key][0] != current:
                    del self._cache[key]

    def _wanted(self, waiting):
        return [item for item in waiting if item[1] or item[0] == self._generation]

    def _work(self, key):
        with self._lock:
            if not self._wanted(self._waiting.get(key) or []):
                self._waiting.pop(key, None)     # nobody looks at this picture any more
                return
        path, width = key
        try:
            signature = self._stat(path)
        except Exception:
            signature = None
        bitmap = self._decode(path, width)
        with self._lock:
            waiting = self._waiting.pop(key, [])
            if bitmap is not None:
                self._cache[key] = (signature, bitmap)
            wanted = self._wanted(waiting)
        if bitmap is None:
            return
        for _generation, _sticky, apply, post in wanted:
            post(lambda apply=apply: apply(bitmap))
