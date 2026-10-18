/**
 * The colour grade gallery's lists (``GET /api/grades`` and, on the desktop,
 * ``/api/grades/user``), fetched once per page load and shared: the Grade row
 * draws them and the Export page checks a stored choice against them before
 * it reaches a request (``lib/grades.gradeFields``). ``loaded`` stays false
 * until both answered, so a choice is never judged against a list still on
 * its way.
 */
import { useEffect, useState } from "react";

import { api, type GradeInfo } from "./api";

export interface GradeCatalog {
  presets: GradeInfo[];
  user: GradeInfo[];
  loaded: boolean;
}

const EMPTY: GradeCatalog = { presets: [], user: [], loaded: false };
let cached: { hosted: boolean; catalog: GradeCatalog } | null = null;
let pending: Promise<GradeCatalog> | null = null;
const listeners = new Set<(c: GradeCatalog) => void>();

/** LUTs uploaded in this page load: a list fetched before the upload landed
 *  must not drop them. */
let uploaded: GradeInfo[] = [];

function publish(fetched: GradeCatalog, hosted: boolean): void {
  const extra = uploaded.filter((u) => !fetched.user.some((f) => f.id === u.id));
  const catalog = { ...fetched, user: [...fetched.user, ...extra] };
  cached = { hosted, catalog };
  for (const listener of listeners) listener(catalog);
}

async function fetchCatalog(hosted: boolean): Promise<GradeCatalog> {
  const presets = await api
    .listGrades()
    .then((r) => r.presets)
    .catch(() => [] as GradeInfo[]);
  const user = hosted
    ? []
    : await api
        .listUserGrades()
        .then((r) => r.user)
        .catch(() => [] as GradeInfo[]);
  return { presets, user, loaded: true };
}

/** A LUT just uploaded joins the list every Grade row and the page see. */
export function addUserGrade(added: GradeInfo): void {
  // Before the lists answered too: the fetch that lands later lists it anyway.
  const { catalog, hosted } = cached ?? { catalog: EMPTY, hosted: false };
  if (!uploaded.some((u) => u.id === added.id)) uploaded = [...uploaded, added];
  publish(catalog, hosted);
}

/** Test seam: forget the fetched lists. */
export function resetGradeCatalog(): void {
  cached = null;
  pending = null;
  uploaded = [];
}

export function useGrades(hosted: boolean): GradeCatalog {
  const [catalog, setCatalog] = useState<GradeCatalog>(
    cached && cached.hosted === hosted ? cached.catalog : EMPTY,
  );
  useEffect(() => {
    listeners.add(setCatalog);
    if (!cached || !cached.catalog.loaded || cached.hosted !== hosted) {
      pending ??= fetchCatalog(hosted).then((c) => {
        publish(c, hosted);
        pending = null;
        return c;
      });
    } else {
      setCatalog(cached.catalog);
    }
    return () => {
      listeners.delete(setCatalog);
    };
  }, [hosted]);
  return catalog;
}
