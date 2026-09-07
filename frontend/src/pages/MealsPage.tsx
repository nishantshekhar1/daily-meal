import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChefHat,
  Clock,
  Users,
  AlertCircle,
  CheckCircle2,
  Loader2,
  Baby,
  Zap,
  RefreshCw,
} from "lucide-react";
import { TopBar } from "@/components/layout/TopBar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { MealFeedback } from "@/components/MealFeedback";
import { apiFetch, apiStream } from "@/lib/utils";
import type {
  SuggestResult,
  SuggestEvent,
  SuggestStatus,
  PlannedMeal,
  CookResult,
  PlanFeedback,
} from "@/types";

const SLOT_LABELS: Record<string, string> = {
  breakfast: "Breakfast",
  lunch: "Lunch",
  dinner: "Dinner",
  snack: "Snack",
};

function slotFromHour(): string {
  const h = new Date().getHours();
  if (h >= 5 && h < 11) return "breakfast";
  if (h >= 11 && h < 15) return "lunch";
  return "dinner";
}

export default function MealsPage() {
  const qc = useQueryClient();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<string>(slotFromHour);
  const [result, setResult] = useState<SuggestResult | null>(null);
  const [cookResult, setCookResult] = useState<CookResult | null>(null);
  const [cookingMeal, setCookingMeal] = useState<PlannedMeal | null>(null);
  const [expandedDish, setExpandedDish] = useState<number | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [streamedMeals, setStreamedMeals] = useState<PlannedMeal[]>([]);

  // A plan for the current slot is usually prewarmed, in which case the stream
  // replays it immediately. Otherwise it is generated live, one LLM call per
  // dish, and the stream lets each recipe render as it lands.
  const suggest = useMutation({
    mutationFn: async (opts?: { slot?: string; force?: boolean }) => {
      setProgress("Starting…");
      setStreamedMeals([]);

      let final: SuggestResult | null = null;
      for await (const event of apiStream<SuggestEvent>("/meals/suggest/stream", {
        slot: opts?.slot ?? selectedSlot,
        session_id: sessionId,
        force: opts?.force ?? false,
      })) {
        if (event.type === "status") setProgress(event.message);
        else if (event.type === "meal") setStreamedMeals((prev) => [...prev, event.meal]);
        else if (event.type === "done") final = event.result;
        else if (event.type === "error") throw new Error(event.message);
      }
      if (!final) throw new Error("Planning ended without a result");
      return final;
    },
    onSuccess: (data) => {
      setResult(data);
      if (data.session_id) setSessionId(data.session_id);
      qc.invalidateQueries({ queryKey: ["suggest-status"] });
    },
    onSettled: () => setProgress(null),
  });

  // Tells the user whether pressing Suggest is instant or a few minutes' wait.
  const status = useQuery({
    queryKey: ["suggest-status", selectedSlot],
    queryFn: () =>
      apiFetch<SuggestStatus>(`/meals/suggest/status?slot=${selectedSlot}`),
    refetchInterval: 60_000,
  });

  // Ratings already given for this plan, so reopening it shows the buttons in
  // their stored state instead of blank.
  const planFeedback = useQuery({
    queryKey: ["plan-feedback", result?.plan_id],
    queryFn: () => apiFetch<PlanFeedback>(`/feedback/plan/${result?.plan_id}`),
    enabled: result?.plan_id != null,
    staleTime: Infinity,
  });

  const answerQuestion = useMutation({
    mutationFn: ({
      ingredient_name,
      available,
    }: {
      ingredient_name: string;
      available: boolean;
    }) =>
      apiFetch("/meals/answer-question", {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId, ingredient_name, available }),
      }),
    onSuccess: () => {
      // Re-suggest after answering
      suggest.mutate(undefined);
    },
  });

  const cookMeal = useMutation({
    mutationFn: (planned_meal_id: number) =>
      apiFetch<CookResult>("/meals/cook", {
        method: "POST",
        body: JSON.stringify({ planned_meal_id }),
      }),
    onSuccess: (data) => {
      setCookResult(data);
    },
  });

  const confirmExhaustion = useMutation({
    mutationFn: ({
      cook_event_id,
      remove_lot_ids,
    }: {
      cook_event_id: number;
      remove_lot_ids: number[];
    }) =>
      apiFetch("/meals/confirm-exhaustion", {
        method: "POST",
        body: JSON.stringify({ cook_event_id, remove_lot_ids }),
      }),
    onSuccess: () => {
      setCookResult(null);
      setCookingMeal(null);
      qc.invalidateQueries({ queryKey: ["pantry"] });
    },
  });

  const timeSlot = slotFromHour();

  const runSuggest = (force = false) => {
    setResult(null);
    setSessionId(null);
    suggest.mutate({ slot: selectedSlot, force });
  };

  const ready = status.data?.ready ?? false;

  // Picking a slot only changes the selection — results for another meal no
  // longer apply, so clear them and let the user press Suggest again.
  const pickSlot = (slot: string) => {
    if (slot === selectedSlot) return;
    setSelectedSlot(slot);
    setResult(null);
    setSessionId(null);
    setStreamedMeals([]);
  };

  // While the stream is open the finished recipes are all we have; the final
  // `done` event replaces them with the authoritative list.
  const meals = result?.planned_meals ?? streamedMeals;
  const questions = result?.pending_questions?.filter((q) => q.answer === null) ?? [];
  const safetyReports = result?.safety_reports?.filter((r) => !r.safe) ?? [];

  return (
    <div className="pb-24">
      <TopBar title="Meal Suggestions" subtitle={`${SLOT_LABELS[timeSlot]} time`} />

      <div className="px-4 py-4 max-w-lg mx-auto space-y-4">
        {/* Suggest button */}
        <div className="flex gap-2">
          {(["breakfast", "lunch", "dinner"] as const).map((slot) => (
            <Button
              key={slot}
              variant={slot === selectedSlot ? "default" : "outline"}
              className="flex-1 h-11 px-2 capitalize"
              onClick={() => pickSlot(slot)}
              disabled={suggest.isPending}
            >
              {SLOT_LABELS[slot]}
            </Button>
          ))}
        </div>

        {/* Live progress */}
        {suggest.isPending && (
          <Card>
            <CardContent className="py-6 flex items-center gap-3">
              <Loader2 className="h-5 w-5 shrink-0 animate-spin text-primary" />
              <p className="text-sm text-muted-foreground">
                {progress ?? "AI is planning your meals…"}
              </p>
            </CardContent>
          </Card>
        )}

        {/* Pending questions */}
        {questions.length > 0 && (
          <Card className="border-amber-200 bg-amber-50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <AlertCircle className="h-4 w-4 text-amber-600" />
                Quick question{questions.length > 1 ? "s" : ""}
              </CardTitle>
              <CardDescription className="text-amber-700 text-xs">
                The AI needs to know about {questions.length} ingredient{questions.length > 1 ? "s" : ""} to make better suggestions.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {questions.map((q, i) => (
                <div key={i} className="bg-white rounded-lg p-3 space-y-2">
                  <p className="text-sm">{q.question}</p>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      className="flex-1"
                      onClick={() => answerQuestion.mutate({ ingredient_name: q.ingredient, available: true })}
                      disabled={answerQuestion.isPending}
                    >
                      Yes, I have it
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="flex-1"
                      onClick={() => answerQuestion.mutate({ ingredient_name: q.ingredient, available: false })}
                      disabled={answerQuestion.isPending}
                    >
                      No
                    </Button>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        {/* Safety warnings */}
        {safetyReports.map((report, i) => (
          <Card key={i} className="border-red-200 bg-red-50">
            <CardContent className="py-3">
              <p className="text-sm font-medium text-red-800 flex items-center gap-2">
                <AlertCircle className="h-4 w-4" /> Safety: {report.dish} removed
              </p>
              {report.violations.map((v, j) => (
                <p key={j} className="text-xs text-red-700 mt-1 ml-6">• {v.rule}</p>
              ))}
            </CardContent>
          </Card>
        ))}

        {/* No result yet */}
        {!suggest.isPending && !result && (
          <Card>
            <CardContent className="py-10 text-center">
              <ChefHat className="h-12 w-12 mx-auto text-muted-foreground mb-3" />
              <p className="font-medium">Ready to suggest a meal</p>
              <p className="text-sm text-muted-foreground mt-1">
                {ready
                  ? `${status.data?.count} ${SLOT_LABELS[selectedSlot].toLowerCase()} ideas are ready to go`
                  : `The AI will check your pantry and plan ${SLOT_LABELS[selectedSlot].toLowerCase()}`}
              </p>
              <Button
                size="lg"
                className="w-full mt-5"
                onClick={() => runSuggest()}
                disabled={suggest.isPending}
              >
                {ready ? <Zap className="h-5 w-5" /> : <ChefHat className="h-5 w-5" />}
                Suggest {SLOT_LABELS[selectedSlot]}
              </Button>
              {!ready && (
                <p className="text-xs text-muted-foreground mt-2">
                  Nothing prepared yet — this one takes a few minutes
                </p>
              )}
            </CardContent>
          </Card>
        )}

        {/* Error */}
        {(result?.error || suggest.isError) && (
          <Card className="border-destructive">
            <CardContent className="py-4">
              <p className="text-sm text-destructive">
                {result?.error
                  ? result.message || "No dishes could be suggested from current stock."
                  : suggest.error?.message || "Planning failed."}
              </p>
            </CardContent>
          </Card>
        )}

        {/* Meal cards */}
        {meals.map((meal, idx) => (
          <Card key={idx} className="overflow-hidden">
            <CardHeader className="pb-2">
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <CardTitle className="text-base">{meal.dish_name}</CardTitle>
                    {meal.audience === "toddler" && (
                      <Badge variant="secondary" className="text-xs">
                        <Baby className="h-3 w-3 mr-1" /> Toddler
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-3 mt-1.5 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {(meal.recipe?.prep_minutes ?? 0) + (meal.recipe?.cook_minutes ?? 0)} min
                    </span>
                    <span className="flex items-center gap-1">
                      <Users className="h-3 w-3" />
                      {meal.recipe?.servings ?? "?"} servings
                    </span>
                    <Badge variant="outline" className="text-xs capitalize">{meal.slot}</Badge>
                  </div>
                </div>
              </div>
            </CardHeader>

            <CardContent className="space-y-3">
              {/* Ingredients summary */}
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">Ingredients</p>
                <div className="flex flex-wrap gap-1">
                  {meal.recipe?.ingredients?.slice(0, expandedDish === idx ? undefined : 5).map((ing, i) => (
                    <span key={i} className="text-xs bg-secondary px-2 py-0.5 rounded-full">
                      {ing.name}
                    </span>
                  ))}
                  {(meal.recipe?.ingredients?.length ?? 0) > 5 && expandedDish !== idx && (
                    <button
                      className="text-xs text-primary"
                      onClick={() => setExpandedDish(idx)}
                    >
                      +{(meal.recipe?.ingredients?.length ?? 0) - 5} more
                    </button>
                  )}
                </div>
              </div>

              {/* Steps (expanded) */}
              {expandedDish === idx && meal.recipe?.steps && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">Steps</p>
                  <ol className="space-y-1.5 list-decimal list-inside">
                    {meal.recipe.steps.map((step, i) => (
                      <li key={i} className="text-sm">{step}</li>
                    ))}
                  </ol>
                </div>
              )}

              <div className="flex gap-2 pt-1">
                <Button
                  size="sm"
                  variant="outline"
                  className="flex-1"
                  onClick={() => setExpandedDish(expandedDish === idx ? null : idx)}
                >
                  {expandedDish === idx ? "Hide recipe" : "View recipe"}
                </Button>
                <Button
                  size="sm"
                  className="flex-1"
                  onClick={() => {
                    setCookingMeal(meal);
                    cookMeal.mutate(meal.planned_meal_id);
                  }}
                  disabled={cookMeal.isPending}
                >
                  <CheckCircle2 className="h-4 w-4 mr-1" />
                  Cooked!
                </Button>
              </div>

              <div className="border-t pt-2">
                {/* Keyed on the stored rating so the control picks up its
                    initial state once the plan's existing feedback loads. */}
                <MealFeedback
                  key={`${meal.planned_meal_id}-${
                    planFeedback.data?.[meal.planned_meal_id]?.signal ?? "none"
                  }`}
                  plannedMealId={meal.planned_meal_id}
                  initial={planFeedback.data?.[meal.planned_meal_id]?.signal ?? null}
                />
              </div>
            </CardContent>
          </Card>
        ))}

        {/* Regenerate: bypasses the prewarmed plan, so this one waits on the model */}
        {meals.length > 0 && !suggest.isPending && (
          <Button
            variant="outline"
            className="w-full h-11"
            onClick={() => runSuggest(true)}
          >
            <RefreshCw className="h-4 w-4" />
            Suggest something else
          </Button>
        )}
      </div>

      {/* Exhaustion confirmation dialog */}
      <Dialog open={!!cookResult} onOpenChange={() => cookResult && setCookResult(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Update Pantry</DialogTitle>
            <DialogDescription>
              Select which ingredients are now fully used up and should be removed from stock.
            </DialogDescription>
          </DialogHeader>
          {cookResult && (
            <ExhaustionConfirm
              cookResult={cookResult}
              onConfirm={(removeIds) =>
                confirmExhaustion.mutate({
                  cook_event_id: cookResult.cook_event_id,
                  remove_lot_ids: removeIds,
                })
              }
              loading={confirmExhaustion.isPending}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ExhaustionConfirm({
  cookResult,
  onConfirm,
  loading,
}: {
  cookResult: CookResult;
  onConfirm: (ids: number[]) => void;
  loading: boolean;
}) {
  const [selected, setSelected] = useState<Set<number>>(
    new Set(cookResult.exhaustion_candidates.map((c) => c.lot_id))
  );

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <div className="space-y-3">
      {cookResult.exhaustion_candidates.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          All ingredients have been deducted. No items are fully exhausted.
        </p>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">These were fully used up:</p>
          <div className="space-y-2">
            {cookResult.exhaustion_candidates.map((c) => (
              <label
                key={c.lot_id}
                className="flex items-center gap-3 p-3 border rounded-lg cursor-pointer hover:bg-accent"
              >
                <input
                  type="checkbox"
                  checked={selected.has(c.lot_id)}
                  onChange={() => toggle(c.lot_id)}
                  className="h-4 w-4 accent-primary"
                />
                <span className="text-sm font-medium">{c.ingredient_name}</span>
              </label>
            ))}
          </div>
        </>
      )}
      <Button
        className="w-full"
        onClick={() => onConfirm([...selected])}
        disabled={loading}
      >
        {loading ? "Updating…" : "Confirm"}
      </Button>
    </div>
  );
}
