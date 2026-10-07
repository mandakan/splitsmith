/**
 * LookEditor -- a Look without code (spec 2026-10-07 s5, #1264): its
 * palette, its default card styles and, on the desktop, where its
 * templates live; the draft previewed on this match's stage before Save.
 * Rules live in ``lib/lookEditor``; this maps them to primitives.
 */
import { Loader2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import { Segmented } from "@/components/ui/Segmented";
import { Sheet } from "@/components/ui/Sheet";
import { useConfirm } from "@/components/useConfirm";
import {
  ApiError,
  api,
  type LookInfo,
  type OwnFontInfo,
  type PreviewCard,
  type CheckFinding,
  type StoredLookBody,
  type TemplateEdit,
} from "@/lib/api";
import {
  CARD_STYLE_SLOTS,
  PREVIEW_CARDS,
  TOKEN_GROUPS,
  contrastWarnings,
  draftErrors,
  fontUploadRefusal,
  hexToRgb,
  isDirty,
  previewRequest,
  rgbToHex,
  serialQueue,
  setStyle,
  styleOptions,
  type LookDraft,
} from "@/lib/lookEditor";
import { BrandPicker } from "@/components/export/BrandPicker";
import { FontPicker, type OwnFonts } from "@/components/export/FontPicker";
import { PaletteSuggestions } from "@/components/export/PaletteSuggestions";
import { TemplateEditor } from "@/components/export/TemplateEditor";
import { editsList, type TemplateEdits } from "@/lib/templateEditor";
import { refreshLooks, useLooks } from "@/lib/useLooks";
import { cn } from "@/lib/utils";

export const GUIDE_URL =
  "https://github.com/mandakan/splitsmith/blob/main/docs/looks/authoring.md";
export const DRAFT_PREVIEW_DEBOUNCE_MS = 400;

type Tab = "palette" | "styles" | "templates";

export interface LookEditorProps {
  open: boolean;
  onClose: () => void;
  /** The user Look being edited. */
  name: string;
  /** Its catalog entry: the variants each card slot offers. */
  info: LookInfo | undefined;
  /** The shooter and stage the preview draws on. */
  slug: string;
  stageNumber: number;
  hosted: boolean;
  /** After a delete: the page picks another Look. */
  onDeleted: () => void;
  /** Made on the way in just now: until it is saved, backing out is Discard,
   *  which removes it without asking, not Delete. */
  isNew?: boolean;
  /** The shooter's Identity sheet, for the palette's "Add a logo". */
  identityHref?: string;
}

const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function LookEditor({
  open,
  onClose,
  name,
  info,
  slug,
  stageNumber,
  hosted,
  onDeleted,
  isNew = false,
  identityHref,
}: LookEditorProps) {
  const confirm = useConfirm();
  const [everSaved, setEverSaved] = useState(false);
  const discardable = isNew && !everSaved;
  const [saved, setSaved] = useState<StoredLookBody | null>(null);
  const [draft, setDraft] = useState<LookDraft | null>(null);
  const [tab, setTab] = useState<Tab>("palette");
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // The template editor's unsaved text and the card it is on (#1265).
  const [edits, setEdits] = useState<TemplateEdits>({});
  const [focus, setFocus] = useState<{ card: PreviewCard; variant: string } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  // Template errors the check found on Save; set means the next Save is
  // "Save anyway". Any change to the draft asks again.
  const [failing, setFailing] = useState<CheckFinding[] | null>(null);
  useEffect(() => setFailing(null), [edits, draft]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setSaved(null);
    setDraft(null);
    setProblem(null);
    setEdits({});
    setFocus(null);
    api
      .getLook(name)
      .then((stored) => {
        if (!alive) return;
        setSaved(stored.body);
        setDraft(stored.body);
      })
      .catch(
        (e: unknown) =>
          alive &&
          setProblem(
            e instanceof ApiError ? e.message : "The Look could not be read.",
          ),
      );
    return () => {
      alive = false;
    };
  }, [open, name]);

  const errors = useMemo(() => (draft ? draftErrors(draft) : {}), [draft]);
  const warnings = useMemo(
    () => (draft ? contrastWarnings(draft.colors) : []),
    [draft],
  );
  const bodyDirty = saved !== null && draft !== null && isDirty(saved, draft);
  const dirty = bodyDirty || Object.keys(edits).length > 0;
  const canSave = dirty && Object.keys(errors).length === 0 && !saving;

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setProblem(null);
    try {
      const templates = editsList(edits);
      if (templates.length > 0 && failing === null) {
        // A template that throws is left out of every export: say so before
        // it is saved, and let the author save work in progress anyway.
        let errors: CheckFinding[] = [];
        try {
          errors = (await api.checkLook(name, draft, templates)).items.filter((i) => i.level === "error");
        } catch (e) {
          errors = [
            {
              subject: "check",
              level: "error",
              message: e instanceof ApiError ? `could not check: ${e.message}` : "could not check the templates",
            },
          ];
        }
        if (errors.length > 0) {
          setFailing(errors);
          return;
        }
      }
      setFailing(null);
      if (bodyDirty) {
        const stored = await api.putLook(name, draft);
        setSaved(stored.body);
        setDraft(stored.body);
      }
      for (const edit of editsList(edits)) await api.saveTemplate(name, edit);
      if (Object.keys(edits).length > 0) {
        setEdits({});
        setReloadKey((k) => k + 1);
      }
      await refreshLooks();
      setEverSaved(true);
    } catch (e) {
      setProblem(
        e instanceof ApiError ? e.message : "The Look could not be saved.",
      );
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!discardable) {
      const answer = await confirm({
        title: `Delete the Look "${draft?.label || name}"?`,
        confirmLabel: "Delete",
      });
      if (!answer.confirmed) return;
    }
    try {
      await api.deleteLook(name);
      await refreshLooks();
      onDeleted();
      onClose();
    } catch (e) {
      setProblem(
        e instanceof ApiError ? e.message : "The Look could not be deleted.",
      );
    }
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      label={`Edit Look ${name}`}
      className="md:max-w-[1100px]"
    >
      <div className="flex items-center gap-3 border-b border-rule px-4 py-3">
        <input
          aria-label="Look name"
          className={cn(inputClass, "max-w-[280px]")}
          value={draft?.label ?? ""}
          disabled={!draft}
          onChange={(e) =>
            draft && setDraft({ ...draft, label: e.target.value })
          }
        />
        <span className="text-sm text-muted">
          {discardable ? "new Look; Close keeps it, Discard removes it" : dirty ? "changes not saved" : "saved"}
        </span>
      </div>
      {errors.label ? (
        <p role="alert" className="px-4 pt-2 text-sm text-destructive">
          {errors.label}
        </p>
      ) : null}
      <div className="px-4 pt-3">
        <Segmented<Tab>
          label="Editor section"
          value={tab}
          onChange={setTab}
          options={[
            { value: "palette", label: "Palette" },
            { value: "styles", label: "Card styles" },
            // Templates are code: the desktop runs a Look's own, hosted does
            // not (yet), and Card styles says so there.
            ...(hosted ? [] : [{ value: "templates" as const, label: "Templates (HTML)" }]),
          ]}
        />
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-y-auto p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="min-w-0">
          {!draft ? (
            <p className="text-md text-muted">
              {problem ?? "Loading the Look…"}
            </p>
          ) : tab === "palette" ? (
            <>
              <PaletteSuggestions
                draft={draft}
                setDraft={setDraft}
                slug={slug}
                stageNumber={stageNumber}
                hosted={hosted}
                identityHref={identityHref}
              />
              <Palette
                draft={draft}
                setDraft={setDraft}
                errors={errors}
                warnings={warnings}
              />
            </>
          ) : tab === "styles" ? (
            <CardStyles
              name={name}
              draft={draft}
              setDraft={setDraft}
              info={info}
              hosted={hosted}
            />
          ) : (
            hosted ? (
              <Templates name={name} hosted={hosted} />
            ) : (
              <TemplateEditor
                name={name}
                draft={draft}
                edits={edits}
                setEdits={setEdits}
                onFocus={setFocus}
                reloadKey={reloadKey}
              />
            )
          )}
        </div>
        {draft ? (
          <DraftPreview
            name={name}
            draft={draft}
            info={info}
            slug={slug}
            stageNumber={stageNumber}
            templates={editsList(edits)}
            focus={tab === "templates" ? focus : null}
            hosted={hosted}
          />
        ) : null}
      </div>
      {failing ? (
        <div role="alert" className="border-t border-rule px-4 py-2 text-sm">
          <p className="text-destructive">
            These templates fail, and the card would be left out of an export. Fix them, or save anyway to keep
            working on them.
          </p>
          <ul className="mt-1">
            {failing.map((f, i) => (
              <li key={`${f.subject}-${i}`} className="text-muted">
                <span className="numeral">{f.subject}</span>: {f.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="flex items-center gap-2 border-t border-rule px-4 py-3">
        <Button
          variant="destructive"
          size="sm"
          onClick={() => void remove()}
          disabled={!draft}
        >
          {discardable ? "Discard" : "Delete Look"}
        </Button>
        {problem && draft ? (
          <p role="alert" className="text-sm text-destructive">
            {problem}
          </p>
        ) : null}
        <span className="flex-1" />
        <Button variant="default" onClick={onClose}>
          {dirty ? "Cancel" : "Close"}
        </Button>
        <Button
          variant="primary"
          onClick={() => void save()}
          disabled={!canSave}
        >
          {saving ? "Saving…" : failing ? "Save anyway" : "Save Look"}
        </Button>
      </div>
    </Sheet>
  );
}

function Palette({
  draft,
  setDraft,
  errors,
  warnings,
}: {
  draft: LookDraft;
  setDraft: (d: LookDraft) => void;
  errors: Record<string, string>;
  warnings: { token: string; message: string }[];
}) {
  const setColour = (token: string, hex: string) => {
    const rgb = hexToRgb(hex);
    if (rgb) setDraft({ ...draft, colors: { ...draft.colors, [token]: rgb } });
  };
  return (
    <div className="flex flex-col gap-4">
      {TOKEN_GROUPS.map((group) => (
        <section key={group.group}>
          <Label>{group.group}</Label>
          <ul className="mt-1">
            {group.tokens.map((t) => {
              const rgb = draft.colors[t.token];
              const warning = warnings.find((w) => w.token === t.token);
              const error = errors[`colors.${t.token}`];
              return (
                <li
                  key={t.token}
                  className="border-b border-rule py-2 last:border-b-0"
                >
                  <div className="flex items-center gap-3">
                    <input
                      type="color"
                      aria-label={`${t.label} colour`}
                      value={rgb ? rgbToHex(rgb) : "#000000"}
                      onChange={(e) => setColour(t.token, e.target.value)}
                      className="h-7 w-9 shrink-0 cursor-pointer rounded border border-rule-strong bg-transparent"
                    />
                    <span className="flex w-32 shrink-0 flex-col">
                      <span className="text-md text-ink">{t.label}</span>
                      <span className="numeral text-sm text-subtle">{t.token}</span>
                    </span>
                    <span className="numeral w-20 shrink-0 text-sm text-muted">
                      {rgb ? rgbToHex(rgb) : "unset"}
                    </span>
                    <span className="min-w-0 text-sm text-muted">{t.help}</span>
                  </div>
                  {error ? (
                    <p role="alert" className="mt-1 text-sm text-destructive">
                      {error}
                    </p>
                  ) : warning ? (
                    <p className="mt-1 text-sm text-live">{warning.message}</p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      <section>
        <Label>Accent series</Label>
        <p className="mt-1 text-sm text-muted">
          Shooters without their own accent, by grid tile.
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {draft.accent_series.map((colour, i) => (
            <span key={`${i}-${colour}`} className="flex items-center gap-1">
              <input
                type="color"
                aria-label={`Series colour ${i + 1}`}
                value={hexToRgb(colour) ? colour : "#000000"}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    accent_series: draft.accent_series.map((c, j) =>
                      j === i ? e.target.value : c,
                    ),
                  })
                }
                className="h-7 w-9 cursor-pointer rounded border border-rule-strong bg-transparent"
              />
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Remove series colour ${i + 1}`}
                onClick={() =>
                  setDraft({
                    ...draft,
                    accent_series: draft.accent_series.filter(
                      (_, j) => j !== i,
                    ),
                  })
                }
              >
                ×
              </Button>
              {errors[`accent_series.${i}`] ? (
                <span role="alert" className="text-sm text-destructive">
                  {errors[`accent_series.${i}`]}
                </span>
              ) : null}
            </span>
          ))}
          <Button
            variant="default"
            size="sm"
            onClick={() =>
              setDraft({
                ...draft,
                accent_series: [
                  ...draft.accent_series,
                  rgbToHex(draft.colors.accent ?? [255, 45, 45]),
                ],
              })
            }
          >
            Add colour
          </Button>
        </div>
      </section>
    </div>
  );
}

/** The Look's own font files and their upload (#1272), desktop only. */
function useOwnFonts(name: string, hosted: boolean): OwnFonts | undefined {
  const [list, setList] = useState<OwnFontInfo[]>([]);
  useEffect(() => {
    if (hosted) return;
    let live = true;
    api
      .listOwnFonts(name)
      .then((f) => live && setList(f))
      .catch(() => live && setList([]));
    return () => {
      live = false;
    };
  }, [name, hosted]);
  if (hosted) return undefined;
  return {
    fonts: list,
    upload: async (file: File) => {
      let added: OwnFontInfo;
      try {
        added = await api.uploadOwnFont(name, file);
      } catch (err) {
        throw new Error(fontUploadRefusal(err), { cause: err });
      }
      setList((prev) => (prev.some((f) => f.value === added.value) ? prev : [...prev, added]));
      return added;
    },
  };
}

function CardStyles({
  name,
  draft,
  setDraft,
  info,
  hosted,
}: {
  name: string;
  draft: LookDraft;
  setDraft: (d: LookDraft) => void;
  info: LookInfo | undefined;
  hosted: boolean;
}) {
  const { fonts = [] } = useLooks();
  const own = useOwnFonts(name, hosted);
  return (
    <div className="flex flex-col">
      <BrandPicker
        name={name}
        draft={draft}
        setDraft={setDraft}
        hosted={hosted}
        upload={async (file) => {
          try {
            return await api.uploadBrandLogo(name, file);
          } catch (err) {
            throw new Error(fontUploadRefusal(err), { cause: err });
          }
        }}
      />
      {CARD_STYLE_SLOTS.map(({ slot, label }) => (
        <div
          key={slot}
          className="flex items-center justify-between gap-3 border-b border-rule py-2.5"
        >
          <span className="text-md text-ink">{label}</span>
          <Segmented<string>
            label={`${label} style`}
            value={draft.styles[slot] ?? "default"}
            onChange={(v) => setDraft(setStyle(draft, slot, v))}
            options={styleOptions(info, slot).map((v) => ({
              value: v,
              label: titleCase(v),
            }))}
          />
        </div>
      ))}
      <p className="mt-3 max-w-[60ch] text-sm text-muted">
        These are the Look&apos;s defaults on the Export page; each export can
        still change a card&apos;s style.
        {hosted
          ? " On splitsmith.app a Look picks from the shipped templates."
          : ""}
      </p>
      <FontPicker draft={draft} setDraft={setDraft} fonts={fonts} own={hosted ? undefined : own} />
    </div>
  );
}

function Templates({ name, hosted }: { name: string; hosted: boolean }) {
  return (
    <div className="flex max-w-[60ch] flex-col gap-3 text-md text-ink-2">
      {hosted ? (
        <>
          <p>
            On splitsmith.app a Look uses the shipped card templates with your
            colours and styles. Writing your own HTML runs code on our servers,
            so it arrives once that code is fenced in.
          </p>
          <p>
            On the desktop app you can write templates today; a Look made there
            keeps its colours and styles here.
          </p>
        </>
      ) : (
        <>
          <p>
            A Look&apos;s templates are HTML files in its folder,{" "}
            <span className="numeral">~/.splitsmith/looks/{name}/</span>. Edit
            them in any editor; the preview here redraws when you change a
            colour or style.
          </p>
          <p>
            Check a template with{" "}
            <span className="numeral">splitsmith looks check {name}</span>.
          </p>
        </>
      )}
      <a
        className="text-sm text-led-text underline-offset-4 hover:underline"
        href={GUIDE_URL}
        target="_blank"
        rel="noreferrer"
      >
        How Looks work
      </a>
    </div>
  );
}

/** How long editing must pause before the big preview asks for its
 *  animation: an animated render is dozens of frames, and the server cannot
 *  cancel one the editor has moved past, so every change while it runs
 *  would wait behind it. */
export const MOTION_IDLE_MS = 1500;
/** The thumbnails redraw once editing pauses, after the big preview. */
export const THUMBS_DEBOUNCE_MS = 900;

function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("animate-spin", className)} aria-hidden />;
}

const wait = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("aborted", "AbortError"));
    });
  });

export function DraftPreview({
  name,
  draft,
  info,
  slug,
  stageNumber,
  templates,
  focus,
  hosted = false,
}: {
  name: string;
  draft: LookDraft;
  info: LookInfo | undefined;
  slug: string;
  stageNumber: number;
  templates: TemplateEdit[];
  /** The template editor's card and style: the big preview follows it. */
  focus: { card: PreviewCard; variant: string } | null;
  /** Hosted has no footage on its disk, so it starts on the demo scene. */
  hosted?: boolean;
}) {
  const [backdrop, setBackdrop] = useState<"footage" | "demo">(hosted ? "demo" : "footage");
  const sting = focus?.card === "sting" ? focus.variant : (info?.slots.transition?.[0]?.name ?? null);
  const cards = PREVIEW_CARDS.filter((c) => c.card !== "sting" || sting !== null);
  const [card, setCard] = useState<PreviewCard>("title");
  const [at, setAt] = useState<number | null>(null);
  const [big, setBig] = useState<string | null>(null);
  const [moving, setMoving] = useState(false);
  // What the big preview is doing now: drawing its still, adding the
  // animation, or nothing. Shown over the last picture, never instead of it.
  const [phase, setPhase] = useState<"still" | "motion" | null>(null);
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [thumbBusy, setThumbBusy] = useState<Record<string, boolean>>({});
  const [thumbFailed, setThumbFailed] = useState<Record<string, boolean>>({});
  const [failed, setFailed] = useState<string | null>(null);
  const urls = useRef<string[]>([]);
  // One render at a time: the server answers 429 to a second in flight.
  const queue = useRef(serialQueue()).current;
  useEffect(() => {
    if (focus) {
      setCard(focus.card);
      setAt(null);
    }
  }, [focus]);
  const current = cards.find((c) => c.card === card) ?? cards[0];
  const templatesKey = JSON.stringify(templates);

  useEffect(
    () => () => {
      urls.current.forEach((u) => URL.revokeObjectURL(u));
    },
    [],
  );

  const fetchCard = async (
    c: PreviewCard,
    width: number,
    time: number | null,
    signal: AbortSignal,
    motion: boolean,
  ) => {
    const blob = await queue(() =>
      api.exportPreview(
        slug,
        previewRequest({
          card: c,
          look: name,
          draft,
          stageNumber,
          width,
          at: time,
          sting,
          variant: focus && focus.card === c ? focus.variant : undefined,
          templates,
          motion,
          backdrop,
        }),
        signal,
      ),
    );
    const url = URL.createObjectURL(blob);
    urls.current.push(url);
    return url;
  };

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    setPhase("still");
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          // The still first: a change shows in about a second.
          const still = await fetchCard(current.card, 960, at, signal, false);
          if (signal.aborted) return;
          setBig(still);
          setMoving(false);
          setFailed(null);
          // Then the animation, once editing has paused.
          if (current.animated && at === null) {
            setPhase(null);
            await wait(MOTION_IDLE_MS, signal);
            setPhase("motion");
            const clip = await fetchCard(current.card, 960, null, signal, true);
            if (signal.aborted) return;
            setBig(clip);
            setMoving(true);
          }
          setPhase(null);
        } catch (e: unknown) {
          if (signal.aborted) return;
          setPhase(null);
          setFailed(
            e instanceof ApiError && e.status === 503 && /rasteriz/.test(e.message)
              ? "This card's template failed to draw; Check under Templates says why."
              : e instanceof ApiError
                ? e.message
                : "The preview could not be drawn.",
          );
        }
      })();
    }, DRAFT_PREVIEW_DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchCard reads these
  }, [current.card, at, draft, name, slug, stageNumber, templatesKey, focus, backdrop]);

  useEffect(() => {
    const controller = new AbortController();
    setThumbBusy(Object.fromEntries(cards.map((c) => [c.card, true])));
    const timer = window.setTimeout(() => {
      cards.forEach((c) => {
        fetchCard(c.card, 240, null, controller.signal, false)
          .then((url) => {
            setThumbs((t) => ({ ...t, [c.card]: url }));
            setThumbFailed((f) => ({ ...f, [c.card]: false }));
          })
          .catch(() => {
            if (!controller.signal.aborted) setThumbFailed((f) => ({ ...f, [c.card]: true }));
          })
          .finally(() => {
            if (!controller.signal.aborted) setThumbBusy((b) => ({ ...b, [c.card]: false }));
          });
      });
    }, THUMBS_DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchCard reads these
  }, [draft, name, slug, stageNumber, sting, templatesKey, backdrop]);

  const status =
    phase === "still"
      ? big
        ? "Updating preview…"
        : "Drawing the preview…"
      : phase === "motion"
        ? "Adding the animation…"
        : null;

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label>Preview</Label>
        <Segmented<"footage" | "demo">
          label="Preview backdrop"
          value={backdrop}
          onChange={setBackdrop}
          options={[
            { value: "footage", label: "This match" },
            { value: "demo", label: "Demo scene" },
          ]}
        />
      </div>
      <p className="text-sm text-muted">
        {current.label},{" "}
        {backdrop === "demo" ? (
          "over the demo scene"
        ) : (
          <>
            over stage <span className="numeral">{String(stageNumber).padStart(2, "0")}</span> of this match
          </>
        )}
      </p>
      <div className="relative aspect-video w-full overflow-hidden rounded-md border border-rule bg-bg">
        {big ? (
          <img
            src={big}
            alt={`${current.label} preview`}
            className={cn(
              "h-full w-full object-contain transition-opacity",
              phase === "still" ? "opacity-50" : "opacity-100",
            )}
          />
        ) : null}
        {status ? (
          <div
            role="status"
            className={cn(
              "absolute flex items-center gap-2 text-sm text-ink",
              big
                ? "bottom-2 left-2 rounded-md bg-bg/80 px-2 py-1"
                : "inset-0 justify-center",
            )}
          >
            <Spinner className="size-4" />
            {status}
          </div>
        ) : null}
      </div>
      {failed ? <p className="text-sm text-destructive">{failed}</p> : null}
      {current.animated ? (
        <div className="flex items-center gap-3">
          <input
            type="range"
            aria-label="Time into the card"
            min={0}
            max={current.seconds}
            step={0.05}
            value={at ?? current.seconds}
            onChange={(e) => setAt(Number(e.target.value))}
            className="flex-1"
          />
          <span className="numeral w-28 text-right text-sm text-muted">
            {at !== null
              ? `${at.toFixed(2)} / ${current.seconds.toFixed(1)} s`
              : moving
                ? "playing"
                : "still frame"}
          </span>
          {at !== null ? (
            <Button variant="ghost" size="sm" onClick={() => setAt(null)}>
              Play
            </Button>
          ) : null}
        </div>
      ) : null}
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        {cards.map((c) => (
          <button
            key={c.card}
            type="button"
            aria-pressed={c.card === current.card}
            onClick={() => {
              setCard(c.card);
              setAt(null);
            }}
            className={cn(
              "flex cursor-pointer flex-col gap-1 rounded-md border p-1 text-left transition-colors",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-led",
              c.card === current.card ? "border-led" : "border-rule hover:border-rule-strong hover:bg-surface-2",
            )}
          >
            <span className="relative aspect-video w-full overflow-hidden rounded-sm bg-bg">
              {thumbs[c.card] ? (
                <img
                  src={thumbs[c.card]}
                  alt=""
                  className={cn("h-full w-full object-cover", thumbBusy[c.card] && "opacity-40")}
                />
              ) : null}
              {thumbBusy[c.card] ? (
                <span className="absolute inset-0 flex items-center justify-center">
                  <Spinner className="size-3.5 text-muted" />
                </span>
              ) : thumbFailed[c.card] ? (
                <span className="absolute inset-0 flex items-center justify-center text-sm text-destructive">
                  failed
                </span>
              ) : null}
            </span>
            <span className="text-sm text-muted">{c.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
