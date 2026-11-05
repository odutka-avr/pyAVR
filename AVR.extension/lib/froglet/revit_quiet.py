# -*- coding: utf-8 -*-
"""Revit's own messages do not disturb a Froglet run.

While a Froglet ExternalEvent works (`quiet(uiapp)` is wrapped round every one of them, see
apartment_bank_window._CallbackExternalEventHandler):

* a failure (the warnings and errors Revit would show in its own dialog) is settled by itself: a warning is deleted - the
  same as pressing OK; an error that offers "Unjoin Elements" is resolved with it, else with its default resolution, else
  with the first one that works (apartment_group_placement._resolve_failures_accessor); an error nothing resolves rolls the
  transaction back silently, as Revit would anyway after its dialog;
* a Revit dialog (a TaskDialog or a message box) is answered with OK at once.

Every closed dialog and settled failure goes to the Froglet log, so that nothing is lost without a trace. Our own messages
are not Revit dialogs (pyRevit's forms.alert), so they stay.
"""

_state = {"depth": 0, "uiapp": None, "failures": None, "dialog": None}


def _log(text):
    try:
        import apartment_group_placement as agp

        agp._log(text)
    except Exception:
        pass


def _on_failures(sender, args):
    """Application.FailuresProcessing: settle every failure of the transaction that is being committed."""
    try:
        import apartment_group_placement as agp

        args.SetProcessingResult(agp._resolve_failures_accessor(args.GetFailuresAccessor()))
    except Exception as error:
        _log(u"[REVIT QUIET] failures not settled: {}".format(error))


def _on_dialog(sender, args):
    """UIApplication.DialogBoxShowing: a Revit dialog is answered with OK (1) before the user sees it."""
    try:
        what = u"{} {}".format(getattr(args, "DialogId", u""), getattr(args, "Message", u"") or u"").strip()
        args.OverrideResult(1)
        _log(u"[REVIT QUIET] dialog closed with OK: {}".format(what))
    except Exception as error:
        _log(u"[REVIT QUIET] dialog not closed: {}".format(error))


class quiet(object):
    """`with quiet(uiapp):` - Revit's failures and dialogs are settled by themselves inside the block. Nested uses share one
    subscription; it never raises (a run does not depend on it)."""

    def __init__(self, uiapp):
        self._uiapp = uiapp

    def __enter__(self):
        if _state["depth"] == 0:
            try:
                uiapp = self._uiapp
                uiapp.Application.FailuresProcessing += _on_failures
                uiapp.DialogBoxShowing += _on_dialog
                _state["uiapp"] = uiapp
                _state["failures"], _state["dialog"] = _on_failures, _on_dialog
            except Exception as error:
                _log(u"[REVIT QUIET] could not subscribe: {}".format(error))
        _state["depth"] += 1
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        _state["depth"] = max(0, _state["depth"] - 1)
        if _state["depth"] == 0 and _state["uiapp"] is not None:
            uiapp = _state["uiapp"]
            try:
                uiapp.Application.FailuresProcessing -= _state["failures"]
            except Exception:
                pass
            try:
                uiapp.DialogBoxShowing -= _state["dialog"]
            except Exception:
                pass
            _state["uiapp"] = _state["failures"] = _state["dialog"] = None
        return False
