/**
 * Share Results' way to the highlight reel: shown only when the shooter
 * has one (spec 2026-10-18-highlight-reel-design, part 2).
 */
import { Clapperboard } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";

export function ReelLink({ slug }: { slug: string }) {
  const href = useMatchHref();
  const [exists, setExists] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .getLatestReel(slug)
      .then(() => {
        if (live) setExists(true);
      })
      .catch(() => {
        if (live) setExists(false);
      });
    return () => {
      live = false;
    };
  }, [slug]);

  if (!exists) return null;
  return (
    <Button asChild>
      <Link to={href("highlights", slug)}>
        <Clapperboard className="size-3.5" aria-hidden="true" />
        Watch the highlight reel
      </Link>
    </Button>
  );
}
