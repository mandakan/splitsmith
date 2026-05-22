/**
 * Thin facade over :func:`FolderPicker` for the create-match flow's
 * parent-folder picker.
 *
 * The two pickers used to be separate components (~80% shared code).
 * Per the post-b3531b5 designer review they are now one component
 * (FolderPicker) with explicit ``contentMode`` and ``shell`` props.
 * This file remains so callers can import a named modal without
 * having to spell out every flag -- delete it once every call site
 * has been migrated to the unified FolderPicker directly.
 */

import { FolderPicker } from "@/components/FolderPicker";

interface DirectoryPickerModalProps {
  /** Where to start. Defaults to the server's home-dir fallback. */
  initialPath?: string | null;
  /** Called with the absolute path of the chosen directory. */
  onSelect: (path: string) => void;
  onCancel: () => void;
}

export function DirectoryPickerModal({
  initialPath,
  onSelect,
  onCancel,
}: DirectoryPickerModalProps) {
  return (
    <FolderPicker
      unbound
      contentMode="directories"
      shell="modal"
      modalTitle="Pick a parent folder"
      modalSubtitle="The project folder will be created inside the directory you choose."
      initialPath={initialPath ?? null}
      onSelect={onSelect}
      onCancel={onCancel}
      selectLabel="Use this folder"
      // The unbound endpoint returns subfolders only at every level; an
      // empty-of-direct-videos folder is still a valid parent dir to
      // create the project in.
      allowEmptyFolder
    />
  );
}
