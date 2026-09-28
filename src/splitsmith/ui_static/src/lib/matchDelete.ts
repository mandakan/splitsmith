/**
 * What deleting a match from the Matches list removes, worded for the
 * confirm dialog.
 *
 * Hosted and desktop deletes are separate actions on purpose: deleting on
 * one side never touches the other side's copy. The dialog says which
 * copy goes and which stays, so nobody deletes on the phone expecting the
 * desktop folder to follow, or the other way round.
 */
import type { RecentProjectDetail } from "@/lib/api";

/** Structurally the ConfirmDialog's checkbox; kept here so the module
 *  stays free of component imports. */
export interface DeleteCheckbox {
  key: string;
  label: string;
  help?: string;
}

export interface MatchDeleteCopy {
  title: string;
  /** One paragraph per entry. */
  body: string[];
  confirmLabel: string;
  checkboxes: DeleteCheckbox[];
}

export function matchDeleteCopy(
  project: Pick<RecentProjectDetail, "name" | "kind" | "origin" | "synced">,
  hosted: boolean,
): MatchDeleteCopy {
  const name = project.name || "this match";
  if (hosted) {
    const body = [
      `Deletes ${name} from splitsmith.app: its stages, audits, comments and stored media, and any running jobs. This cannot be undone.`,
    ];
    if (project.origin === "desktop") {
      body.push(
        "It was synced from a desktop, and that copy is not deleted. The desktop app stops syncing this match and offers Publish again if you want it back here.",
      );
    } else if (project.origin !== "hosted") {
      body.push("If it was synced from a desktop, that copy is not deleted.");
    }
    return {
      title: `Delete ${name}?`,
      body,
      confirmLabel: "Delete match",
      checkboxes: [
        {
          key: "deleteRawUploads",
          label: "Also delete raw uploads that fed only this match",
          help: "Uploaded videos still attached to another match are kept.",
        },
      ],
    };
  }
  if (project.kind === "missing") {
    return {
      title: `Remove ${name} from the list?`,
      body: [
        "Its folder is no longer at the saved location, so this only removes the entry from this list.",
        "A copy on splitsmith.app, if there is one, is not deleted.",
      ],
      confirmLabel: "Remove",
      checkboxes: [],
    };
  }
  const body = [`Removes ${name} from this computer's match list and cancels its running jobs.`];
  body.push(
    project.synced
      ? "It has been synced, and the copy on splitsmith.app is not deleted. Delete it there too if you want it gone everywhere."
      : "It has never been synced, so there is no copy on splitsmith.app.",
  );
  return {
    title: `Delete ${name}?`,
    body,
    confirmLabel: "Delete match",
    checkboxes: [
      {
        key: "deleteLocalFiles",
        label: "Also delete the project folder on disk",
        help: "Permanently removes the footage, audit work and exports under this match's folder. This cannot be undone.",
      },
    ],
  };
}
