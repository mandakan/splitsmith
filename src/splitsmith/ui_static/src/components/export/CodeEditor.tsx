/**
 * CodeEditor -- CodeMirror 6 over one HTML template (HTML with its CSS and
 * JS highlighted, line numbers, undo). Loaded lazily by the template editor
 * so the editor's weight never reaches a page that does not open it.
 */
import { useEffect, useRef } from "react";

import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { html } from "@codemirror/lang-html";
import { bracketMatching, defaultHighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorState } from "@codemirror/state";
import { EditorView, highlightActiveLine, keymap, lineNumbers } from "@codemirror/view";

export interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  label: string;
}

const theme = EditorView.theme(
  {
    "&": { height: "100%", fontSize: "12px", backgroundColor: "var(--color-surface-2)", color: "var(--color-ink)" },
    ".cm-content": { fontFamily: "var(--font-mono)" },
    ".cm-gutters": { backgroundColor: "var(--color-surface)", color: "var(--color-subtle)", border: "none" },
    ".cm-activeLine": { backgroundColor: "rgba(255,255,255,0.04)" },
    "&.cm-focused": { outline: "1px solid var(--color-led)" },
  },
  { dark: true },
);

export default function CodeEditor({ value, onChange, label }: CodeEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const changed = useRef(onChange);
  changed.current = onChange;

  useEffect(() => {
    if (!host.current) return;
    view.current = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(),
          history(),
          highlightActiveLine(),
          bracketMatching(),
          syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
          html(),
          keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": label }),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) changed.current(u.state.doc.toString());
          }),
          theme,
        ],
      }),
    });
    return () => {
      view.current?.destroy();
      view.current = null;
    };
    // The view is made once; ``value`` changes from outside are applied below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div ref={host} className="h-full min-h-[320px] overflow-hidden rounded-md border border-rule-strong" />;
}
