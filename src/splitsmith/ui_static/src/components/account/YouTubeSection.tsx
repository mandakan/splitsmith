/**
 * The Account page's YouTube section (issue #1000, phase 2): the one
 * channel this account uploads to. Connect, the channel's name once
 * connected, Disconnect. The upload choices themselves live on the
 * Export page's row (components/export/YouTubeConnect), which shares the
 * login through useYouTubeLogin; this section only owns the connection.
 *
 * Mounted hosted only: local mode has no Account page.
 */
import { MoreHorizontal } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useYouTubeLogin } from "@/components/export/useYouTubeLogin";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import { api, apiErrorText, type YouTubeSettings } from "@/lib/api";

export function YouTubeSection() {
  const [settings, setSettings] = useState<YouTubeSettings | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const reload = useCallback(async () => {
    try {
      setSettings(await api.getYouTubeSettings());
    } catch {
      setSettings(null);
    }
  }, []);
  useEffect(() => {
    void reload();
  }, [reload]);
  const { pending, error, connect, cancel, setError } = useYouTubeLogin(() => void reload());

  async function disconnect() {
    setMenuOpen(false);
    try {
      await api.disconnectYouTube();
    } catch (e) {
      setError(apiErrorText(e, "Could not disconnect - check the app and retry."));
      return;
    }
    void reload();
  }

  if (settings === null) return null;

  return (
    <section className="rounded-[10px] border border-rule bg-surface" aria-labelledby="account-youtube">
      <div className="border-b border-rule px-3.5 py-2">
        <Label id="account-youtube">YouTube</Label>
      </div>
      <Field
        label="Channel"
        help="Rendered match videos upload to this channel from the Export page. splitsmith only ever uploads; it never reads your videos or comments."
        error={error}
      >
        {!settings.configured ? (
          <p className="text-md text-muted">YouTube upload is not configured on this server.</p>
        ) : !settings.connected ? (
          <div className="flex items-center gap-3">
            {pending ? (
              <>
                <span className="text-md text-ink-2">Waiting for Google...</span>
                <Button type="button" variant="ghost" size="sm" onClick={cancel}>
                  Cancel
                </Button>
              </>
            ) : (
              <Button type="button" variant="default" size="sm" onClick={() => void connect()}>
                Connect YouTube
              </Button>
            )}
          </div>
        ) : (
          <div className="relative flex items-center gap-2">
            <span className="text-md text-ink-2">Connected as {settings.channel_title ?? "your channel"}</span>
            <Button
              size="icon"
              variant="ghost"
              aria-label="YouTube actions"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <MoreHorizontal className="size-4" aria-hidden />
            </Button>
            <Menu open={menuOpen} onClose={() => setMenuOpen(false)} align="left">
              <button type="button" role="menuitem" className={menuItemClass} onClick={() => void disconnect()}>
                Disconnect
              </button>
            </Menu>
          </div>
        )}
      </Field>
    </section>
  );
}
