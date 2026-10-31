"use client";

import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef, type ICellRendererParams } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { api, ApiError } from "@/api/client";
import { humanize, money, reasonLabel, whenShort } from "@/api/format";
import type { QueueRow } from "@/api/types";
import { FaultTag, IntegrityTag } from "./FaultTag";

ModuleRegistry.registerModules([AllCommunityModule]);

const theme = themeQuartz.withParams({
  fontFamily: "var(--font-plex-condensed), sans-serif",
  headerFontFamily: "var(--font-plex-mono), monospace",
  headerFontSize: 11,
  headerFontWeight: 500,
  headerTextColor: "#6e6a5e",
  fontSize: 14,
  backgroundColor: "#fbf9f4",
  foregroundColor: "#17160f",
  headerBackgroundColor: "#ebe6da",
  oddRowBackgroundColor: "#fbf9f4",
  rowHoverColor: "#f3efe6",
  selectedRowBackgroundColor: "#f6ead2",
  borderColor: "#d6cfbf",
  accentColor: "#f05a0a",
  wrapperBorder: false,
  wrapperBorderRadius: 0,
  borderRadius: 0,
  rowHeight: 46,
  headerHeight: 38,
  spacing: 7,
  cellHorizontalPadding: 10,
  headerColumnResizeHandleColor: "#d6cfbf",
});

export function DisputeQueue({ rows, onChanged }: { rows: QueueRow[]; onChanged: () => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const adjudicate = async (row: QueueRow) => {
    setBusy(row.dispute_id);
    setError(null);
    try {
      const res = await api.resolve(row.dispute_id, false); // adjudicate only; PayPal is not changed
      if (res.status === "no_receipt") setError(`${row.dispute_id}: ${res.why}`);
      onChanged();
      window.dispatchEvent(new Event("ledger-changed"));
    } catch (e) {
      setError(e instanceof ApiError ? `${row.dispute_id}: ${JSON.stringify(e.detail)}` : String(e));
    } finally {
      setBusy(null);
    }
  };

  const columns = useMemo<ColDef<QueueRow>[]>(() => [
    // Every column flexes from a minimum; the minimums sum to ~1180px so the grid fits the
    // 1270px page column with no horizontal scroll.
    {
      headerName: "Dispute", field: "dispute_id", minWidth: 250, flex: 2.2, tooltipField: "dispute_id",
      cellRenderer: (p: ICellRendererParams<QueueRow>) => (
        <span className="mono cell-clip">
          {p.data?.simulated && <span className="tag none" style={{ marginRight: 6 }}>sim</span>}{p.value}
        </span>
      ),
    },
    { headerName: "Opened", field: "opened_at", minWidth: 116, flex: 1, sort: "desc", valueFormatter: (p) => whenShort(p.value) },
    { headerName: "Reason", field: "reason", minWidth: 128, flex: 1.1, valueFormatter: (p) => reasonLabel(p.value) },
    {
      headerName: "Amount", field: "amount", minWidth: 84, flex: 0.7, type: "rightAligned",
      valueFormatter: (p) => money(p.value, p.data?.currency), cellClass: "mono",
    },
    {
      headerName: "PayPal status", colId: "status", minWidth: 116, flex: 1.3, tooltipValueGetter: (p) => p.value,
      valueGetter: (p) => p.data?.simulated ? "simulated" : [p.data?.status, p.data?.stage].filter(Boolean).map((s) => humanize(s!)).join(" · "),
    },
    {
      headerName: "Fault", colId: "fault", minWidth: 128, flex: 1.1,
      valueGetter: (p) => p.data?.verdict?.fault_class ?? (p.data?.verdict?.status === "needs_review" ? "REVIEW" : ""),
      cellRenderer: (p: ICellRendererParams<QueueRow>) => {
        const v = p.data?.verdict;
        if (!v && p.data) {
          if (p.data.status === "RESOLVED") return <span className="muted">—</span>;
          return (
            <button className="btn small" disabled={busy !== null}
              onClick={(e) => { e.stopPropagation(); adjudicate(p.data!); }}>
              {busy === p.data.dispute_id ? "Adjudicating…" : "Adjudicate"}
            </button>
          );
        }
        return <FaultTag fault={v?.fault_class} status={v?.status} />;
      },
    },
    {
      headerName: "Conf.", minWidth: 62, flex: 0.5, type: "rightAligned", cellClass: "mono",
      valueGetter: (p) => p.data?.verdict?.confidence ?? null, valueFormatter: (p) => (p.value == null ? "—" : p.value.toFixed(2)),
    },
    {
      headerName: "Integrity", minWidth: 116, flex: 1, valueGetter: (p) => p.data?.verdict?.integrity ?? null,
      cellRenderer: (p: ICellRendererParams<QueueRow>) =>
        p.data?.verdict ? <IntegrityTag status={p.value} check={p.data.verdict.integrity_check} compact /> : null,
    },
    { headerName: "Rule", minWidth: 58, flex: 0.5, cellClass: "mono", valueGetter: (p) => p.data?.verdict?.cited_rule ?? "—" },
    {
      headerName: "Filed", minWidth: 90, flex: 0.9, cellClass: "mono", tooltipValueGetter: (p) => p.value,
      valueGetter: (p) => (p.data?.filed.length ? p.data.filed.join(" → ") : "—"),
    },
  ], [busy]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      {error && <div className="error-box" style={{ marginBottom: 10 }}>{error}</div>}
      <div className="grid-wrap" style={{ height: Math.min(560, 40 + 46 * Math.max(rows.length, 4) + 2) }}>
        <AgGridReact<QueueRow>
          theme={theme}
          rowData={rows}
          columnDefs={columns}
          getRowId={(p) => p.data.dispute_id}
          defaultColDef={{ resizable: true, sortable: true }}
          overlayNoRowsTemplate="No disputes yet. Seed a failure above, or open one from the sandbox buyer account."
          onRowClicked={(e) => {
            if (e.data?.receipt_id) router.push(`/receipts/${e.data.receipt_id}?dispute=${encodeURIComponent(e.data.dispute_id)}`);
          }}
          rowStyle={{ cursor: "pointer" }}
        />
      </div>
    </>
  );
}
