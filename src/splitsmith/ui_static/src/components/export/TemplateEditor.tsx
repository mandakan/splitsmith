/**
 * TemplateEditor -- the Look editor's Templates tab on the desktop (spec
 * 2026-10-07 s6, #1265): pick a card and style, edit its HTML in
 * CodeMirror (loaded on first open), start from a starter or the template
 * the Look borrows, check the draft the way ``splitsmith looks check``
 * does, see exactly what the template receives, and open the Look's
 * folder. Unsaved text lives in the sheet (``edits``) so the preview, the
 * check and Save all see it; rules live in ``lib/templateEditor``.
 */
import { Suspense, lazy, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { inputClass } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import { Segmented } from "@/components/ui/Segmented";
import { ApiError, api, type CheckFinding, type StoredLookBody, type TemplateInfo } from "@/lib/api";
import {
  editKey,
  editsList,
  previewFocusFor,
  sharedWith,
  templateKey,
  templateLabel,
  templateText,
  type TemplateEdits,
} from "@/lib/templateEditor";
import { cn } from "@/lib/utils";

const CodeEditor = lazy(() => import("@/components/export/CodeEditor"));

export interface TemplateEditorProps {
  name: string;
  draft: StoredLookBody;
  edits: TemplateEdits;
  setEdits: (edits: TemplateEdits) => void;
  /** The card and style being edited: the preview follows it. */
  onFocus: (focus: { card: ReturnType<typeof previewFocusFor>["card"]; variant: string }) => void;
  /** Bumped after Save, to read the saved files back. */
  reloadKey: number;
}

const LEVEL_TONE = { ok: "ok", warn: "warn", error: "warn" } as const;

export function TemplateEditor({ name, draft, edits, setEdits, onFocus, reloadKey }: TemplateEditorProps) {
  const [templates, setTemplates] = useState<TemplateInfo[] | null>(null);
  const [starters, setStarters] = useState<{ name: string; content: string }[]>([]);
  const [key, setKey] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [findings, setFindings] = useState<CheckFinding[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [samples, setSamples] = useState<{ case: string; context: Record<string, unknown> }[]>([]);
  const [sampleCase, setSampleCase] = useState(0);
  const [starter, setStarter] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .listTemplates(name)
      .then((r) => {
        if (!alive) return;
        setTemplates(r.templates);
        setStarters(r.starters);
        setStarter((s) => s || r.starters[0]?.name || "");
        setKey((k) => k ?? (r.templates[0] ? templateKey(r.templates[0].slot, r.templates[0].variant) : null));
      })
      .catch((e: unknown) => alive && setProblem(e instanceof ApiError ? e.message : "The templates could not be read."));
    return () => {
      alive = false;
    };
  }, [name, reloadKey]);

  const current = useMemo(
    () => templates?.find((t) => templateKey(t.slot, t.variant) === key) ?? null,
    [templates, key],
  );

  useEffect(() => {
    if (!current) return;
    onFocus(previewFocusFor(current.slot, current.variant));
    let alive = true;
    api
      .templateSamples(name, current.slot, current.variant)
      .then((r) => {
        if (alive) {
          setSamples(r.cases);
          setSampleCase(0);
        }
      })
      .catch(() => alive && setSamples([]));
    return () => {
      alive = false;
    };
    // onFocus is the sheet's setter; the current template is what moves this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.slot, current?.variant, name]);

  if (problem) return <p className="text-md text-muted">{problem}</p>;
  if (!templates || !current) return <p className="text-md text-muted">Loading the templates…</p>;

  const k = editKey(templates, current);
  const text = templateText(current, edits, templates);
  const shared = sharedWith(templates, current);
  const edited = k in edits;
  const setText = (value: string) => setEdits({ ...edits, [k]: value });

  const check = async () => {
    setChecking(true);
    setFindings(null);
    try {
      const r = await api.checkLook(name, draft, editsList(edits));
      setFindings(r.items);
    } catch (e) {
      setFindings([
        { subject: "check", level: "error", message: e instanceof ApiError ? e.message : "The check could not run." },
      ]);
    } finally {
      setChecking(false);
    }
  };

  const mine = current.own || edited;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Template"
          className={cn(inputClass, "w-auto")}
          value={k}
          onChange={(e) => {
            setKey(e.target.value);
            setFindings(null);
          }}
        >
          {templates.map((t) => (
            <option key={templateKey(t.slot, t.variant)} value={templateKey(t.slot, t.variant)}>
              {templateLabel(t)}
            </option>
          ))}
        </select>
        <span className="text-sm text-muted">
          {mine ? `Yours: ${current.own ? current.file : `${current.slot}-${current.variant}.html`}` : `Borrowed: ${current.file}`}
          {edited ? " · not saved" : ""}
        </span>
      </div>
      {shared.length > 0 ? (
        <p className="text-sm text-muted">This file also draws: {shared.join(", ")}. A change here changes them too.</p>
      ) : null}
      {!mine ? (
        <p className="text-sm text-muted">
          This style draws with the shipped template. Edit it below to make your own copy; Save writes it into the
          Look&apos;s folder.
        </p>
      ) : null}
      <div className="h-[380px]">
        <Suspense fallback={<p className="text-sm text-muted">Loading the editor…</p>}>
          <CodeEditor value={text} onChange={setText} label={`${templateLabel(current)} template`} />
        </Suspense>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Starter"
          className={cn(inputClass, "w-auto")}
          value={starter}
          onChange={(e) => setStarter(e.target.value)}
        >
          {starters.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
        <Button
          variant="default"
          size="sm"
          onClick={() => {
            const s = starters.find((x) => x.name === starter);
            if (s) setText(s.content);
          }}
        >
          Start from this starter
        </Button>
        {edited ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              const next = { ...edits };
              delete next[k];
              setEdits(next);
            }}
          >
            Discard changes
          </Button>
        ) : null}
        <span className="flex-1" />
        <Button variant="default" size="sm" onClick={() => void check()} disabled={checking}>
          {checking ? "Checking…" : "Check"}
        </Button>
        <Button variant="default" size="sm" onClick={() => void api.revealLook(name).catch(() => undefined)}>
          Open folder
        </Button>
      </div>
      {findings ? (
        <ul aria-label="Check findings" className="flex flex-col">
          {findings.map((f, i) => (
            <li key={`${f.subject}-${i}`} className="flex items-baseline gap-2 border-b border-rule py-1.5 last:border-b-0">
              <Chip tone={LEVEL_TONE[f.level]}>{f.level}</Chip>
              <span className="numeral text-sm text-ink-2">{f.subject}</span>
              <span className={cn("min-w-0 text-sm", f.level === "error" ? "text-destructive" : "text-muted")}>
                {f.message}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {samples.length > 0 ? (
        <section className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Label>Data</Label>
            <Segmented<string>
              label="Sample case"
              value={String(sampleCase)}
              onChange={(v) => setSampleCase(Number(v))}
              options={samples.map((s, i) => ({ value: String(i), label: s.case }))}
            />
          </div>
          <pre className="numeral max-h-[260px] overflow-auto rounded-md border border-rule bg-surface-2 p-3 text-sm text-ink-2">
            {JSON.stringify(samples[sampleCase]?.context.data ?? {}, null, 2)}
          </pre>
          <p className="text-sm text-muted">
            What the template receives as <span className="numeral">window.splitsmith.data</span> in this sample;
            the theme, size and fps sit beside it.
          </p>
        </section>
      ) : null}
    </div>
  );
}
