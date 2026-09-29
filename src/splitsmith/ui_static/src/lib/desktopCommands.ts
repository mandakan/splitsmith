/**
 * Wording and state for desktop commands (#1100, spec 2026-09-28): the
 * requests a phone makes for the desktop to run, and whether a desktop is
 * around to pick them up. Pure; pages map the results to primitives.
 */
import { capabilityDenied, type DesktopCommand, type DesktopPresence, type MatchCapability, type MatchOrigin } from "@/lib/api";
import { formatRelative } from "@/lib/matches";

export type CommandTone = "muted" | "live" | "ok" | "error";

export interface CommandLine {
  text: string;
  tone: CommandTone;
  /** A waiting or running request can still be cancelled. */
  cancellable: boolean;
  /** The result to open, when the request produced one (a video). */
  link?: { href: string; label: string };
}

/** A hosted page on a desktop-synced match whose export is the desktop's
 *  to run: the match came from a desktop (origin), this account may
 *  request (review, what the request routes need) and cannot edit here.
 *  Unknown capabilities decide nothing yet. */
export function rendersOnDesktop(
  hosted: boolean,
  origin: MatchOrigin | null | undefined,
  capabilities: MatchCapability[] | null | undefined,
): boolean {
  return (
    hosted &&
    origin === "desktop" &&
    capabilityDenied(capabilities, "edit") &&
    !capabilityDenied(capabilities, "review")
  );
}

export function isActiveCommand(c: DesktopCommand): boolean {
  return c.status === "pending" || c.status === "claimed";
}

/** The newest request for one stage (the list comes newest first). */
export function latestForStage(
  commands: readonly DesktopCommand[],
  slug: string,
  stageNumber: number,
): DesktopCommand | null {
  return commands.find((c) => c.slug === slug && c.stage_number === stageNumber) ?? null;
}

/** What the phone says about the desktop while a request waits. */
export function presenceText(presence: DesktopPresence, now: number = Date.now()): string {
  if (!presence.linked) return "No desktop linked. Link one from the desktop app's hosted sync settings.";
  if (presence.around) return "Your desktop will pick this up shortly.";
  if (presence.last_seen_at) {
    return `Waiting for your desktop (last seen ${formatRelative(new Date(presence.last_seen_at), now)}).`;
  }
  return "Waiting for your desktop.";
}

/** Where the desktop stands, for a list header rather than one request. */
export function presenceSummary(presence: DesktopPresence, now: number = Date.now()): string {
  if (!presence.linked) return "No desktop linked. Link one from the desktop app's hosted sync settings.";
  if (presence.around) return "Your desktop is online.";
  if (presence.last_seen_at) return `Your desktop was last seen ${formatRelative(new Date(presence.last_seen_at), now)}.`;
  return "Your desktop has not connected yet.";
}

/** The line under the Export page's "Render on desktop" button: the
 *  per-request wording only while one of these requests waits or runs,
 *  otherwise where the desktop stands. */
export function renderPresenceLine(
  presence: DesktopPresence,
  requests: readonly DesktopCommand[],
  now: number = Date.now(),
): string {
  return requests.some(isActiveCommand) ? presenceText(presence, now) : presenceSummary(presence, now);
}

export function commandLine(
  c: DesktopCommand,
  presence: DesktopPresence,
  now: number = Date.now(),
): CommandLine {
  switch (c.status) {
    case "pending":
      return { text: presenceText(presence, now), tone: "muted", cancellable: true };
    case "claimed":
      if (c.cancel_requested) return { text: "Cancelling on your desktop...", tone: "muted", cancellable: false };
      return {
        text: c.progress_message ? `Running on your desktop: ${c.progress_message}` : "Running on your desktop.",
        tone: "live",
        cancellable: true,
      };
    case "succeeded": {
      const when = c.finished_at ? ` ${formatRelative(new Date(c.finished_at), now)}` : "";
      if (c.kind === "render_upload") {
        const rawUrl = typeof c.result?.url === "string" ? c.result.url : null;
        const url = rawUrl && /^https?:\/\//i.test(rawUrl) ? rawUrl : null;
        const channel =
          typeof c.result?.channel_title === "string" && c.result.channel_title ? c.result.channel_title : "YouTube";
        return {
          text: `Uploaded to ${channel}${when}.`,
          tone: "ok",
          cancellable: false,
          link: url ? { href: url, label: url.replace(/^https?:\/\//, "") } : undefined,
        };
      }
      return { text: `Re-detected on your desktop${when}.`, tone: "ok", cancellable: false };
    }
    case "failed":
      return { text: `Failed on your desktop: ${c.error ?? "no reason given"}`, tone: "error", cancellable: false };
    case "cancelled":
      return { text: "Cancelled.", tone: "muted", cancellable: false };
  }
}

/** A request's name in a list: "Re-detect Stage 03 (anna)". */
export function commandTitle(c: DesktopCommand): string {
  if (c.kind === "render_upload") return c.slug ? `Render and upload (${c.slug})` : "Render and upload";
  const what = c.kind === "shot_detect" ? "Re-detect" : c.kind;
  if (c.stage_number == null) return what;
  const stage = `Stage ${String(c.stage_number).padStart(2, "0")}`;
  return c.slug ? `${what} ${stage} (${c.slug})` : `${what} ${stage}`;
}

/** The confirm a re-detect request asks for. */
export const REDETECT_CONFIRM = {
  title: "Re-detect on your desktop?",
  body: "Your desktop runs detection again and replaces this stage's shots. If the stage is edited after you press this, the desktop does not run it and you can ask again.",
  confirmLabel: "Re-detect",
} as const;
