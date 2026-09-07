import { useState, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Camera, Search } from "lucide-react";
import { TopBar } from "@/components/layout/TopBar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { apiFetch, API_BASE } from "@/lib/utils";
import type { PantryItem, CanonicalIngredient } from "@/types";

export default function PantryPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [addIngId, setAddIngId] = useState<number | null>(null);
  const [addQty, setAddQty] = useState("1");
  const [addUnit, setAddUnit] = useState("count");
  const [ingSearch, setIngSearch] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const { data: pantry = [], isLoading } = useQuery<PantryItem[]>({
    queryKey: ["pantry"],
    queryFn: () => apiFetch("/pantry/"),
  });

  const { data: ingredients = [] } = useQuery<CanonicalIngredient[]>({
    queryKey: ["ingredients", ingSearch],
    queryFn: () =>
      ingSearch.length > 1
        ? apiFetch(`/pantry/ingredients/search?q=${encodeURIComponent(ingSearch)}`)
        : apiFetch("/pantry/ingredients"),
    enabled: addOpen,
  });

  const removeLot = useMutation({
    mutationFn: (lotId: number) =>
      apiFetch(`/pantry/stock/${lotId}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pantry"] }),
  });

  const addStock = useMutation({
    mutationFn: () =>
      apiFetch("/pantry/stock", {
        method: "POST",
        body: JSON.stringify({
          ingredient_id: addIngId,
          quantity: parseFloat(addQty),
          unit: addUnit,
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pantry"] });
      setAddOpen(false);
      setAddIngId(null);
      setAddQty("1");
    },
  });

  const uploadPhoto = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/pantry/photo`, { method: "POST", body: fd });
      return res.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pantry"] }),
  });

  const filtered = pantry.filter((p) =>
    p.name.toLowerCase().includes(search.toLowerCase())
  );

  const categoryColors: Record<string, string> = {
    dairy: "bg-blue-50 text-blue-700",
    meat: "bg-red-50 text-red-700",
    produce: "bg-green-50 text-green-700",
    grain: "bg-yellow-50 text-yellow-700",
    seafood: "bg-cyan-50 text-cyan-700",
    legume: "bg-orange-50 text-orange-700",
    default: "bg-gray-50 text-gray-700",
  };

  return (
    <div className="pb-24">
      <TopBar
        title="Pantry"
        subtitle={`${pantry.length} ingredient${pantry.length !== 1 ? "s" : ""} in stock`}
        right={
          <div className="flex gap-2">
            <Button
              size="icon"
              variant="outline"
              onClick={() => fileRef.current?.click()}
              title="Add from photo"
            >
              <Camera className="h-4 w-4" />
            </Button>
            <Button size="icon" onClick={() => setAddOpen(true)}>
              <Plus className="h-4 w-4" />
            </Button>
          </div>
        }
      />

      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) uploadPhoto.mutate(file);
        }}
      />

      <div className="px-4 py-3 max-w-lg mx-auto">
        <div className="relative mb-4">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search pantry..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>

        {isLoading && (
          <div className="text-center py-12 text-muted-foreground">Loading pantry…</div>
        )}

        {!isLoading && filtered.length === 0 && (
          <div className="text-center py-12 text-muted-foreground">
            <p className="font-medium">Pantry is empty</p>
            <p className="text-sm mt-1">Add ingredients manually or scan a receipt</p>
          </div>
        )}

        <div className="space-y-2">
          {filtered.map((item) => {
            const catClass = categoryColors[item.category] ?? categoryColors.default;
            return (
              <Card key={item.ingredient_id} className="overflow-hidden">
                <CardContent className="p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-sm truncate">{item.name}</span>
                        <span className={`text-xs px-2 py-0.5 rounded-full ${catClass}`}>
                          {item.category}
                        </span>
                        {item.toddler_flags.high_sodium && (
                          <Badge variant="warning" className="text-xs">High sodium</Badge>
                        )}
                        {item.toddler_flags.choking_risk && (
                          <Badge variant="warning" className="text-xs">Choking risk</Badge>
                        )}
                        {item.toddler_flags.forbidden_under_months && (
                          <Badge variant="destructive" className="text-xs">
                            Under {item.toddler_flags.forbidden_under_months}m forbidden
                          </Badge>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground mt-1">
                        {item.total_quantity.toFixed(1)} {item.unit}
                        {item.lots.length > 1 && (
                          <span className="ml-2 text-xs">({item.lots.length} lots)</span>
                        )}
                      </p>
                    </div>
                    <div className="flex gap-1">
                      {item.lots.map((lot) => (
                        <Button
                          key={lot.lot_id}
                          size="icon"
                          variant="ghost"
                          className="h-8 w-8 text-destructive"
                          onClick={() => removeLot.mutate(lot.lot_id)}
                          title={`Remove lot (${lot.quantity} ${lot.unit})`}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      ))}
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>

      {/* Add ingredient dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Add to Pantry</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-sm font-medium">Ingredient</label>
              <Input
                placeholder="Search ingredient..."
                value={ingSearch}
                onChange={(e) => setIngSearch(e.target.value)}
                className="mt-1"
              />
              {ingSearch.length > 0 && ingredients.length > 0 && (
                <div className="mt-1 border rounded-md max-h-40 overflow-y-auto">
                  {ingredients.map((ing) => (
                    <button
                      key={ing.id}
                      className={`w-full text-left px-3 py-2 text-sm hover:bg-accent transition-colors ${addIngId === ing.id ? "bg-primary/10 font-medium" : ""}`}
                      onClick={() => {
                        setAddIngId(ing.id);
                        setAddUnit(ing.default_unit);
                        setIngSearch(ing.name);
                      }}
                    >
                      {ing.name} <span className="text-muted-foreground text-xs">({ing.category})</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="flex gap-2">
              <div className="flex-1">
                <label className="text-sm font-medium">Quantity</label>
                <Input
                  type="number"
                  min="0"
                  step="0.1"
                  value={addQty}
                  onChange={(e) => setAddQty(e.target.value)}
                  className="mt-1"
                />
              </div>
              <div className="w-24">
                <label className="text-sm font-medium">Unit</label>
                <Input
                  value={addUnit}
                  onChange={(e) => setAddUnit(e.target.value)}
                  className="mt-1"
                  placeholder="g, mL…"
                />
              </div>
            </div>
            <Button
              className="w-full"
              disabled={!addIngId || !addQty || addStock.isPending}
              onClick={() => addStock.mutate()}
            >
              {addStock.isPending ? "Adding…" : "Add to Pantry"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
