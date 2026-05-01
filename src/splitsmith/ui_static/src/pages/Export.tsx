import { FileBarChart } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function Export() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Analysis &amp; Export</h1>
        <p className="text-sm text-muted-foreground">
          Per-stage shot table, anomalies, output toggles, regenerate. Implemented in{" "}
          <a className="underline" href="https://github.com/mandakan/splitsmith/issues/17">
            #17
          </a>
          .
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileBarChart className="size-5" />
            Placeholder
          </CardTitle>
          <CardDescription>
            Wraps existing <code>csv_gen.py</code>, <code>fcpxml_gen.py</code>,{" "}
            <code>report.py</code> with a UI; produces files identical to today's
            CLI output.
          </CardDescription>
        </CardHeader>
        <CardContent />
      </Card>
    </div>
  );
}
