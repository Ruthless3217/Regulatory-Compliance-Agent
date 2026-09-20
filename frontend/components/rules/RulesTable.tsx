"use client";
import * as React from "react";
import { toast } from "sonner";
import { Pencil, Check, X, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SeverityBadge, Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { categoryLabel } from "@/lib/format";
import { updateRule, deleteRule } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Rule } from "@/lib/types";
import { useAuth } from "@/components/auth/AuthProvider";

interface Props {
  initialRules: Rule[];
}

const CATEGORIES = ["all", "irdai", "brand", "sebi", "regulatory", "seo"] as const;
const SEVERITIES = ["all", "critical", "high", "medium", "low"] as const;
const PRODUCT_SCOPES = [
  "global", "term", "ulip", "par", "non_par", "savings_endowment",
  "pension_annuity", "health", "rider", "group",
] as const;

type Cat = (typeof CATEGORIES)[number];
type Sev = (typeof SEVERITIES)[number];
type Active = "all" | "active" | "inactive";

const PAGE_SIZE = 50;

export function RulesTable({ initialRules }: Props) {
  const { me } = useAuth();
  const canEdit = me?.role !== "user";

  const [rules, setRules] = React.useState<Rule[]>(initialRules);
  const [cat, setCat] = React.useState<Cat>("all");
  const [sev, setSev] = React.useState<Sev>("all");
  const [act, setAct] = React.useState<Active>("active");
  const [page, setPage] = React.useState(0);
  const [editingId, setEditingId] = React.useState<string | null>(null);
  const [editText, setEditText] = React.useState("");
  const [editScope, setEditScope] = React.useState("");
  const [pending, setPending] = React.useState<Set<string>>(new Set());

  const filtered = rules.filter((r) => {
    if (cat !== "all" && r.category.toLowerCase() !== cat) return false;
    if (sev !== "all" && r.severity.toLowerCase() !== sev) return false;
    if (act === "active" && !r.is_active) return false;
    if (act === "inactive" && r.is_active) return false;
    return true;
  });

  // Clamp rather than reset: deleting the last row on the last page must not
  // strand the view on an empty page.
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(page, pageCount - 1);
  const start = current * PAGE_SIZE;
  const visible = filtered.slice(start, start + PAGE_SIZE);

  const setBusy = (id: string, busy: boolean) =>
    setPending((p) => {
      const next = new Set(p);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });

  const toggle = async (r: Rule) => {
    if (
      !r.is_active &&
      !PRODUCT_SCOPES.includes((r.product_line ?? "") as (typeof PRODUCT_SCOPES)[number])
    ) {
      startEdit(r);
      toast.error("Choose an explicit product scope before activating this legacy rule");
      return;
    }
    setBusy(r.id, true);
    try {
      await updateRule(r.id, { is_active: !r.is_active });
      setRules((rs) => rs.map((x) => (x.id === r.id ? { ...x, is_active: !x.is_active } : x)));
    } catch (e) {
      toast.error(`Toggle failed: ${(e as Error).message}`);
    } finally {
      setBusy(r.id, false);
    }
  };

  const startEdit = (r: Rule) => {
    setEditingId(r.id);
    setEditText(r.rule_text);
    setEditScope(r.product_line ?? "");
  };
  const cancelEdit = () => {
    setEditingId(null);
    setEditText("");
    setEditScope("");
  };
  const saveEdit = async () => {
    if (!editingId) return;
    if (!editScope) {
      toast.error("Choose an explicit product scope before saving");
      return;
    }
    setBusy(editingId, true);
    try {
      const updated = await updateRule(editingId, {
        rule_text: editText,
        product_line: editScope,
      });
      setRules((rs) => rs.map((x) => (x.id === editingId ? { ...x, ...updated } : x)));
      toast.success("Rule updated");
      cancelEdit();
    } catch (e) {
      toast.error(`Update failed: ${(e as Error).message}`);
    } finally {
      setBusy(editingId, false);
    }
  };

  const remove = async (r: Rule) => {
    const preview = r.rule_text.length > 120 ? `${r.rule_text.slice(0, 120)}…` : r.rule_text;
    if (!confirm(`Delete this rule? This cannot be undone.\n\n"${preview}"`)) return;
    setBusy(r.id, true);
    try {
      await deleteRule(r.id);
      setRules((rs) => rs.filter((x) => x.id !== r.id));
      if (editingId === r.id) cancelEdit();
      toast.success("Rule deleted");
    } catch (e) {
      toast.error(`Delete failed: ${(e as Error).message}`);
    } finally {
      setBusy(r.id, false);
    }
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Chips
          label="Category"
          options={CATEGORIES}
          value={cat}
          onChange={(v) => { setCat(v as Cat); setPage(0); }}
        />
        <Chips
          label="Severity"
          options={SEVERITIES}
          value={sev}
          onChange={(v) => { setSev(v as Sev); setPage(0); }}
        />
        <Chips
          label="Status"
          options={["all", "active", "inactive"] as const}
          value={act}
          onChange={(v) => { setAct(v as Active); setPage(0); }}
        />
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-background shadow-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-muted/30 text-left">
              <th className="px-4 py-3 micro-label w-[110px]">Category</th>
              <th className="px-4 py-3 micro-label w-[90px]">Severity</th>
              <th className="px-4 py-3 micro-label w-[150px]">Product scope</th>
              <th className="px-4 py-3 micro-label">Rule</th>
              <th className="px-4 py-3 micro-label w-[110px] text-right">Status</th>
              {canEdit && <th className="px-4 py-3 micro-label w-[120px] text-right">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={canEdit ? 6 : 5} className="px-4 py-10 text-center text-muted-foreground">
                  No rules match this filter.
                </td>
              </tr>
            ) : (
              visible.map((r) => (
                <tr key={r.id} className="border-b border-border last:border-0 align-top">
                  <td className="px-4 py-3"><Badge>{categoryLabel(r.category)}</Badge></td>
                  <td className="px-4 py-3"><SeverityBadge severity={r.severity} /></td>
                  <td className="px-4 py-3">
                    {editingId === r.id ? (
                      <select
                        value={editScope}
                        onChange={(e) => setEditScope(e.target.value)}
                        className="h-9 w-full rounded-md border border-input bg-background px-2 text-xs"
                      >
                        <option value="">Select scope</option>
                        {PRODUCT_SCOPES.map((scope) => (
                          <option key={scope} value={scope}>{categoryLabel(scope)}</option>
                        ))}
                      </select>
                    ) : (
                      <Badge>{r.product_line ? categoryLabel(r.product_line) : "Unclassified"}</Badge>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {editingId === r.id ? (
                      <Textarea
                        value={editText}
                        onChange={(e) => setEditText(e.target.value)}
                        rows={3}
                        className="min-h-[60px]"
                      />
                    ) : (
                      <span className="leading-snug">{r.rule_text}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => toggle(r)}
                      disabled={!canEdit || pending.has(r.id)}
                      className={cn(
                        "rounded-sm border px-2 py-0.5 text-xs uppercase tracking-micro transition-colors",
                        r.is_active
                          ? "border-success text-success hover:bg-success/10"
                          : "border-border text-muted-foreground hover:bg-muted"
                      )}
                    >
                      {r.is_active ? "active" : "inactive"}
                    </button>
                  </td>
                  {canEdit && (
                    <td className="px-4 py-3 text-right">
                      {editingId === r.id ? (
                      <div className="inline-flex gap-1">
                        <Button variant="ghost" size="icon" onClick={saveEdit} disabled={pending.has(r.id)}>
                          <Check className="h-4 w-4 text-success" />
                        </Button>
                        <Button variant="ghost" size="icon" onClick={cancelEdit}>
                          <X className="h-4 w-4" />
                        </Button>
                      </div>
                    ) : (
                      <div className="inline-flex gap-1">
                        <Button variant="ghost" size="icon" onClick={() => startEdit(r)} title="Edit rule">
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => remove(r)}
                          disabled={pending.has(r.id)}
                          title="Delete rule"
                          className="text-muted-foreground hover:text-sev-critical"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    )}
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <nav aria-label="Rules pagination" className="mt-2 flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {filtered.length === 0
            ? `0 of ${rules.length} rules`
            : `${start + 1}–${start + visible.length} of ${filtered.length} filtered (${rules.length} total)`}
        </p>
        {pageCount > 1 && (
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage(current - 1)}
              disabled={current === 0}
            >
              Previous
            </Button>
            <span className="text-xs text-muted-foreground" aria-live="polite">
              Page {current + 1} of {pageCount}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage(current + 1)}
              disabled={current >= pageCount - 1}
            >
              Next
            </Button>
          </div>
        )}
      </nav>
    </div>
  );
}

function Chips<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="micro-label">{label}</span>
      {options.map((o) => {
        const active = o === value;
        return (
          <button
            key={o}
            type="button"
            onClick={() => onChange(o)}
            className={cn(
              "rounded-sm border px-2 py-0.5 text-xs transition-colors",
              active
                ? "border-foreground bg-foreground text-background"
                : "border-border text-muted-foreground hover:border-foreground hover:text-foreground"
            )}
          >
            {o === "all" ? "All" : categoryLabel(o)}
          </button>
        );
      })}
    </div>
  );
}
