import { Crosshair } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function Audit() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
        <p className="text-sm text-muted-foreground">
          Multi-video audit screen. Implemented in{" "}
          <a className="underline" href="https://github.com/mandakan/splitsmith/issues/15">
            #15
          </a>
          . Depends on Sub 5 short-GOP trim (#16) for scrub-friendly playback.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Crosshair className="size-5" />
            Placeholder
          </CardTitle>
          <CardDescription>
            The audit screen v2 replaces the existing <code>splitsmith review</code>{" "}
            SPA inside this shell. Single playback source (video element drives
            time), real-time scrubbing, multi-video viewing tabs.
          </CardDescription>
        </CardHeader>
        <CardContent />
      </Card>
    </div>
  );
}
