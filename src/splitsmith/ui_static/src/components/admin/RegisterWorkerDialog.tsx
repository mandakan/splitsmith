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
  const [copied, setCopied] = useState(false);

  const panelRef = useRef<HTMLDivElement | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useDialogFocus(true, panelRef, onClose);

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

  async function handleCopy() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.docker_command);
      setCopied(true);
      if (copyTimerRef.current != null) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access denied.
    }
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
                  after registration, so deleting the worker does not revoke them.
                </div>

                <div className="space-y-1">
                  <div className="font-mono text-xs uppercase tracking-[0.08em] text-muted-foreground">
                    Registration token
                  </div>
                  <div className="break-all rounded border border-input bg-surface-2 px-3 py-2 font-mono text-xs">
                    {result.registration_token}
                  </div>
                </div>

                <div className="space-y-1">
                  <div className="font-mono text-xs uppercase tracking-[0.08em] text-muted-foreground">
                    Docker command
                  </div>
                  <div className="flex items-start gap-2">
                    <pre className="min-w-0 flex-1 overflow-x-auto rounded border border-input bg-surface-2 px-3 py-2 font-mono text-xs whitespace-pre-wrap break-all">
                      {result.docker_command}
                    </pre>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      aria-label={
                        copied
                          ? "Docker command copied to clipboard"
                          : "Copy docker command to clipboard"
                      }
                      onClick={() => void handleCopy()}
                    >
                      {copied ? "Copied" : "Copy"}
                    </Button>
                  </div>
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
