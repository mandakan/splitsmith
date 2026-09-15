/**
 * RegisterWorkerSheet -- add a self-hosted compute worker to the pool
 * from the Workers admin page. Two steps: the name / priority form, then
 * the once-only registration token with the three commands that bring
 * the agent up on the host. Built on Sheet + Field like every other
 * side-panel form (spec 2026-09-13 s6).
 */
import { useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Field, inputClass } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import { Sheet } from "@/components/ui/Sheet";
import { ApiError, api, type CreateWorkerResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

interface RegisterWorkerSheetProps {
  open: boolean;
  onClose: () => void;
}

const BUILD_COMMAND =
  "git clone https://github.com/mandakan/splitsmith.git && " +
  "cd splitsmith && docker build -t splitsmith:local .";
const LOGS_COMMAND = "docker logs -f splitsmith-agent";

const CODE_BLOCK =
  "min-w-0 flex-1 overflow-x-auto whitespace-pre-wrap break-all rounded-md border border-rule bg-surface-2 px-3 py-2 font-mono text-sm text-ink";

export function RegisterWorkerSheet({ open, onClose }: RegisterWorkerSheetProps) {
  const [step, setStep] = useState<"form" | "success">("form");
  const [name, setName] = useState("");
  const [priority, setPriority] = useState("10");
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [result, setResult] = useState<CreateWorkerResponse | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function handleCreate() {
    setFormError(null);
    const p = parseInt(priority, 10);
    if (!name.trim()) {
      setFormError("Name is required.");
      return;
    }
    if (Number.isNaN(p)) {
      setFormError("Priority must be a number.");
      return;
    }
    setCreating(true);
    try {
      const resp = await api.adminCreateWorker(name.trim(), p);
      setResult(resp);
      setStep("success");
    } catch (e) {
      setFormError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setCreating(false);
    }
  }

  async function handleCopy(key: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      if (copyTimerRef.current != null) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopiedKey(null), 2000);
    } catch {
      // Clipboard access denied.
    }
  }

  function renderStep(key: string, index: number, title: string, command: string, note: string) {
    return (
      <div className="flex flex-col gap-1.5">
        <div className="flex items-baseline gap-2">
          <Label tone="subtle">{index}</Label>
          <Label>{title}</Label>
        </div>
        <div className="flex items-start gap-2">
          <pre className={CODE_BLOCK}>{command}</pre>
          <Button
            type="button"
            size="sm"
            aria-label={
              copiedKey === key ? `${title} command copied to clipboard` : `Copy ${title} command to clipboard`
            }
            onClick={() => void handleCopy(key, command)}
          >
            {copiedKey === key ? "Copied" : "Copy"}
          </Button>
        </div>
        <p className="text-sm text-muted">{note}</p>
      </div>
    );
  }

  const title = step === "form" ? "Register worker" : "Worker registered";
  return (
    <Sheet open={open} onClose={onClose} label={title}>
      <div className="flex items-center gap-3 border-b border-rule px-4 py-3">
        <span className="min-w-0 flex-1 text-md font-medium text-ink">{title}</span>
        <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close">
          &#10005;
        </Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4 text-md text-ink-2">
        {step === "form" ? (
          <>
            <p className="text-sm text-muted">Add a self-hosted compute worker to the pool.</p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void handleCreate();
              }}
              className="flex flex-col gap-4"
            >
              <div className="rounded-[10px] border border-rule bg-surface">
                <Field label="Name" htmlFor="worker-name">
                  <input
                    id="worker-name"
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    disabled={creating}
                    placeholder="my-worker-01"
                    className={inputClass}
                    aria-required="true"
                  />
                </Field>
                <Field
                  label="Priority"
                  htmlFor="worker-priority"
                  help="Higher priority workers are selected first, so your self-hosted workers take jobs before the Railway fallback. Self-hosted defaults to 1000; Railway is 100."
                >
                  <input
                    id="worker-priority"
                    type="number"
                    value={priority}
                    onChange={(e) => setPriority(e.target.value)}
                    disabled={creating}
                    className={cn(inputClass, "numeral w-28")}
                    aria-required="true"
                  />
                </Field>
              </div>
              {formError ? (
                <p role="alert" className="text-sm text-led-text">
                  {formError}
                </p>
              ) : null}
              <Button type="submit" variant="primary" disabled={creating} className="self-start">
                {creating ? "Creating..." : "Create"}
              </Button>
            </form>
          </>
        ) : result ? (
          <>
            <p className="text-sm text-muted">
              Copy the docker command and run it on your host to activate the worker.
            </p>
            <p className="flex items-start gap-2 rounded-[10px] border border-rule px-3 py-2.5 text-sm text-ink-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>
                This token is shown once. The agent keeps database credentials after registration; deleting the
                worker does not revoke them.
              </span>
            </p>

            <div className="flex flex-col gap-1.5">
              <Label>Registration token</Label>
              <pre className={CODE_BLOCK}>{result.registration_token}</pre>
            </div>

            <div className="flex flex-col gap-4">
              <Label>Run the worker</Label>
              {renderStep(
                "build",
                1,
                "Build image",
                BUILD_COMMAND,
                "One-time. Skip if you already have the image (for example pulled from a registry).",
              )}
              {renderStep(
                "run",
                2,
                "Start agent",
                result.docker_command,
                "Uses the server's configured image. If you built locally, replace it with your local tag (for example splitsmith:local).",
              )}
              {renderStep(
                "logs",
                3,
                "Check logs",
                LOGS_COMMAND,
                "You should see 'connected to wake channel; waiting for wake events'. The agent is quiet between jobs. A 404 means the worker was deleted.",
              )}
            </div>
          </>
        ) : null}
      </div>
      {step === "success" ? (
        <div className="flex justify-end border-t border-rule px-4 py-3">
          <Button type="button" onClick={onClose}>
            Done
          </Button>
        </div>
      ) : null}
    </Sheet>
  );
}
