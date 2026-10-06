/**
 * What connecting YouTube lets splitsmith do, shown before every login
 * and from the connected channel's menu. Google's consent screen words
 * the ``youtube.force-ssl`` scope as "see, edit, and permanently delete"
 * your videos; captions need that scope, so the app cannot ask for less.
 * This sheet names what the code actually calls (``youtube/client.py``:
 * upload, captions, thumbnail, playlists, the channel's name) so the
 * consent screen is not the first thing a user reads.
 *
 * Every line here is a claim about the client. A new YouTube API call
 * moves the copy in ``lib/youtubeAccess`` and the privacy page's YouTube
 * section (``site/privacy.html#youtube``); the test pins both to the same words.
 *
 * ``onContinue`` set: the pre-login sheet, ending in "Continue to Google".
 * Unset: the same text, read-only, from the connected menu.
 */
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { Sheet } from "@/components/ui/Sheet";
import { useDeploymentMode } from "@/lib/features";
import { DOES, GOOGLE_PERMISSIONS_URL, GOOGLE_SCOPE_WORDING, keyLine, NEVER, PRIVACY_URL } from "@/lib/youtubeAccess";

export interface YouTubeAccessSheetProps {
  open: boolean;
  onClose: () => void;
  /** Starts the login; omitted for the read-only view. */
  onContinue?: () => void;
}

export function YouTubeAccessSheet({ open, onClose, onContinue }: YouTubeAccessSheetProps) {
  const { mode } = useDeploymentMode();
  return (
    <Sheet open={open} onClose={onClose} label="What connecting YouTube allows">
      <div className="border-b border-rule px-4 py-3">
        <Label>Connecting YouTube</Label>
      </div>
      <div className="flex flex-col gap-4 overflow-y-auto px-4 py-4 text-md text-ink-2">
        <p>
          Google will ask you to let splitsmith "{GOOGLE_SCOPE_WORDING}". That is the narrowest permission YouTube offers
          that also covers uploading captions. This is the part of it splitsmith uses.
        </p>
        <section className="flex flex-col gap-1.5" aria-label="What splitsmith does">
          <Label>What it does</Label>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {DOES.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
        <section className="flex flex-col gap-1.5" aria-label="What splitsmith never does">
          <Label>What it never does</Label>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {NEVER.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
        <section className="flex flex-col gap-1.5" aria-label="Where the key is kept">
          <Label>The key</Label>
          <p>{keyLine(mode === "hosted")}</p>
          <p>
            Disconnect deletes it and asks Google to revoke it. You can also remove splitsmith at any time from{" "}
            <a href={GOOGLE_PERMISSIONS_URL} target="_blank" rel="noreferrer" className="underline">
              your Google account's third-party access page
            </a>
            .
          </p>
        </section>
        <p className="text-sm text-muted">
          <a href={PRIVACY_URL} target="_blank" rel="noreferrer" className="underline">
            The privacy policy
          </a>{" "}
          has the full detail.
        </p>
      </div>
      <div className="mt-auto flex justify-end gap-2 border-t border-rule px-4 py-3">
        {onContinue ? (
          <>
            <Button type="button" variant="ghost" size="sm" onClick={onClose}>
              Not now
            </Button>
            <Button type="button" variant="default" size="sm" onClick={onContinue}>
              Continue to Google
            </Button>
          </>
        ) : (
          <Button type="button" variant="default" size="sm" onClick={onClose}>
            Done
          </Button>
        )}
      </div>
    </Sheet>
  );
}
