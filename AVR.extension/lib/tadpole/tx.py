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


def swallow_warnings(transaction):
    """Suppress warning dialogs for the life of one Transaction.

    Degrades quietly if the interface cannot be implemented on this
    engine — losing the suppression is annoying, not dangerous.
    """
    try:
        from Autodesk.Revit.DB import (
            IFailuresPreprocessor, FailureProcessingResult, FailureSeverity,
        )

        class _Swallow(IFailuresPreprocessor):
            def PreprocessFailures(self, accessor):
                for failure in accessor.GetFailureMessages():
                    if failure.GetSeverity() == FailureSeverity.Warning:
                        accessor.DeleteWarning(failure)
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
