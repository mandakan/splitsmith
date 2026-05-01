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

interface Health {
  status: string;
  project_name: string;
  project_root: string;
  schema_version: number;
}

export function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then(setHealth)
      .catch((e: Error) => setError(e.message));
  }, []);

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
    </div>
  );
}
