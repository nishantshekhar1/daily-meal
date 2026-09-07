import { useState, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Receipt, Upload, Check, X, Edit2, Loader2 } from "lucide-react";
import { TopBar } from "@/components/layout/TopBar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { API_BASE, apiFetch } from "@/lib/utils";
import type { ReceiptUploadResult, ReceiptLine } from "@/types";

export default function ReceiptsPage() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<ReceiptUploadResult | null>(null);
  const [lines, setLines] = useState<ReceiptLine[]>([]);
  const [editingLine, setEditingLine] = useState<number | null>(null);

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/receipts/upload`, { method: "POST", body: fd });
      if (!res.ok) throw new Error(await res.text());
      return res.json() as Promise<ReceiptUploadResult>;
    },
    onSuccess: (data) => {
      setResult(data);
      setLines(data.lines);
    },
  });

  const updateLine = async (lineId: number, patch: Partial<ReceiptLine>) => {
    if (!result) return;
    await apiFetch(`/receipts/${result.receipt_id}/lines/${lineId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
    setLines((prev) => prev.map((l) => (l.id === lineId ? { ...l, ...patch } : l)));
  };

  const confirm = useMutation({
    mutationFn: () => apiFetch(`/receipts/${result!.receipt_id}/confirm`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pantry"] });
      setResult(null);
      setLines([]);
    },
  });

  const discard = useMutation({
    mutationFn: () => apiFetch(`/receipts/${result!.receipt_id}`, { method: "DELETE" }),
    onSuccess: () => {
      setResult(null);
      setLines([]);
    },
  });

  const groceryLines = lines.filter((l) => l.is_grocery);
  const confirmedCount = groceryLines.filter((l) => l.confirmed).length;

  return (
    <div className="pb-24">
      <TopBar title="Receipt Scanner" subtitle="Scan to bulk-add ingredients" />

      <div className="px-4 py-4 max-w-lg mx-auto space-y-4">
        {!result && (
          <Card>
            <CardContent className="py-10 flex flex-col items-center gap-4">
              <div className="rounded-full bg-primary/10 p-4">
                <Receipt className="h-8 w-8 text-primary" />
              </div>
              <div className="text-center">
                <p className="font-medium">Scan a grocery receipt</p>
                <p className="text-sm text-muted-foreground mt-1">
                  Take a photo or upload an image. The AI extracts grocery items automatically.
                </p>
              </div>
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                capture="environment"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) upload.mutate(f);
                }}
              />
              <Button
                size="lg"
                className="w-full max-w-xs"
                onClick={() => fileRef.current?.click()}
                disabled={upload.isPending}
              >
                {upload.isPending ? (
                  <>
                    <Loader2 className="h-5 w-5 animate-spin mr-2" />
                    Processing…
                  </>
                ) : (
                  <>
                    <Upload className="h-5 w-5 mr-2" />
                    Take / Upload Photo
                  </>
                )}
              </Button>
            </CardContent>
          </Card>
        )}

        {result && (
          <>
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base">Review Items</CardTitle>
                  <span className="text-sm text-muted-foreground">
                    {confirmedCount}/{groceryLines.length} confirmed
                  </span>
                </div>
                {result.store_name && (
                  <p className="text-sm text-muted-foreground">{result.store_name}</p>
                )}
              </CardHeader>
              <CardContent className="space-y-2">
                {groceryLines.map((line) => (
                  <LineItem
                    key={line.id}
                    line={line}
                    onToggle={(confirmed) => updateLine(line.id, { confirmed })}
                    onEdit={() => setEditingLine(editingLine === line.id ? null : line.id)}
                    isEditing={editingLine === line.id}
                    onSave={(patch) => {
                      updateLine(line.id, patch);
                      setEditingLine(null);
                    }}
                  />
                ))}

                {groceryLines.length === 0 && (
                  <p className="text-sm text-muted-foreground text-center py-4">
                    No grocery items detected.
                  </p>
                )}
              </CardContent>
            </Card>

            <div className="flex gap-3">
              <Button
                variant="outline"
                className="flex-1"
                onClick={() => discard.mutate()}
                disabled={discard.isPending || confirm.isPending}
              >
                <X className="h-4 w-4 mr-2" />
                Discard
              </Button>
              <Button
                className="flex-1"
                onClick={() => confirm.mutate()}
                disabled={confirmedCount === 0 || confirm.isPending}
              >
                {confirm.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                ) : (
                  <Check className="h-4 w-4 mr-2" />
                )}
                Add {confirmedCount} to Pantry
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function LineItem({
  line,
  onToggle,
  onEdit,
  isEditing,
  onSave,
}: {
  line: ReceiptLine;
  onToggle: (v: boolean) => void;
  onEdit: () => void;
  isEditing: boolean;
  onSave: (patch: Partial<ReceiptLine>) => void;
}) {
  const [qty, setQty] = useState(String(line.quantity ?? "1"));
  const [unit, setUnit] = useState(line.unit ?? "count");

  const confidence = line.match_confidence;
  const confBadge =
    confidence >= 0.9
      ? { label: "High", variant: "success" as const }
      : confidence >= 0.7
      ? { label: "Medium", variant: "warning" as const }
      : { label: "Low", variant: "destructive" as const };

  return (
    <div
      className={`flex flex-col gap-2 p-3 rounded-lg border transition-colors ${
        line.confirmed ? "bg-green-50 border-green-200" : "bg-background"
      }`}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={line.confirmed}
          onChange={(e) => onToggle(e.target.checked)}
          className="h-4 w-4 mt-0.5 accent-primary flex-shrink-0"
          disabled={!line.ingredient_id}
        />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate">{line.raw_text}</p>
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            {line.quantity && (
              <span className="text-xs text-muted-foreground">
                {line.quantity} {line.unit}
              </span>
            )}
            <Badge variant={confBadge.variant} className="text-xs">
              {confBadge.label}
            </Badge>
            {!line.ingredient_id && (
              <Badge variant="outline" className="text-xs">Unmatched</Badge>
            )}
          </div>
        </div>
        <button
          className="text-muted-foreground hover:text-foreground p-1"
          onClick={onEdit}
        >
          <Edit2 className="h-3 w-3" />
        </button>
      </div>

      {isEditing && (
        <div className="flex gap-2 items-end ml-7">
          <div className="flex-1">
            <label className="text-xs text-muted-foreground">Qty</label>
            <Input
              type="number"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              className="h-8 text-sm"
            />
          </div>
          <div className="w-20">
            <label className="text-xs text-muted-foreground">Unit</label>
            <Input
              value={unit}
              onChange={(e) => setUnit(e.target.value)}
              className="h-8 text-sm"
            />
          </div>
          <Button
            size="sm"
            className="h-8"
            onClick={() => onSave({ quantity: parseFloat(qty), unit })}
          >
            Save
          </Button>
        </div>
      )}
    </div>
  );
}
