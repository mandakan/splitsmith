"""The names export artefacts get on disk.

One writer produces ``<exports>/stage<N>_<slug>_trimmed.mp4`` and six
readers go looking for it: the CLI, ``ui.exports``, ``ui.project``,
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
