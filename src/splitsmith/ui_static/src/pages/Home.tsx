import { Link } from "react-router-dom";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";

interface Health {
  status: string;
  project_name: string;
  project_root: string;
  schema_version: number;
}

export function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [includeTrimmed, setIncludeTrimmed] = useState(false);
  const [includeExports, setIncludeExports] = useState(false);
  const [includeRaw, setIncludeRaw] = useState(false);
  const [includeAudio, setIncludeAudio] = useState(false);
  const [preparing, setPreparing] = useState(false);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then(setHealth)
      .catch((e: Error) => setError(e.message));
  }, []);

  function downloadBackup() {
    const url = api.exportProjectUrl({
      includeTrimmed,
      includeExports,
      includeRaw,
      includeAudio,
    });
    // Navigating in a hidden anchor keeps the user on the page while
    // the browser streams the archive to disk.
    const a = document.createElement("a");
    a.href = url;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    // The browser owns the download from here; we don't get a callback.
    // Show a brief "Preparing..." state so the user sees the click
    // registered -- generating the tarball can take a couple of seconds
    // for a large project.
    setPreparing(true);
    window.setTimeout(() => setPreparing(false), 4000);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Match overview</h1>
        <p className="text-sm text-muted-foreground">
          Welcome. This is the v1 shell — ingest, audit and export screens
          land in their own sub-issues (#13, #15, #17).
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Project</CardTitle>
          <CardDescription>
            Loaded from <code>project.json</code> at the path passed to{" "}
            <code>splitsmith ui --project</code>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {error ? (
            <p className="text-sm text-destructive">Failed to load: {error}</p>
          ) : !health ? (
            <div className="space-y-2">
              <Skeleton className="h-4 w-1/3" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          ) : (
            <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Name</dt>
              <dd className="font-medium">{health.project_name}</dd>
              <dt className="text-muted-foreground">Root</dt>
              <dd className="font-mono text-xs">{health.project_root}</dd>
              <dt className="text-muted-foreground">Schema</dt>
              <dd className="font-mono text-xs">v{health.schema_version}</dd>
            </dl>
          )}
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-2">
        <Button asChild>
          <Link to="/ingest">Go to ingest</Link>
        </Button>
        <Button asChild variant="secondary">
          <Link to="/audit">Open audit</Link>
        </Button>
        <Button asChild variant="outline">
          <Link to="/_design">View design system</Link>
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Backup</CardTitle>
          <CardDescription>
            Download a <code>.tar.gz</code> of this project. The default
            archive holds only the irreplaceable bits: <code>project.json</code>,{" "}
            <code>audit/</code> (hand-labeled shot times) and{" "}
            <code>scoreboard/</code>. Tick a box to include directories
            that are regeneratable from the source footage.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={includeTrimmed}
                onChange={(e) => setIncludeTrimmed(e.target.checked)}
              />
              <span>
                Include <code>trimmed/</code>
                <span className="text-xs text-muted-foreground"> (per-stage MP4s)</span>
              </span>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={includeExports}
                onChange={(e) => setIncludeExports(e.target.checked)}
              />
              <span>
                Include <code>exports/</code>
                <span className="text-xs text-muted-foreground"> (FCPXML, CSV)</span>
              </span>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={includeRaw}
                onChange={(e) => setIncludeRaw(e.target.checked)}
              />
              <span>
                Include <code>raw/</code>
                <span className="text-xs text-muted-foreground"> (source video)</span>
              </span>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={includeAudio}
                onChange={(e) => setIncludeAudio(e.target.checked)}
              />
              <span>
                Include <code>audio/</code>
                <span className="text-xs text-muted-foreground"> (extracted wav)</span>
              </span>
            </label>
          </div>
          <div className="flex items-center gap-3">
            <Button
              type="button"
              onClick={downloadBackup}
              disabled={!health || preparing}
            >
              {preparing ? "Preparing archive..." : "Download backup"}
            </Button>
            {preparing ? (
              <span className="text-xs text-muted-foreground">
                The browser will save the file once the server finishes
                writing it.
              </span>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
