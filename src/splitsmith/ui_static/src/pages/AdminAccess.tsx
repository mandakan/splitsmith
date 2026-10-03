/**
 * Access (admin) -- hosted access requests and account tiers (spec
 * 2026-10-03). The pending queue is approved with a tier picked on the
 * row or declined; decided requests fold away below it, with Resend on an
 * approval whose sign-in mail never went out. Every account is listed
 * with its tier and a row menu to move it.
 *
 * Server-wide like Workers, so it mounts under RootLayout with no shell
 * nav. No primary button: every action here is per row. After any action
 * both lists reload, so a decision made elsewhere shows up too.
 */
import { MoreHorizontal } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Section } from "@/components/export/Section";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import { Label } from "@/components/ui/Label";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import { PageHeader } from "@/components/ui/PageHeader";
import { Segmented } from "@/components/ui/Segmented";
import { approveTierDefault, requestRows, type RequestRow } from "@/lib/adminAccess";
import { ApiError, api, type AccessRequest, type AccessTiers, type AdminAccount } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const ALREADY_DECIDED = "Someone else already decided this request.";

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.detail : String(e);
}

interface PendingRowProps {
  row: RequestRow;
  tiers: AccessTiers;
  busy: boolean;
  onApprove: (tier: string) => void;
  onDecline: () => void;
}

function PendingRow({ row, tiers, busy, onApprove, onDecline }: PendingRowProps) {
  const [tier, setTier] = useState(() => approveTierDefault(tiers));
  return (
    <Tr>
      <Td kind="name" className="max-w-[16rem] truncate" title={row.email}>
        {row.email}
      </Td>
      <Td className="max-w-[18rem] truncate text-muted" title={row.note ?? undefined}>
        {row.note ?? ""}
      </Td>
      <Td className="whitespace-nowrap">{row.sourceLabel}</Td>
      <Td kind="num" className="whitespace-nowrap">
        {row.age}
      </Td>
      <Td>
        <Segmented
          label={`Tier for ${row.email}`}
          value={tier}
          options={tiers.tiers.map((t) => ({ value: t.name, label: t.name }))}
          onChange={setTier}
          disabled={busy}
          className="flex-nowrap"
        />
      </Td>
      <Td className="whitespace-nowrap text-right">
        <span className="inline-flex items-center gap-1">
          <Button type="button" size="sm" disabled={busy} onClick={() => onApprove(tier)}>
            Approve
          </Button>
          <Button type="button" variant="destructive" size="sm" disabled={busy} onClick={onDecline}>
            Decline
          </Button>
        </span>
      </Td>
    </Tr>
  );
}

interface UserRowProps {
  account: AdminAccount;
  tiers: AccessTiers;
  busy: boolean;
  onSetTier: (tier: string) => void;
}

function UserRow({ account, tiers, busy, onSetTier }: UserRowProps) {
  const [open, setOpen] = useState(false);
  return (
    <Tr>
      <Td kind="name" className="max-w-[16rem] truncate" title={account.email}>
        {account.email}
      </Td>
      <Td className="max-w-[12rem] truncate">{account.display_name ?? ""}</Td>
      <Td className="whitespace-nowrap">
        <span className="inline-flex items-center gap-2">
          {account.access_tier}
          {account.is_admin ? (
            <>
              <Chip>Admin</Chip>
              <span className="text-sm text-muted">tier has no effect</span>
            </>
          ) : null}
        </span>
      </Td>
      <Td className="numeral whitespace-nowrap text-muted" title={account.created_at}>
        {account.created_at.slice(0, 10)}
      </Td>
      <Td className="text-right">
        <span className="relative inline-block">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={busy}
            aria-label={`Set tier for ${account.email}`}
            aria-haspopup="menu"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            <MoreHorizontal aria-hidden="true" />
          </Button>
          <Menu open={open} onClose={() => setOpen(false)} align="right">
            {tiers.tiers.map((t) => (
              <button
                key={t.name}
                type="button"
                role="menuitem"
                className={menuItemClass}
                disabled={t.name === account.access_tier}
                onClick={() => {
                  setOpen(false);
                  onSetTier(t.name);
                }}
              >
                Set tier: {t.name}
              </button>
            ))}
          </Menu>
        </span>
      </Td>
    </Tr>
  );
}

export function AdminAccess() {
  const { user } = useAuth();
  const [requests, setRequests] = useState<AccessRequest[] | null>(null);
  const [users, setUsers] = useState<AdminAccount[] | null>(null);
  const [tiers, setTiers] = useState<AccessTiers | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [decidedOpen, setDecidedOpen] = useState(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const loadLists = async () => {
    try {
      const [reqs, accts] = await Promise.all([api.adminAccessRequests(), api.adminUsers()]);
      if (!mountedRef.current) return;
      setRequests(reqs);
      setUsers(accts);
      setFetchError(null);
    } catch (e) {
      if (mountedRef.current) setFetchError(errorText(e));
    }
  };

  useEffect(() => {
    if (!user?.is_admin) return;
    void (async () => {
      try {
        const t = await api.adminAccessTiers();
        if (mountedRef.current) setTiers(t);
      } catch (e) {
        if (mountedRef.current) setFetchError(errorText(e));
      }
    })();
    void loadLists();
  }, [user?.is_admin]); // loadLists closes over only stable refs (setters, refs, module api)

  /** Run one row action, then reload both lists whatever the outcome: a
   *  409 means the lists are stale, and a success changes both. */
  const act = async (id: string, call: () => Promise<unknown>) => {
    setBusyId(id);
    setActionError(null);
    setNotice(null);
    try {
      await call();
    } catch (e) {
      if (!mountedRef.current) return;
      if (e instanceof ApiError && e.status === 409) setNotice(ALREADY_DECIDED);
      else setActionError(errorText(e));
    } finally {
      if (mountedRef.current) setBusyId(null);
    }
    await loadLists();
  };

  const { pending, decided } = useMemo(
    () => (requests ? requestRows(requests, new Date()) : { pending: [], decided: [] }),
    [requests],
  );

  const back = { label: "Matches", to: "/pick" };

  if (!user?.is_admin) {
    return (
      <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-4 px-7 py-5">
        <PageHeader title="Access" back={back} />
        <p className="text-md text-muted">Admin access required.</p>
      </div>
    );
  }

  const loading = (requests === null || users === null || tiers === null) && !fetchError;

  return (
    <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-6 px-7 py-5">
      <PageHeader title="Access" back={back} />

      {fetchError ? (
        <p role="alert" className="text-sm text-destructive">
          {fetchError}
        </p>
      ) : null}
      {actionError ? (
        <p role="alert" className="text-sm text-destructive">
          {actionError}
        </p>
      ) : null}
      {notice ? <p className="text-sm text-muted">{notice}</p> : null}

      {loading ? <p className="text-sm text-muted">Loading...</p> : null}

      {requests !== null && tiers !== null ? (
        <section aria-label="Requests" className="flex flex-col gap-2">
          <Label>Requests</Label>
          {pending.length === 0 ? (
            <p className="text-sm text-muted">No pending requests.</p>
          ) : (
            <Table>
              <thead>
                <tr className="whitespace-nowrap">
                  <Th>Email</Th>
                  <Th>Note</Th>
                  <Th>Source</Th>
                  <Th align="right">Age</Th>
                  <Th>Tier</Th>
                  <Th align="right">
                    <span className="sr-only">Actions</span>
                  </Th>
                </tr>
              </thead>
              <tbody>
                {pending.map((row) => (
                  <PendingRow
                    key={row.id}
                    row={row}
                    tiers={tiers}
                    busy={busyId === row.id}
                    onApprove={(tier) => void act(row.id, () => api.adminApproveAccessRequest(row.id, tier))}
                    onDecline={() => void act(row.id, () => api.adminDeclineAccessRequest(row.id))}
                  />
                ))}
              </tbody>
            </Table>
          )}

          {decided.length > 0 ? (
            <Section
              label="Decided"
              summary={`${decided.length} ${decided.length === 1 ? "request" : "requests"}`}
              open={decidedOpen}
              onToggle={() => setDecidedOpen((o) => !o)}
              flush
            >
              <Table>
                <thead>
                  <tr className="whitespace-nowrap">
                    <Th>Email</Th>
                    <Th>Status</Th>
                    <Th>Tier</Th>
                    <Th>Decided by</Th>
                    <Th align="right">Age</Th>
                    <Th align="right">
                      <span className="sr-only">Actions</span>
                    </Th>
                  </tr>
                </thead>
                <tbody>
                  {decided.map((row) => (
                    <Tr key={row.id}>
                      <Td kind="name" className="max-w-[16rem] truncate" title={row.email}>
                        {row.email}
                      </Td>
                      <Td>
                        <Chip tone={row.status === "approved" ? "ok" : "neutral"}>{row.status}</Chip>
                      </Td>
                      <Td>{row.tierGranted ?? ""}</Td>
                      <Td className="max-w-[14rem] truncate text-muted">{row.decidedBy ?? ""}</Td>
                      <Td kind="num" className="whitespace-nowrap">
                        {row.age}
                      </Td>
                      <Td className="whitespace-nowrap text-right">
                        {row.mailFailed ? (
                          <span className="inline-flex items-center gap-2">
                            <Chip tone="warn">Email not sent</Chip>
                            <Button
                              type="button"
                              size="sm"
                              disabled={busyId === row.id}
                              onClick={() => void act(row.id, () => api.adminResendAccessRequest(row.id))}
                            >
                              Resend
                            </Button>
                          </span>
                        ) : null}
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </Section>
          ) : null}
        </section>
      ) : null}

      {users !== null && tiers !== null ? (
        <section aria-label="Users" className="flex flex-col gap-2">
          <Label>Users</Label>
          <Table>
            <thead>
              <tr className="whitespace-nowrap">
                <Th>Email</Th>
                <Th>Name</Th>
                <Th>Tier</Th>
                <Th>Created</Th>
                <Th align="right">
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {users.map((a) => (
                <UserRow
                  key={a.id}
                  account={a}
                  tiers={tiers}
                  busy={busyId === a.id}
                  onSetTier={(tier) => void act(a.id, () => api.adminSetUserTier(a.id, tier))}
                />
              ))}
            </tbody>
          </Table>
        </section>
      ) : null}
    </div>
  );
}
