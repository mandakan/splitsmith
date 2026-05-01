import { FolderInput } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function Ingest() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Ingest</h1>
        <p className="text-sm text-muted-foreground">
          Drop videos, suggest stage matches, confirm assignments. Implemented
          in <a className="underline" href="https://github.com/mandakan/splitsmith/issues/13">#13</a>.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FolderInput className="size-5" />
            Placeholder
          </CardTitle>
          <CardDescription>
            The Sub 1 foundation lands the app shell + project model. The
            ingest workflow (drop zone, stage cards, primary-video selection,
            re-enterable adds for incremental ingest) is its own sub-issue.
          </CardDescription>
        </CardHeader>
        <CardContent />
      </Card>
    </div>
  );
}
