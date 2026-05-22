"use client";
import * as React from "react";
import { toast } from "sonner";
import { Pencil, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SeverityBadge, Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { categoryLabel } from "@/lib/format";
import { updateRule } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Rule } from "@/lib/types";

interface Props {
  initialRules: Rule[];
}

const CATEGORIES = ["all", "irdai", "brand", "sebi", "regulatory", "seo"] as const;
const SEVERITIES = ["all", "critical", "high", "medium", "low"] as const;

type Cat = (typeof CATEGORIES)[number];
type Sev = (typeof SEVERITIES)[number];
type Active = "all" | "active" | "inactive";

export function RulesTable({ initialRules }: Props) {
  const [rules, setRules] = React.useState<Rule[]>(initialRules);
  const [cat, setCat] = React.useState<Cat>("all");
  const [sev, setSev] = React.useState<Sev>("all");
  const [act, setAct] = React.useState<Active>("active");
  const [editingId, setEditingId] = React.useState<string | null>(null);
  const [editText, setEditText] = React.useState("");
  const [pending, setPending] = React.useState<Set<string>>(new Set());

  const filtered = rules.filter((r) => {
    if (cat !== "all" && r.category.toLowerCase() !== cat) return false;
    if (sev !== "all" && r.severity.toLowerCase() !== sev) return false;
    if (act === "active" && !r.is_active) return false;
    if (act === "inactive" && r.is_active) return false;
    return true;
  });

  const setBusy = (id: string, busy: boolean) =>
    setPending((p) => {
      const next = new Set(p);
      busy ? next.add(id) : next.delete(id);
      return next;
    });

  const toggle = async (r: Rule) => {
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
  };
  const cancelEdit = () => {
    setEditingId(null);
    setEditText("");
  };
  const saveEdit = async () => {
    if (!editingId) return;
    setBusy(editingId, true);
    try {
      await updateRule(editingId, { rule_text: editText });
      setRules((rs) => rs.map((x) => (x.id === editingId ? { ...x, rule_text: editText } : x)));
      toast.success("Rule updated");
      cancelEdit();
    } catch (e) {
      toast.error(`Update failed: ${(e as Error).message}`);
    } finally {
      setBusy(editingId, false);
    }
  };

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Chips label="Category" options={CATEGORIES} value={cat} onChange={(v) => setCat(v as Cat)} />
        <Chips label="Severity" options={SEVERITIES} value={sev} onChange={(v) => setSev(v as Sev)} />
        <Chips
          label="Status"
          options={["all", "active", "inactive"] as const}
          value={act}
          onChange={(v) => setAct(v as Active)}
        />
      </div>

      <div className="overflow-hidden rounded-md border border-border bg-surface">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-background text-left">
              <th className="px-4 py-3 micro-label w-[110px]">Category</th>
              <th className="px-4 py-3 micro-label w-[90px]">Severity</th>
              <th className="px-4 py-3 micro-label">Rule</th>
              <th className="px-4 py-3 micro-label w-[110px] text-right">Status</th>
              <th className="px-4 py-3 micro-label w-[100px] text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-muted-foreground">
                  No rules match this filter.
                </td>
              </tr>
            ) : (
              filtered.map((r) => (
                <tr key={r.id} className="border-b border-border last:border-0 align-top">
                  <td className="px-4 py-3"><Badge>{categoryLabel(r.category)}</Badge></td>
                  <td className="px-4 py-3"><SeverityBadge severity={r.severity} /></td>
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
                      disabled={pending.has(r.id)}
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
                      <Button variant="ghost" size="icon" onClick={() => startEdit(r)}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{filtered.length} of {rules.length} rules</p>
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
