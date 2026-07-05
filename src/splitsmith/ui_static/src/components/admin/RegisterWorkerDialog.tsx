import { useRef, useState } from "react";
import { AlertTriangle, Server } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Portal } from "@/components/ui/Portal";
import { useDialogFocus } from "@/lib/dialogFocus";
import { ApiError, api, type CreateWorkerResponse } from "@/lib/api";

interface RegisterWorkerDialogProps {
  onClose: () => void;
}

export function RegisterWorkerDialog({ onClose }: RegisterWorkerDialogProps) {
  const [step, setStep] = useState<"form" | "success">("form");
  const [name, setName] = useState("");
  const [priority, setPriority] = useState("10");
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [result, setResult] = useState<CreateWorkerResponse | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const panelRef = useRef<HTMLDivElement | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useDialogFocus(true, panelRef, onClose);

  const BUILD_COMMAND =
    "git clone https://github.com/mandakan/splitsmith.git && " +
    "cd splitsmith && docker build -t splitsmith:local .";
  const LOGS_COMMAND = "docker logs -f splitsmith-agent";

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

  function renderStep(
    key: string,
    index: number,
    title: string,
    command: string,
    note: string,
  ) {
    return (
      <div className="space-y-1">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-xs text-muted-foreground">{index}.</span>
          <span className="font-mono text-xs uppercase tracking-[0.08em] text-muted-foreground">
            {title}
          </span>
        </div>
        <div className="flex items-start gap-2">
          <pre className="min-w-0 flex-1 overflow-x-auto rounded border border-input bg-surface-2 px-3 py-2 font-mono text-xs whitespace-pre-wrap break-all">
            {command}
          </pre>
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={
              copiedKey === key
                ? `${title} command copied to clipboard`
                : `Copy ${title} command to clipboard`
            }
            onClick={() => void handleCopy(key, command)}
          >
            {copiedKey === key ? "Copied" : "Copy"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">{note}</p>
      </div>
    );
  }

  return (
    <Portal>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="register-worker-title"
        aria-describedby="register-worker-desc"
        className="fixed inset-0 z-modal flex items-center justify-center bg-background/70 p-4"
        onClick={onClose}
      >
        <Card
          ref={panelRef}
          tabIndex={-1}
          className="flex max-h-[90vh] w-full max-w-lg flex-col shadow-xl outline-none"
          onClick={(e) => e.stopPropagation()}
        >
          <CardHeader>
            <CardTitle
              id="register-worker-title"
              className="flex items-center gap-2"
            >
              <Server className="size-5" aria-hidden="true" />
              {step === "form" ? "Register worker" : "Worker registered"}
            </CardTitle>
            <CardDescription id="register-worker-desc">
              {step === "form"
                ? "Add a self-hosted compute worker to the pool."
                : "Copy the docker command and run it on your host to activate the worker."}
            </CardDescription>
          </CardHeader>

          <CardContent className="flex-1 space-y-4 overflow-y-auto text-sm">
            {step === "form" ? (
              <>
                {formError ? (
                  <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                    <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
                    <span>{formError}</span>
                  </div>
                ) : null}

                <div className="space-y-3">
                  <div className="flex flex-col gap-1">
                    <label
                      htmlFor="worker-name"
                      className="font-mono text-xs uppercase tracking-[0.08em] text-muted-foreground"
                    >
                      Name
                    </label>
                    <input
                      id="worker-name"
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      disabled={creating}
                      placeholder="my-worker-01"
                      className="rounded border border-input bg-background px-3 py-1.5 text-sm disabled:opacity-50"
                      aria-required="true"
                    />
                  </div>

                  <div className="flex flex-col gap-1">
                    <label
                      htmlFor="worker-priority"
                      className="font-mono text-xs uppercase tracking-[0.08em] text-muted-foreground"
                    >
                      Priority
                    </label>
                    <input
                      id="worker-priority"
                      type="number"
                      value={priority}
                      onChange={(e) => setPriority(e.target.value)}
                      disabled={creating}
                      className="w-24 rounded border border-input bg-background px-3 py-1.5 text-sm disabled:opacity-50"
                      aria-required="true"
                    />
                    <p className="text-xs text-muted-foreground">
                      Higher priority workers are selected first.
                    </p>
                  </div>
                </div>

                <div>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => void handleCreate()}
                    disabled={creating}
                  >
                    {creating ? "Creating..." : "Create"}
                  </Button>
                </div>
              </>
            ) : result ? (
              <>
                <div className="rounded-md border border-amber-400/40 bg-amber-400/10 p-3 text-xs text-amber-600">
                  <AlertTriangle
                    className="mb-1 inline size-4 align-middle"
                    aria-hidden="true"
                  />{" "}
                  This token is shown once. The agent keeps database credentials
                  after registration - deleting the worker does not revoke them.
                </div>

                <div className="space-y-1">
                  <div className="font-mono text-xs uppercase tracking-[0.08em] text-muted-foreground">
                    Registration token
                  </div>
                  <div className="break-all rounded border border-input bg-surface-2 px-3 py-2 font-mono text-xs">
                    {result.registration_token}
                  </div>
                </div>

                <div className="space-y-3">
                  <div className="font-mono text-xs uppercase tracking-[0.08em] text-muted-foreground">
                    Run the worker on your host
                  </div>
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
          </CardContent>

          <div className="flex justify-end border-t border-border p-4">
            <Button type="button" variant="ghost" onClick={onClose}>
              {step === "success" ? "Done" : "Cancel"}
            </Button>
          </div>
        </Card>
      </div>
    </Portal>
  );
}
