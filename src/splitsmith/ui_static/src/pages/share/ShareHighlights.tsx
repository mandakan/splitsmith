/**
 * The highlight reel on a share link (spec 2026-10-18-highlight-reel-design,
 * part 2): the newest reel, played through the share surface, which admits
 * exactly the reel's file (``_SHARE_PATH_RE``).
 */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { PageHeader } from "@/components/ui/PageHeader";
import { type LatestReel, api } from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";

export function ShareHighlights() {
  const { slug = "" } = useParams<{ slug: string }>();
  const href = useMatchHref();
  const [reel, setReel] = useState<LatestReel | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .getLatestReel(slug)
      .then((r) => {
        if (live) setReel(r);
      })
      .catch(() => {
        if (live) setMissing(true);
      });
    return () => {
      live = false;
    };
  }, [slug]);

  return (
    <div className="mx-auto w-full max-w-[1100px] px-4 py-4 md:px-7 md:py-5">
      <PageHeader title="Highlight reel" sub={<Link to={href("results")}>Back to the splits</Link>} />
      {missing ? (
        <p className="text-md text-muted">There is no highlight reel for this shooter yet.</p>
      ) : reel ? (
        <video
          data-testid="highlight-reel"
          src={api.exportFileUrl(slug, reel.filename)}
          controls
          playsInline
          preload="metadata"
          className="mx-auto max-h-[80dvh] w-auto max-w-full rounded-[10px] bg-black"
        />
      ) : (
        <p className="text-md text-muted">Loading the reel...</p>
      )}
    </div>
  );
}
