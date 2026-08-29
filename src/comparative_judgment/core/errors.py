"""Named refusals.

Every error here corresponds to a refusal the specification requires by name. They
are separate types rather than one error with a message because the acceptance
criteria assert *which* refusal fired, and a message string is not a contract.
"""

from __future__ import annotations


class ComparativeJudgmentError(Exception):
    """Base for every refusal this package makes."""


class StoreSchemaError(ComparativeJudgmentError):
    """The store's schema version is not one this build understands.

    Refuses to write rather than guessing at an unfamiliar layout: a store is
    append-only and shared across sessions, so a wrong guess is unrecoverable.
    """


class StoreExistsError(ComparativeJudgmentError):
    """A store was asked to be created where one already exists.

    Creating writes a fresh `meta.json` and an empty `cuts.json`, so running it
    over a populated store would destroy the three band cuts — the only absolute
    judgments the tool ever asks for — under a command whose name reads as safe.
    """


class NothingToJudgeError(ComparativeJudgmentError):
    """A judgment was offered when the session had no pair to judge.

    A refusal like every other one here rather than a bare `ValueError`: an error
    that is not one of these escapes the CLI's handler and reaches the user as a
    traceback, which is the one thing this module exists to prevent.
    """


class SeverityWriteError(ComparativeJudgmentError):
    """The severity file could not be written where it was asked to go.

    A missing directory or a permission refusal is an ordinary mistake, not a
    crash, and the last step of a session that may have cost several hundred
    judgments is a poor place to hand someone a stack trace.
    """


class RetractionError(ComparativeJudgmentError):
    """A retraction names no live comparison.

    The log's hash is published as provenance in every severity file, so a record
    referring to nothing is not merely inert: it changes the fingerprint of a
    history without changing what that history says.
    """


class FindingsFileError(ComparativeJudgmentError):
    """The findings document could not be read or parsed.

    Missing, unreadable, or not valid YAML. Separate from
    :class:`FindingSchemaError`, which is about a document that parsed and then
    said the wrong thing.
    """


class NoStorePathError(ComparativeJudgmentError):
    """No store path was supplied.

    There is deliberately no implicit fallback. A tool that writes findings text
    somewhere by default will eventually do so on a machine where those findings
    are real and the person did not choose the location.
    """


class FindingSchemaError(ComparativeJudgmentError):
    """A findings entry is missing a required key, or carries a forbidden one.

    `severity` is the forbidden one: it is joined from a separate file by id, and
    the tool never mutates the document it reads.
    """


class UnknownItemError(ComparativeJudgmentError):
    """A comparison or cut references an item the store does not hold."""


class DisconnectedComparisonsError(ComparativeJudgmentError):
    """Scale values were requested across items with no comparison path between them.

    Bradley-Terry estimates *differences*, so two groups never compared against
    each other have independent and arbitrary origins. Their values are not
    comparable, and no amount of further fitting makes them so.
    """


class FitDidNotConvergeError(ComparativeJudgmentError):
    """The fit hit its iteration cap.

    Emits nothing rather than returning whatever value it had reached, which
    would make the result depend on the cap instead of on the judgments.
    """


class CutError(ComparativeJudgmentError):
    """A band cut is missing, malformed, or its anchor pair has inverted.

    Inversion means a refit reordered the two findings the cut was drawn
    between: the scale changed materially in that region and a human should
    look, so it is reported rather than silently re-sorted.
    """
