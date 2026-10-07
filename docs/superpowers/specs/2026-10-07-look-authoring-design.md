# Look authoring: an editor, a guide and safe hosted Looks

Status: approved in conversation 2026-10-07 ("Sounds good. Security first.").
Builds on `2026-10-06-rendered-video-first-design.md` (Looks, templates,
identity, transitions, stings, the gallery).

## 1. Goal

Making a Look should be as smooth as picking one. Most people want their
club's colours on the cards; a few want to write their own HTML. Today a
Look is a folder copied into `~/.splitsmith/looks/`, a hand-edited
`look.json`, and templates written against a contract documented only in
the code. Mistakes are silent: a broken user Look is skipped with a log
line and never appears in the gallery. The only way to see a template is
an export or the frame scripts. Hosted accounts have no Looks of their own
at all.

This spec adds, in order: a guide with starter templates and a
`splitsmith looks` command group; Look storage per account (local folders,
hosted rows); a Look editor without code; a template editor (local); and a
sandbox that lets hosted accounts run their own templates.

Out of scope: a drag-and-drop layout editor (the fit policy that keeps text
inside a card would fight it), per-element styling forms, and font choice
(section 7).

## 2. Security

A template is HTML and JavaScript that runs in Chromium on the machine that
renders. Today it loads from a `file://` URL, and so do the shared scripts,
the fonts and the logos; nothing limits what else the page can open. A
template can put any readable file into an iframe or an image and the pixels
land in the video.

- **Locally** that is the user's own machine and files. Custom HTML is
  allowed from the first slice.
- **Hosted**, it would expose the container: the process environment and its
  secrets, other accounts' files on the same disk. So hosted accounts get
  Looks **without custom HTML** first: a hosted Look is a manifest (base Look,
  palette, accent series, each slot's variant) drawn by the shipped templates.
  No account-supplied code runs.
- **Hosted custom HTML** ships only with the sandbox (slice 5), after its own
  security review:
  - Templates load from a virtual origin (`https://look.invalid/`) that the
    rasterizer answers request by request through Playwright routing; no
    `file://` navigation, and the page has no `file://` reach at all.
  - The route serves only the Look's own files, the shared engine scripts,
    the bundled fonts and the requesting account's logos, by allowlist. Every
    other request, including any network host, is aborted.
  - Limits: template and asset sizes, a wall-clock budget per frame and per
    template, a frame-count ceiling, one browser context per template that is
    closed after use.
  - The same loader then serves local templates too, so there is one path
    and the local experience matches what hosted renders.
  - Tests that try to read `/proc/self/environ`, another account's logo, a
    path outside the Look, and an outside host, and assert each is blocked.

## 3. Look storage

One interface, two backends, mirroring export presets
(`export_presets.json` locally, a table hosted):

- `LookStore.list() / get(name) / save(name, manifest, files) / delete(name)`.
- **Local**: the folders under `~/.splitsmith/looks/` as today. `save` writes
  `look.json` and any template files; a Look made in the editor is an ordinary
  folder the user can open and edit.
- **Hosted**: a `user_looks` table per account (`user_id`, `name`, `manifest`
  JSON, `updated_at`), never a `state_docs` kind (Looks are per user, not per
  match, like presets). Manifest only until slice 5. A render materializes the
  manifest into a temporary Look folder whose templates resolve to the base
  Look's (`template_for` already falls back to the shipped default).
- Validation is `LookManifest` everywhere; the editor and `looks check` show
  its errors with the field named, instead of a skipped Look and a log line.
- A user Look may shadow a shipped name locally (as today); hosted names may
  not, so a shipped Look always means the same thing on the server.
- Name rules as today (`^[a-z][a-z0-9_-]{0,31}$`); label free text.

The catalog (`GET /api/looks`) lists the caller's Looks after the shipped
ones in both modes, with `source: "user"` and `editable: true`.

## 4. Commands and the guide (slice 1)

- `splitsmith looks list`: installed Looks, their source and slots.
- `splitsmith looks new <name> [--from <look>] [--starter still|animated|lower-third|sting]`:
  a copy of a Look, or a minimal Look from a starter, in the user Looks folder.
- `splitsmith looks check <name>`: validates the manifest, loads every
  template in Chromium against sample data (a long stage name, no logo, two
  shooters, an animated duration) and reports script errors, missing files,
  fonts outside the bundled faces, text that overflows the card, and an
  animation whose `poster()` lands outside `duration()`. Exit code 1 on any
  error.
- `splitsmith looks preview <name> [--out DIR] [--stage <project-dir>:<n>]`:
  renders every slot and sting of the Look to PNGs (and a contact sheet), on
  demo footage or a real stage.
- **The guide**: `docs/looks/authoring.md`, linked from the Advanced section
  and printed by `looks new`. It covers the folder and `look.json`, slots and
  variants, `window.splitsmith` (theme, data, size, fps, engine, assets) with
  the exact shape per slot, animation (`duration`, `seek`, `poster`), stings,
  identity and logos, the bundled fonts, and how `looks check` and `looks
  preview` help. Each section ends with a screenshot.
- **Starters**: four short, commented templates under
  `data/looks/_starters/`: a still card, an animated card, a lower third, a
  sting.

## 5. The Look editor (slice 3)

Where: an **Advanced** row at the bottom of the Export page's Look group:
"Edit Look…" (on a user Look), "Duplicate Look…" (on any), and the guide link.
Both open a full-width editor sheet.

The sheet:

- **Header**: Look name and label, Save, Cancel, Delete (user Looks).
- **Palette**: a colour picker per token, grouped by role (ink, surface,
  accent, splits), each with a one-line description of where it shows; the
  accent series as a row of swatches with add and remove.
- **Card styles**: per slot, the default variant (Default, Rise, ...).
- **Preview strip**: the title page, slate, lower third, summary, closing card
  and a sting, rendered on the first selected stage of the current match, in
  the draft (unsaved) Look; a time slider for animated cards and stings.
  Endpoint: `POST /api/looks/draft-preview` with the draft manifest, a card
  and a time, returning a PNG; cached by content.
- **Errors inline**: a missing token, an unreadable colour, a variant the base
  Look lacks; Save is disabled until they are fixed.

## 6. The template editor (slice 4, local; hosted after slice 5)

Inside the editor sheet, a **Templates** tab per slot:

- CodeMirror 6 (new dependency, approved) with HTML, CSS and JS highlighting.
- The same preview with the time slider, refreshed on a short debounce.
- A **data panel** showing exactly what the template receives, with sample
  cases to switch between (no logo, two shooters, a long stage name, a
  sting).
- Script errors from the page shown under the editor with their line.
- "Open folder" to edit in the user's own editor; the preview reloads when the
  files change.
- "Start from a starter" inserts one of the four starters.

## 7. Fonts

`look.json` names fonts, but the renderer only ever uses its two bundled
faces (Antonio as `Splitsmith Display`, JetBrains Mono as `Splitsmith Mono`).
The editor offers no font choice. The guide says so, `looks check` reports a
template that names another family, and the unused `fonts` field stays
accepted for compatibility. More faces would mean bundling more OFL fonts in
the wheel, which is a separate decision.

## 8. Testing

- Storage: both backends behind one contract test; hosted rows scoped per
  account; a hosted Look cannot shadow a shipped name.
- Commands: `looks new` makes a Look that `looks check` passes; `check` fails
  with a named error for each sample defect; `preview` writes one PNG per slot.
- Editor: the draft preview keys on the draft; Save round-trips; errors block
  Save; the Advanced row shows Edit only on user Looks.
- Sandbox (slice 5): the blocked-request tests in section 2, plus a template
  that loops forever and one that requests a huge image, each stopped within
  its limit.

## 9. Slices

1. The guide, starters and `splitsmith looks` commands.
2. `LookStore`: local folders and hosted `user_looks` rows (manifest only).
3. The Look editor (palette, card styles, preview strip), both modes.
4. The template editor, local.
5. The sandboxed template loader, then hosted custom HTML.

Wireframes: `https://art.urdr.dev/look-authoring-wireframes`. Epic #1267; slices #1262 to #1266.
Found while building slice 1: a long name runs off the shipped cards (#1268).
