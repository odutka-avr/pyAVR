# -*- coding: utf-8 -*-
"""Transaction helpers.

One Transaction for a whole run, with a SubTransaction per element.

Why this shape:

  - ONE Transaction means one commit and one document regeneration, so a
    few thousand creations take seconds rather than minutes. A separate
    Transaction per element regenerates the document every time.
  - A SubTransaction still isolates failures. Rolling one back discards
    only that element's changes and leaves the outer Transaction usable,
    which is what makes it safe to carry on after a rejected element.
  - Never carry on inside a transaction scope that has thrown WITHOUT
    rolling it back first. That is what crashed Revit early on.

Also swallows warning dialogs, because a few thousand creations will
otherwise stack up modal prompts and stall the run.
"""

from Autodesk.Revit.DB import (
    Transaction, SubTransaction, TransactionGroup,
)


def _join_failure_ids():
    """The definition ids of Revit's join failures ("Can't keep elements joined" and its kin: every
    BuiltInFailures.JoinElementsFailures member named CannotKeep*, CannotJoin* or Joining*), as Guid strings."""
    ids = set()
    try:
        from Autodesk.Revit.DB import BuiltInFailures
        joins = BuiltInFailures.JoinElementsFailures
        for name in dir(joins):
            if not (name.startswith('CannotKeep') or name.startswith('CannotJoin') or name.startswith('Joining')):
                continue
            fid = getattr(joins, name, None)
            if fid is not None and hasattr(fid, 'Guid'):
                ids.add(u'{0}'.format(fid.Guid))
    except Exception:
        pass
    return ids


def swallow_warnings(transaction):
    """Suppress warning dialogs for the life of one Transaction.

    AND UNJOIN WHAT CANNOT STAY JOINED (user, 2026-10-16: "ще в процесі з'являється таке повідомлення [Can't keep
    elements joined] і я маю щоразу натискати unjoin elements, треба щоб без нього було"): an ERROR of Revit's join
    family - or any error that offers "Unjoin Elements" - is resolved with Unjoin Elements, the button she pressed
    every time, and the transaction goes on. Any other error is left to Revit as before: a resolution that deletes
    elements is never picked here.

    Degrades quietly if the interface cannot be implemented on this
    engine — losing the suppression is annoying, not dangerous.
    """
    try:
        from Autodesk.Revit.DB import (
            IFailuresPreprocessor, FailureProcessingResult, FailureSeverity, FailureResolutionType,
        )
        # "Unjoin Elements" is DetachElements in Revit's enum (2024 has no UnjoinElements), and the default resolution
        # of "Can't keep elements joined"
        detach = getattr(FailureResolutionType, 'DetachElements', None)
        join_ids = _join_failure_ids()

        def caption(failure):
            try:
                return u'{0}'.format(failure.GetDefaultResolutionCaption() or u'')
            except Exception:
                return u''

        def is_join(failure):
            try:
                if u'{0}'.format(failure.GetFailureDefinitionId().Guid) in join_ids:
                    return True
            except Exception:
                pass
            return caption(failure).lower().startswith(u'unjoin')

        class _Swallow(IFailuresPreprocessor):
            def PreprocessFailures(self, accessor):
                resolved = False
                for failure in accessor.GetFailureMessages():
                    severity = failure.GetSeverity()
                    if severity == FailureSeverity.Warning:
                        accessor.DeleteWarning(failure)
                        continue
                    if severity == FailureSeverity.Error and is_join(failure):
                        try:
                            if not caption(failure).lower().startswith(u'unjoin'):
                                # the default is something else: Unjoin (detach) where it is offered, else leave it
                                if detach is None or not failure.HasResolutionOfType(detach):
                                    continue
                                failure.SetCurrentResolutionType(detach)
                            if failure.HasResolutions():
                                accessor.ResolveFailure(failure)
                                resolved = True
                        except Exception:
                            pass
                if resolved:
                    return FailureProcessingResult.ProceedWithCommit
                return FailureProcessingResult.Continue

        options = transaction.GetFailureHandlingOptions()
        options = options.SetForcedModalHandling(False)
        options = options.SetClearAfterRollback(True)
        options = options.SetFailuresPreprocessor(_Swallow())
        transaction.SetFailureHandlingOptions(options)
        return True
    except Exception:
        return False


def start(doc, name):
    """Begin the single Transaction for a run."""
    transaction = Transaction(doc, name)
    transaction.Start()
    swallow_warnings(transaction)
    return transaction


def finish(transaction):
    if transaction.HasStarted() and not transaction.HasEnded():
        transaction.Commit()


def abort(transaction):
    if transaction.HasStarted() and not transaction.HasEnded():
        transaction.RollBack()


class Attempt(object):
    """A SubTransaction scope for one element.

    Commits on success, rolls back on any exception, and never lets the
    exception reach the outer Transaction unhandled.

        with Attempt(doc) as attempt:
            attempt.result = Wall.Create(...)
        if not attempt.ok:
            record(attempt.error)
    """

    def __init__(self, doc):
        self.doc = doc
        self.sub = None
        self.ok = False
        self.error = None
        self.result = None

    def __enter__(self):
        self.sub = SubTransaction(self.doc)
        self.sub.Start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            try:
                self.sub.Commit()
                self.ok = True
            except Exception as exc:
                self._rollback()
                self.error = exc
        else:
            self._rollback()
            self.error = exc_value
        return True     # swallow — the caller inspects .ok and .error

    def _rollback(self):
        try:
            if self.sub.HasStarted() and not self.sub.HasEnded():
                self.sub.RollBack()
        except Exception:
            pass


# ----------------------------------------------------------------------
# a group, for a run that opens more than one Transaction
# ----------------------------------------------------------------------

def group(doc, name):
    """Begin a TransactionGroup.

    A run that writes two different kinds of element opens two
    Transactions, which without this would be two separate undo steps.
    Assimilating merges them into one, so the promise that a single
    Ctrl+Z reverses the whole run still holds.
    """
    handle = TransactionGroup(doc, name)
    handle.Start()
    return handle


def assimilate(handle):
    """Commit the group as ONE undo step."""
    if handle.HasStarted() and not handle.HasEnded():
        handle.Assimilate()


def abort_group(handle):
    if handle.HasStarted() and not handle.HasEnded():
        handle.RollBack()
