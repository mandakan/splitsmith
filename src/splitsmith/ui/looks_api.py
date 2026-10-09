"""``GET /api/looks`` and ``GET /api/looks/{name}/preview/{file}`` (spec
2026-10-06 section 4, issue #1246).

The catalog is :func:`splitsmith.looks.look_catalog`, read per request (a
user may drop a Look into ``~/.splitsmith/looks`` while the app runs). A
preview file is served only for an installed Look (or ``_shipped``, the
shipped default a user Look borrows from, or ``_transitions``, the xfade
families' loops, #1259), by a bare
``<slot>-<variant>.png`` / ``.webp`` name inside that ``preview/``
directory; anything else is the same 404, which is what keeps the
``{file}`` parameter harmless hosted (``route_scope.HOSTED_CONFINED_ROUTES``).

``GET / PUT / DELETE /api/looks/{name}`` read and write the caller's own
Looks through ``state.looks`` (issue #1263): the Looks folder locally, the
account's ``user_looks`` rows hosted (the auth gate pins the tenant and
its Looks provider before this router runs). A shipped Look is never one
of them: it is not returned, changed or deleted here.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..composition import XFADE_FAMILIES
from ..fonts import FONTS
from ..identity import LOGO_MAX_BYTES
from ..look_brand import BrandError, save_brand_logo
from ..look_store import (
    MAX_LABEL_LENGTH,
    TEMPLATE_SLOTS,
    FolderLookStore,
    LookStore,
    LookStoreError,
    StoredLook,
    StoredLookBody,
    TemplateEdit,
    apply_template_edits,
    body_from_manifest,
    check_name,
    draft_look,
    is_shipped_name,
    template_file,
)
from ..look_tools import (
    EDITOR_STARTERS,
    STARTERS,
    LookToolError,
    check_folder,
    outdated_copies,
    refresh_templates,
    sample_contexts,
)
from ..looks import (
    BRAND_DIR,
    BRAND_FILE_RE,
    DEFAULT_LOOK,
    DEFAULT_VARIANT,
    MANIFEST_FILE,
    PREVIEW_DIR,
    TRANSITIONS_OWNER,
    Look,
    LookError,
    load_look,
    look_catalog,
    preview_owner_root,
    read_look,
    shipped_looks_dir,
    user_looks_dir,
    variants_for,
)
from ..overlay_raster import ChromiumRasterizer, RasterizerUnavailableError
from ..own_fonts import MAX_FONT_BYTES, OwnFont, OwnFontError, list_fonts, own_font_path, save_font
from ..runtime import runtime

router = APIRouter()

_FILE_RE = re.compile(r"^[a-z0-9_-]+\.(png|webp)$")
_MEDIA = {".png": "image/png", ".webp": "image/webp"}


class TransitionDirectionInfo(BaseModel):
    name: str
    kind: str


class TransitionFamilyInfo(BaseModel):
    """One xfade family as the gallery shows it (issue #1259): a tile, its
    directions (the first is what the tile selects) and a looping preview."""

    id: str
    label: str
    help: str
    preview: str | None
    directions: list[TransitionDirectionInfo]


def transition_catalog() -> list[TransitionFamilyInfo]:
    """``composition.XFADE_FAMILIES`` with each family's preview URL."""
    previews = shipped_looks_dir() / TRANSITIONS_OWNER / PREVIEW_DIR
    out: list[TransitionFamilyInfo] = []
    for family in XFADE_FAMILIES:
        file = f"{family.id}.webp"
        out.append(
            TransitionFamilyInfo(
                id=family.id,
                label=family.label,
                help=family.help,
                preview=(
                    f"/api/looks/{TRANSITIONS_OWNER}/preview/{file}" if (previews / file).is_file() else None
                ),
                directions=[TransitionDirectionInfo(name=d.name, kind=d.kind) for d in family.directions],
            )
        )
    return out


class FontInfo(BaseModel):
    """One bundled face a Look may choose (#1272), with the URL its sample
    loads from."""

    id: str
    label: str
    role: str
    help: str
    url: str


def font_catalog() -> list[FontInfo]:
    return [
        FontInfo(id=f.id, label=f.label, role=f.role, help=f.help, url=f"/api/looks/fonts/{f.id}")
        for f in FONTS
    ]


@router.get("/api/looks")
def get_looks() -> dict[str, Any]:
    return {
        "looks": [info.model_dump() for info in look_catalog()],
        "transitions": [info.model_dump() for info in transition_catalog()],
        "fonts": [info.model_dump() for info in font_catalog()],
    }


@router.get("/api/looks/fonts/{font_id}")
def get_font(font_id: str) -> FileResponse:
    """A bundled face's file, by its catalog id only (the editor's samples)."""
    face = next((f for f in FONTS if f.id == font_id), None)
    if face is None:
        raise HTTPException(status_code=404, detail="not found")
    path = Path(str(resources.files("splitsmith.data").joinpath("fonts").joinpath(face.file)))
    return FileResponse(path, media_type="font/ttf", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/looks/{name}/preview/{file}")
def get_look_preview(name: str, file: str) -> FileResponse:
    root = preview_owner_root(name) if _FILE_RE.match(file) else None
    if root is None:
        raise HTTPException(status_code=404, detail="not found")
    path = root / PREVIEW_DIR / file
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        path, media_type=_MEDIA[path.suffix], headers={"Cache-Control": "public, max-age=3600"}
    )


def _store(request: Request) -> LookStore:
    return request.app.state.splitsmith_state.looks


def _name(name: str) -> str:
    try:
        return check_name(name)
    except LookStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/api/looks/{name}", response_model=StoredLook)
async def get_own_look(name: str, request: Request) -> StoredLook:
    stored = await _store(request).get(_name(name))
    if stored is None:
        raise HTTPException(status_code=404, detail="not found")
    return stored


@router.put("/api/looks/{name}", response_model=StoredLook)
async def put_own_look(name: str, body: StoredLookBody, request: Request, response: Response) -> StoredLook:
    store = _store(request)
    created = await store.get(_name(name)) is None
    try:
        stored = await store.put(name, body)
    except LookStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if created:
        response.status_code = 201
    return stored


@router.delete("/api/looks/{name}", status_code=204)
async def delete_own_look(name: str, request: Request) -> Response:
    store = _store(request)
    if await store.get(_name(name)) is None:
        raise HTTPException(status_code=404, detail="not found")
    await store.delete(name)
    return Response(status_code=204)


class DuplicateLookRequest(BaseModel):
    source: str
    #: What the person named it on the way in; the copy's own label otherwise.
    label: str | None = Field(default=None, max_length=MAX_LABEL_LENGTH)


@router.post("/api/looks/{name}/duplicate", status_code=201, response_model=StoredLook)
async def duplicate_look(name: str, req: DuplicateLookRequest, request: Request) -> StoredLook:
    """A new Look ``name`` copied from the installed Look ``source`` (#1264).
    Locally the copy is a folder with the source's templates (``looks new
    --from``); hosted it is a manifest whose base is the source's shipped
    Look, its colours and styles copied."""
    store = _store(request)
    if await store.get(_name(name)) is not None:
        raise HTTPException(status_code=409, detail=f"a Look named {name!r} already exists")
    try:
        source = load_look(req.source)
    except LookError:
        raise HTTPException(status_code=404, detail=f"no Look named {req.source!r}") from None
    try:
        if isinstance(store, FolderLookStore):
            from ..look_tools import LookToolError, new_look

            try:
                new_look(name, from_look=req.source)
            except LookToolError as exc:
                raise LookStoreError(str(exc)) from None
            stored = await store.get(name)
            assert stored is not None  # new_look loads the folder before it returns
            if req.label:
                return await store.put(name, stored.body.model_copy(update={"label": req.label}))
            return stored
        body = body_from_manifest(source.manifest)
        base = req.source if is_shipped_name(req.source) else body.base
        update: dict[str, object] = {"base": base}
        if req.label:
            update["label"] = req.label
        return await store.put(name, body.model_copy(update=update))
    except LookStoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


# --- the template editor (#1265), local only ---------------------------------
#
# Every route below is in ``route_scope.LOCAL_ONLY_ROUTES``: a template is
# code, and hosted runs none of an account's: custom templates are
# desktop only, by decision (Chromium's OS sandbox cannot start on Railway).

#: Swapped by tests; ``looks check``'s prober.
prober_factory = ChromiumRasterizer


def _own_look(name: str) -> Look:
    """The user's own Look folder ``name``, strictly read; 404 otherwise."""
    folder = user_looks_dir() / _name(name)
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"no Look of yours named {name!r}")
    try:
        return read_look(folder, "user")
    except LookError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


class TemplateInfo(BaseModel):
    slot: str
    variant: str
    #: The Look's own file, or the shipped default's it borrows.
    file: str
    own: bool
    content: str


class StarterInfo(BaseModel):
    name: str
    content: str


class TemplatesPayload(BaseModel):
    templates: list[TemplateInfo]
    starters: list[StarterInfo]


@router.get("/api/looks/{name}/templates", response_model=TemplatesPayload)
def list_templates(name: str) -> TemplatesPayload:
    look = _own_look(name)
    shipped = read_look(shipped_looks_dir() / DEFAULT_LOOK, "shipped")
    templates: list[TemplateInfo] = []
    for slot in TEMPLATE_SLOTS:
        for variant in variants_for(look, slot):
            own = look.own_template(slot, variant)
            path = own or shipped.own_template(slot, variant)
            if path is None:
                continue
            templates.append(
                TemplateInfo(
                    slot=slot,
                    variant=variant,
                    file=path.name,
                    own=own is not None,
                    content=path.read_text(encoding="utf-8"),
                )
            )
    starters_dir = shipped_looks_dir() / "_starters"
    starters = [
        StarterInfo(name=starter, content=(starters_dir / file).read_text(encoding="utf-8"))
        for starter, file in sorted(STARTERS.items())
        if starter in EDITOR_STARTERS
    ]
    return TemplatesPayload(templates=templates, starters=starters)


@router.put("/api/looks/{name}/templates", response_model=TemplateInfo)
def save_template(name: str, edit: TemplateEdit) -> TemplateInfo:
    """Write one template into the Look's folder, naming it in ``look.json``
    when the slot was borrowed; a folder that no longer reads is rolled back."""
    look = _own_look(name)
    manifest_path = look.root / MANIFEST_FILE
    before = manifest_path.read_text(encoding="utf-8")
    target = look.root / template_file(look.manifest.slots, edit.slot, edit.variant)
    previous = target.read_text(encoding="utf-8") if target.is_file() else None
    try:
        apply_template_edits(look.root, [edit])
        read_look(look.root, "user")
    except (LookError, OSError, ValueError) as exc:
        manifest_path.write_text(before, encoding="utf-8")
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(previous, encoding="utf-8")
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return TemplateInfo(
        slot=edit.slot, variant=edit.variant, file=target.name, own=True, content=edit.content
    )


class SampleCase(BaseModel):
    case: str
    #: ``window.splitsmith`` as the template receives it, without the
    #: engine stylesheet (fonts, long and the same for every case).
    context: dict[str, Any]


@router.get("/api/looks/{name}/samples")
def template_samples(name: str, slot: str, variant: str = DEFAULT_VARIANT) -> dict[str, list[SampleCase]]:
    try:
        TemplateEdit(slot=slot, variant=variant, content="")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    look = _own_look(name)
    with tempfile.TemporaryDirectory(prefix="looks-samples-") as work:
        contexts = sample_contexts(look, slot, variant, Path(work))
        cases = []
        for case, context in contexts:
            dumped = context.model_dump(mode="json")
            dumped["engine"] = {k: v for k, v in dumped["engine"].items() if k != "css"}
            cases.append(SampleCase(case=case, context=dumped))
    return {"cases": cases}


class CheckRequest(BaseModel):
    draft: StoredLookBody | None = None
    templates: list[TemplateEdit] = Field(default_factory=list, max_length=32)


#: Bump when the same files would check differently.
CHECK_CACHE_VERSION = 1


def _folder_digest(root: Path) -> str:
    """Every file of a Look folder, by relative path and bytes."""
    digest = hashlib.sha256(f"v{CHECK_CACHE_VERSION}".encode())
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


@router.post("/api/looks/{name}/check")
def check_draft(name: str, req: CheckRequest) -> dict[str, Any]:
    """``looks check`` on the Look as the editor holds it, unsaved. The saved
    Look (no draft, no template text: the Export page's preflight, #1276) is
    cached by the folder's content, so choosing a Look again launches no
    browser and any edit to its files checks it again."""
    look = _own_look(name)
    cached: Path | None = None
    if req.draft is None and not req.templates:
        cached = runtime().cache_dir / "look-check" / f"{_folder_digest(look.root)}.json"
        if cached.is_file():
            try:
                return json.loads(cached.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
    result = _check(name, look, req)
    if cached is not None:
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(result), encoding="utf-8")
        except OSError:
            pass
    return result


def _check(name: str, look: Look, req: CheckRequest) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="looks-check-draft-") as work:
        try:
            draft = draft_look(look, req.draft, Path(work), req.templates)
        except LookStoreError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        try:
            with prober_factory() as prober:
                report = check_folder(name, draft.root, "user", prober=prober)
        except RasterizerUnavailableError as exc:
            raise HTTPException(
                status_code=503, detail="checking needs a browser: Playwright could not launch Chromium"
            ) from exc
    return {
        "items": [{"subject": i.subject, "level": i.level, "message": i.message} for i in report.items],
        "errors": report.errors,
        "warnings": report.warnings,
    }


class OutdatedTemplates(BaseModel):
    #: The Look's unedited copies of an older shipped template.
    files: list[str]


@router.get("/api/looks/{name}/outdated", response_model=OutdatedTemplates)
def outdated_templates(name: str) -> OutdatedTemplates:
    """Which of your Look's templates are unedited copies of an older
    shipped version (a Look duplicated before a release): the Export
    page offers to draw the current ones instead. No browser."""
    return OutdatedTemplates(files=list(outdated_copies(_own_look(name))))


@router.post("/api/looks/{name}/refresh", response_model=OutdatedTemplates)
def refresh_look_templates(name: str) -> OutdatedTemplates:
    """``looks refresh``: drop the Look's unedited copies of shipped
    templates, old or current, so those cards draw the shipped ones. An
    edited file is never touched. Answers the files removed."""
    _own_look(name)
    try:
        return OutdatedTemplates(files=list(refresh_templates(name)))
    except LookToolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/api/looks/{name}/reveal")
def reveal_look(name: str) -> dict[str, str]:
    """Open the Look's folder in the system's file manager."""
    from . import server

    look = _own_look(name)
    server._reveal_in_file_manager(look.root)
    return {"revealed": str(look.root)}


# --- a Look's own fonts (#1272 step 2) ---------------------------------------
#
# Local only, like the template routes: hosted has no account assets for a
# font yet, and ``db.looks`` refuses a Look that names one.


class OwnFontInfo(BaseModel):
    #: What ``fonts`` in the Look's body names it by.
    value: str
    family: str
    #: Where the editor's sample loads it from.
    url: str


def _font_info(name: str, font: OwnFont) -> OwnFontInfo:
    return OwnFontInfo(value=font.value, family=font.family, url=f"/api/looks/{name}/fonts/{font.file}")


@router.get("/api/looks/{name}/fonts", response_model=list[OwnFontInfo])
def list_own_fonts(name: str) -> list[OwnFontInfo]:
    look = _own_look(name)
    return [_font_info(look.name, font) for font in list_fonts(look.root)]


@router.post("/api/looks/{name}/fonts", status_code=201, response_model=OwnFontInfo)
def upload_own_font(name: str, file: Annotated[UploadFile, File()]) -> OwnFontInfo:
    """Store a TTF or OTF in the Look's ``fonts/`` folder (``own_fonts``):
    sniffed and opened with FreeType, never trusted by its name."""
    look = _own_look(name)
    # Sync on purpose: FastAPI runs it on its threadpool, so FreeType
    # opening the file never blocks the event loop.
    data = file.file.read(MAX_FONT_BYTES + 1)
    if len(data) > MAX_FONT_BYTES:
        raise HTTPException(status_code=413, detail="The font is larger than 2 MB.")
    try:
        font = save_font(look.root, data)
    except OwnFontError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _font_info(look.name, font)


@router.get("/api/looks/{name}/fonts/{file}")
def get_own_font(name: str, file: str) -> FileResponse:
    """One of the Look's own font files, by its content name only."""
    look = _own_look(name)
    path = own_font_path(look.root, file)
    if path is None:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        path,
        media_type="font/otf" if path.suffix == ".otf" else "font/ttf",
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


# --- your brand (the branding work) -------------------------------------------
#
# Local only, like the font routes: hosted Looks have no file store yet.


class BrandLogoInfo(BaseModel):
    #: What ``brand.logo`` in the Look's body names it by.
    logo: str
    #: Where the editor shows it from.
    url: str


@router.post("/api/looks/{name}/brand-logo", status_code=201, response_model=BrandLogoInfo)
def upload_brand_logo(name: str, file: Annotated[UploadFile, File()]) -> BrandLogoInfo:
    """Store a PNG, JPEG or WebP in the Look's ``brand/`` folder
    (``look_brand``): sniffed and sized, never trusted by its name. Saving
    the Look with ``brand.logo`` naming it is what puts it on the cards."""
    look = _own_look(name)
    data = file.file.read(LOGO_MAX_BYTES + 1)
    try:
        logo = save_brand_logo(look.root, data)
    except BrandError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return BrandLogoInfo(logo=logo, url=f"/api/looks/{look.name}/brand/{logo}")


@router.get("/api/looks/{name}/brand/{file}")
def get_brand_logo(name: str, file: str) -> FileResponse:
    """One of the Look's brand logos, by its content name only."""
    look = _own_look(name)
    if not BRAND_FILE_RE.fullmatch(file):
        raise HTTPException(status_code=404, detail="not found")
    path = look.root / BRAND_DIR / file
    if path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    media = {".png": "image/png", ".webp": "image/webp"}.get(path.suffix, "image/jpeg")
    return FileResponse(
        path,
        media_type=media,
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


__all__ = ["router"]
