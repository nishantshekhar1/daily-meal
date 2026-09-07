import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Pencil, UserMinus, Baby, User, Users } from "lucide-react";
import { TopBar } from "@/components/layout/TopBar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { apiFetch } from "@/lib/utils";
import type { HouseholdMember } from "@/types";

const KIND_LABELS: Record<string, string> = {
  adult_male: "Adult Male",
  adult_female: "Adult Female",
  child: "Child (2–12y)",
  toddler: "Toddler (0–24m)",
};

const KIND_ENERGY: Record<string, number> = {
  adult_male: 2500,
  adult_female: 2000,
  child: 1600,
  toddler: 1000,
};

function MemberIcon({ kind }: { kind: string }) {
  if (kind === "toddler") return <Baby className="h-5 w-5 text-primary" />;
  return <User className="h-5 w-5 text-muted-foreground" />;
}

export default function HouseholdPage() {
  const qc = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<HouseholdMember | null>(null);

  const { data: members = [] } = useQuery<HouseholdMember[]>({
    queryKey: ["household"],
    queryFn: () => apiFetch("/household/members"),
  });

  const [form, setForm] = useState({ name: "", kind: "adult_male", age_months: "", dietary_notes: "" });

  const openAdd = () => {
    setEditing(null);
    setForm({ name: "", kind: "adult_male", age_months: "", dietary_notes: "" });
    setDialogOpen(true);
  };

  const openEdit = (m: HouseholdMember) => {
    setEditing(m);
    setForm({
      name: m.name,
      kind: m.kind,
      age_months: m.age_months?.toString() ?? "",
      dietary_notes: m.dietary_notes ?? "",
    });
    setDialogOpen(true);
  };

  const save = useMutation({
    mutationFn: () => {
      const payload = {
        name: form.name,
        kind: form.kind,
        age_months: form.age_months ? parseInt(form.age_months) : null,
        dietary_notes: form.dietary_notes || null,
      };
      if (editing) {
        return apiFetch(`/household/members/${editing.id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
      }
      return apiFetch("/household/members", { method: "POST", body: JSON.stringify(payload) });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["household"] });
      setDialogOpen(false);
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) =>
      apiFetch(`/household/members/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["household"] }),
  });

  const active = members.filter((m) => m.active);
  const totalEnergy = active.reduce((sum, m) => sum + (KIND_ENERGY[m.kind] ?? 0), 0);

  return (
    <div className="pb-24">
      <TopBar
        title="Household"
        subtitle={`${active.length} member${active.length !== 1 ? "s" : ""}`}
        right={
          <Button size="icon" onClick={openAdd}>
            <Plus className="h-4 w-4" />
          </Button>
        }
      />

      <div className="px-4 py-4 max-w-lg mx-auto space-y-4">
        {active.length === 0 && (
          <Card>
            <CardContent className="py-10 text-center">
              <Users className="h-10 w-10 mx-auto text-muted-foreground mb-3" />
              <p className="font-medium">No household members</p>
              <p className="text-sm text-muted-foreground mt-1">
                Add members so the AI can scale portions and apply toddler safety rules.
              </p>
              <Button className="mt-4" onClick={openAdd}>
                Add First Member
              </Button>
            </CardContent>
          </Card>
        )}

        {active.length > 0 && (
          <div className="text-xs text-muted-foreground text-center">
            Combined daily energy target: ~{totalEnergy.toLocaleString()} kcal
          </div>
        )}

        {active.map((m) => (
          <Card key={m.id}>
            <CardContent className="p-4">
              <div className="flex items-center gap-3">
                <div className="rounded-full bg-secondary p-2">
                  <MemberIcon kind={m.kind} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-sm">{m.name}</p>
                  <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                    <Badge variant="secondary" className="text-xs">
                      {KIND_LABELS[m.kind]}
                    </Badge>
                    {m.age_months && (
                      <span className="text-xs text-muted-foreground">
                        {m.age_months} months old
                      </span>
                    )}
                    {m.kind === "toddler" && (
                      <Badge variant="warning" className="text-xs">Safety rules active</Badge>
                    )}
                  </div>
                  {m.dietary_notes && (
                    <p className="text-xs text-muted-foreground mt-1">{m.dietary_notes}</p>
                  )}
                </div>
                <div className="flex gap-1">
                  <Button size="icon" variant="ghost" className="h-8 w-8" onClick={() => openEdit(m)}>
                    <Pencil className="h-3 w-3" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-8 w-8 text-destructive"
                    onClick={() => remove.mutate(m.id)}
                  >
                    <UserMinus className="h-3 w-3" />
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit Member" : "Add Household Member"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-sm font-medium">Name</label>
              <Input
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Dad, Emma..."
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-sm font-medium">Type</label>
              <select
                className="mt-1 flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                value={form.kind}
                onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value }))}
              >
                {Object.entries(KIND_LABELS).map(([val, label]) => (
                  <option key={val} value={val}>{label}</option>
                ))}
              </select>
            </div>
            {(form.kind === "toddler" || form.kind === "child") && (
              <div>
                <label className="text-sm font-medium">
                  Age {form.kind === "toddler" ? "(months)" : "(years — convert to months)"}
                </label>
                <Input
                  type="number"
                  min="0"
                  value={form.age_months}
                  onChange={(e) => setForm((f) => ({ ...f, age_months: e.target.value }))}
                  placeholder={form.kind === "toddler" ? "e.g. 18" : "e.g. 60 for 5 years"}
                  className="mt-1"
                />
                {form.kind === "toddler" && (
                  <p className="text-xs text-muted-foreground mt-1">
                    Age in months is used for safety rules (honey, nuts, salt limits).
                  </p>
                )}
              </div>
            )}
            <div>
              <label className="text-sm font-medium">Dietary notes (optional)</label>
              <Input
                value={form.dietary_notes}
                onChange={(e) => setForm((f) => ({ ...f, dietary_notes: e.target.value }))}
                placeholder="e.g. vegetarian, lactose-free..."
                className="mt-1"
              />
            </div>
            <Button
              className="w-full"
              disabled={!form.name || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? "Saving…" : editing ? "Save Changes" : "Add Member"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
