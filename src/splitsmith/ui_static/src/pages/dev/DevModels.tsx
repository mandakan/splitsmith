/**
 * DevModels -- /dev/models: what the shipped detectors were built from and
 * how they were tested (``GET /api/dev/models``). Read only. The map, the
 * flow and the holdout panel share one filter and one highlight; every
 * rule is in ``lib/modelMap``.
 */
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { CompareModelsPanel } from "@/components/developer/models/CompareModelsPanel";
import { CorpusFlow } from "@/components/developer/models/CorpusFlow";
import { CorpusMap, type MapLens } from "@/components/developer/models/CorpusMap";
import { FixtureSheet } from "@/components/developer/models/FixtureSheet";
import { HoldoutPanel } from "@/components/developer/models/HoldoutPanel";
import { ParametersPanel } from "@/components/developer/models/ParametersPanel";
import { RankerPanel } from "@/components/developer/models/RankerPanel";
import { VersionTimeline } from "@/components/developer/models/VersionTimeline";
import { Button, Chip, PageHeader, Segmented, Stat, StatStrip, inputClass } from "@/components/ui";
import { api } from "@/lib/api";
import {
  CLASSIFIER_EXPLAIN,
  NO_FILTER,
  STATUS_LABEL,
  corpusMembership,
  countBy,
  filterFixtures,
  fixtureStatus,
  foldColors,
  matchKey,
  type CameraClass,
  type MapFilter,
  type ModelMap,
  type ModelSummary,
  type SplitName,
} from "@/lib/modelMap";
import { cn } from "@/lib/utils";

export function DevModels() {
  const [data, setData] = useState<ModelMap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<MapFilter>(NO_FILTER);
  // A link that already names a comparison opens on it.
  const [lens, setLens] = useState<MapLens>(() =>
    new URLSearchParams(window.location.search).get("vs") ? "compare" : "status",
  );
  const [split, setSplit] = useState<SplitName>("lomo");
  const [highlight, setHighlight] = useState<Set<string> | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [params, setParams] = useSearchParams();
  const modelId = params.get("model") ?? "shipped";
  const vsId = params.get("vs");
  const [vs, setVs] = useState<ModelMap | null>(null);

  const pick = (key: "model" | "vs", value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    api
      .getDevModels(modelId)
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, [modelId]);

  useEffect(() => {
    let alive = true;
    setVs(null);
    if (!vsId || vsId === modelId) return;
    api
      .getDevModels(vsId)
      .then((d) => {
        if (alive) setVs(d);
      })
      .catch(() => {
        if (alive) setVs(null);
      });
    return () => {
      alive = false;
    };
  }, [vsId, modelId]);

  const membership = useMemo(() => (data && vs ? corpusMembership(data, vs) : null), [data, vs]);
  // The Compare lens needs a second model; drop back when it goes away.
  const effectiveLens: MapLens = lens === "compare" && !membership ? "status" : lens;

  const fixtures = useMemo(() => data?.fixtures ?? [], [data]);
  const shown = useMemo(() => filterFixtures(fixtures, filter), [fixtures, filter]);
  const matches = useMemo(() => [...new Set(fixtures.map(matchKey))].sort(), [fixtures]);
  const folds = useMemo(() => foldColors(matches), [matches]);
  const stems = useMemo(() => new Set(fixtures.map((f) => f.stem)), [fixtures]);
  const status = useMemo(() => countBy(fixtures, fixtureStatus), [fixtures]);
  const selectedFixture = fixtures.find((f) => f.stem === selected) ?? null;

  if (error) {
    return (
      <div className="p-7">
        <PageHeader title="Models" sub="Could not load the model map." />
        <p className="text-sm text-muted">{error}</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="p-7">
        <PageHeader title="Models" sub="Reading the corpus, the calibration and its history..." />
      </div>
    );
  }

  const filtered = JSON.stringify(filter) !== JSON.stringify(NO_FILTER);
  const versions = data.history.length;

  const models = data.models ?? [];
  return (
    <div className="flex flex-col gap-8 p-7">
      <PageHeader
        title="Models"
        sub="What each detector model was built from, how it was tested, and how the corpus got here."
        actions={
          models.length > 1 ? (
            <>
              <ModelSelect label="Model" value={modelId} models={models} onChange={(v) => pick("model", v)} />
              <ModelSelect
                label="Compare with"
                value={vsId ?? ""}
                models={models.filter((m) => m.id !== modelId)}
                onChange={(v) => {
                  pick("vs", v || null);
                  if (v) setLens("compare");
                }}
                none="No comparison"
              />
            </>
          ) : null
        }
      >
        {data.model ? (
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
            <Chip tick={data.model.channel === "shipped" ? "fire" : "movement"}>{data.model.channel}</Chip>
            <span className="numeral text-ink-2">{data.model.version}</span>
            {data.model.description ? <span>{data.model.description}</span> : null}
            {data.model.selection ? <span className="text-subtle">({data.model.selection})</span> : null}
          </div>
        ) : null}
      </PageHeader>

      <StatStrip lead>
        <Stat label="Fixtures" value={String(fixtures.length)} />
        <Stat label="Headcam classifier" value={String(status.headcam ?? 0)} />
        <Stat label="Handheld classifier" value={String(status.handheld ?? 0)} />
        <Stat label="Awaiting review" value={String(status.review ?? 0)} tone={status.review ? "ink" : "dim"} />
        <Stat label="Not in build" value={String(status.unused ?? 0)} tone="dim" />
        <Stat label="Versions" value={String(versions)} unit={data.ensemble.built_at?.slice(0, 10)} />
      </StatStrip>
      <p className="-mt-5 max-w-[860px] text-sm text-muted">{CLASSIFIER_EXPLAIN}</p>

      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Match"
          className={cn(inputClass, "w-auto")}
          value={filter.match ?? ""}
          onChange={(e) => setFilter({ ...filter, match: e.target.value || null })}
        >
          <option value="">All matches</option>
          {matches.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <Segmented
          label="Camera class"
          value={filter.cameraClass ?? "all"}
          options={[
            { value: "all", label: "Both cameras" },
            { value: "headcam", label: "Headcam", tick: "movement" },
            { value: "handheld", label: "Handheld", tick: "transition" },
          ]}
          onChange={(v) => setFilter({ ...filter, cameraClass: v === "all" ? null : (v as CameraClass) })}
        />
        <input
          aria-label="Search fixtures"
          placeholder="Search stem or camera"
          className={cn(inputClass, "w-56")}
          value={filter.text}
          onChange={(e) => setFilter({ ...filter, text: e.target.value })}
        />
        {filter.status ? <Chip>{STATUS_LABEL[filter.status]}</Chip> : null}
        {filtered ? (
          <Button variant="default" size="sm" onClick={() => setFilter(NO_FILTER)}>
            Clear
          </Button>
        ) : null}
        <span className="ml-auto text-sm text-muted">
          <span className="numeral text-ink-2">{shown.length}</span> of {fixtures.length} shown
        </span>
      </div>

      <div className="grid gap-8 2xl:grid-cols-[minmax(0,1fr)_minmax(0,560px)]">
        <CorpusMap
          fixtures={shown}
          lens={effectiveLens}
          onLens={setLens}
          folds={folds}
          highlight={highlight}
          selected={selected}
          onSelect={setSelected}
          membership={membership}
          names={{ a: data.model?.name ?? "A", b: vs?.model?.name ?? "B" }}
        />
        <CorpusFlow
          fixtures={shown}
          onHighlight={setHighlight}
          onPickStatus={(s) => setFilter({ ...filter, status: filter.status === s ? null : s })}
        />
      </div>

      {vs ? <CompareModelsPanel a={data} b={vs} /> : null}

      <HoldoutPanel
        ensemble={data.ensemble}
        fixtures={shown}
        split={split}
        onSplit={(s) => {
          setSplit(s);
          if (s === "lomo") setLens("fold");
        }}
        folds={folds}
        onPickMatch={(m) => setFilter({ ...filter, match: filter.match === m ? null : m })}
      />

      <VersionTimeline history={data.history} known={stems} onSelectFixture={setSelected} />

      {data.beep_ranker ? <RankerPanel ranker={data.beep_ranker} /> : null}

      <ParametersPanel ensemble={data.ensemble} />

      <FixtureSheet fixture={selectedFixture} heads={data.ensemble.heads} onClose={() => setSelected(null)} />
    </div>
  );
}

function ModelSelect({
  label,
  value,
  models,
  onChange,
  none,
}: {
  label: string;
  value: string;
  models: ModelSummary[];
  onChange: (id: string) => void;
  none?: string;
}) {
  return (
    <select
      aria-label={label}
      className={cn(inputClass, "w-auto")}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {none !== undefined ? <option value="">{none}</option> : null}
      {models.map((m) => (
        <option key={m.id} value={m.id}>
          {m.name} {m.version}
        </option>
      ))}
    </select>
  );
}
