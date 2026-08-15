"""The names export artefacts get on disk.

One writer produces ``<exports>/stage<N>_<slug>_trimmed.mp4`` and six
readers go looking for it: the CLI, ``ui.exports``, ``match_project``,
``ui.server``, the MCP export tools, and ``compare.project_loader``.
Every one of them used to build that string by hand, off a private
``_slugify`` copied into three modules.

The three copies were identical apart from the fallback returned for a
name with no alphanumerics in it -- ``"stage"`` in ``cli`` and
``ui.exports``, ``"match"`` in ``ui.match_exports``. Two readers imported
the ``"match"`` one to build a *stage* filename, so for any stage whose
name slugified to nothing the exporter wrote ``stage1_stage_trimmed.mp4``
and they went looking for ``stage1_match_trimmed.mp4``. Nothing raised:
``compare.project_loader`` reports "no trim" and the grid renders a black
filler tile, which is its documented behaviour for a missing trim.

Hence :func:`stage_file_base`. The fallback stays a parameter of
:func:`slugify` rather than being unified away, because the two values
are both right for their own job -- an unnamed *project* should not
produce ``stage-match.fcpxml``. What was wrong was letting a caller pick.

Not to be confused with :func:`splitsmith.match_model.slugify_filename`,
which strips diacritics via ``unicodedata`` and bounds its output for use
inside a URL-safe id. That is a different function on purpose: folding
these together would rename existing files, since ``Långvägen`` is
``l-ngv-gen`` on disk today and ``langvagen`` under the other rule.
"""

from __future__ import annotations

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")

#: Every per-stage artefact starts with this -- see :func:`stage_file_base`.
_STAGE_FILE_RE = re.compile(r"^stage\d+_")


def slugify(name: str, *, fallback: str) -> str:
    """Filesystem-friendly slug: lowercase, ``[a-z0-9]`` runs joined by ``-``.

    ``fallback`` is returned when nothing survives -- an empty name, or
    one made entirely of punctuation. It is required rather than
    defaulted so a caller has to say which kind of thing it is naming;
    guessing wrong is the bug this module exists for.
    """
    return _SLUG_RE.sub("-", name.lower()).strip("-") or fallback


def stage_file_base(stage_number: int, stage_name: str) -> str:
    """The shared stem of every per-stage export artefact.

    Callers append their own suffix: ``_trimmed.mp4`` for the lossless
    trim, ``_cam_<video_id>_trimmed.mp4`` for a secondary, ``.csv`` /
    ``.fcpxml`` / ``.txt`` for the rest. Writers and readers must call
    this rather than interpolating, or they can drift apart on names
    nobody thought to try.
    """
    return f"stage{stage_number}_{slugify(stage_name, fallback='stage')}"


def match_file_base(project_name: str) -> str:
    """The stem of every match-level export artefact.

    ``ui.match_exports`` appends the output format's extension
    (``.fcpxml`` / ``.xml`` / ``.mp4``) and the YouTube sidecar writer
    appends ``.srt`` / ``.json`` to the same stem. Same contract as
    :func:`stage_file_base`: the writer and every reader call this
    instead of interpolating, so they cannot drift.

    ``fallback='match'`` and not ``'stage'`` -- see the module docstring
    for why that distinction is a parameter rather than unified away.
    """
    return f"{slugify(project_name, fallback='match')}-match"


def is_match_export(filename: str) -> bool:
    """Whether a basename in ``exports/`` is a match-level deliverable.

    Readers that only have a directory listing -- no project name --
    need this: the name a match export was written under encodes the
    ``project_name`` *of that run*, which the caller may since have
    changed, so comparing against ``match_file_base(project.name)``
    would miss the user's own files.

    The ``stage<N>_`` test is load-bearing, not belt-and-braces. A stage
    literally named "Match" slugifies into
    ``stage1_match_trimmed.mp4`` (harmless), but a stage named "The
    Match" produces ``stage1_the-match.fcpxml``, whose stem *does* end
    in ``-match``. Per-stage artefacts always begin ``stage<N>_`` --
    :func:`stage_file_base` guarantees it -- so excluding them first is
    what keeps that stage's FCPXML from being offered as the match's.
    """
    if _STAGE_FILE_RE.match(filename):
        return False
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return stem.endswith("-match")


def stage_number_from_filename(filename: str) -> int | None:
    """The stage number a per-stage artefact belongs to, or ``None``.

    The inverse of :func:`stage_file_base`'s prefix, for readers that hold
    a basename and need to ask a question about its stage -- e.g. "is that
    stage's source still around?". Match-level deliverables and anything
    not written by this module answer ``None``.

    Lives here rather than in the caller for the reason the module
    docstring gives: every reader that takes a name apart has to agree
    with the one writer that put it together.
    """
    m = _STAGE_FILE_RE.match(filename)
    if m is None:
        return None
    return int(m.group(0)[len("stage") : -1])
