import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ThumbsUp, ThumbsDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiFetch, cn } from "@/lib/utils";
import type { FeedbackReason, FeedbackSignal, MealFeedbackResponse } from "@/types";

const REASONS: Array<{ value: FeedbackReason; label: string }> = [
  { value: "too_bland", label: "Too bland" },
  { value: "too_complex", label: "Too fiddly" },
  { value: "disliked_ingredient", label: "Ingredient" },
  { value: "too_repetitive", label: "Had it recently" },
  { value: "wrong_portion", label: "Portion off" },
];

interface Props {
  plannedMealId: number;
  /** Rating already stored for this meal, when the plan is reopened. */
  initial?: FeedbackSignal | null;
}

/**
 * Thumbs up/down on a suggested meal.
 *
 * The rating posts immediately and optimistically: the household is rating a
 * dinner idea, not filling in a form, so a round-trip before the button
 * responds would make it feel broken. A failed post reverts the button.
 *
 * Picking a reason after a thumbs-down is optional. It is a second, separate
 * post that refines the rating already recorded, so abandoning it loses
 * nothing.
 */
export function MealFeedback({ plannedMealId, initial = null }: Props) {
  const [signal, setSignal] = useState<FeedbackSignal | null>(initial);
  const [reason, setReason] = useState<FeedbackReason | null>(null);
  const [showReasons, setShowReasons] = useState(false);

  const rate = useMutation({
    mutationFn: (body: { signal: FeedbackSignal; reason?: FeedbackReason }) =>
      apiFetch<MealFeedbackResponse>("/feedback/meal", {
        method: "POST",
        body: JSON.stringify({ planned_meal_id: plannedMealId, ...body }),
      }),
  });

  function submit(next: FeedbackSignal) {
    const previous = signal;
    // Tapping the active button clears the rating locally; there is no delete
    // endpoint, so re-sending the opposite is the only way to correct it.
    setSignal(next);
    setShowReasons(next === "thumbs_down");
    if (next === "thumbs_up") setReason(null);

    rate.mutate(
      { signal: next },
      {
        onError: () => {
          setSignal(previous);
          setShowReasons(false);
        },
      }
    );
  }

  function submitReason(value: FeedbackReason) {
    const next = reason === value ? null : value;
    setReason(next);
    rate.mutate({
      signal: "thumbs_down",
      ...(next ? { reason: next } : {}),
    });
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1">
        <span className="text-xs text-muted-foreground mr-1">
          {signal ? "Thanks — noted" : "Good suggestion?"}
        </span>
        <Button
          size="icon"
          variant="ghost"
          aria-label="Good suggestion"
          aria-pressed={signal === "thumbs_up"}
          className={cn(
            "h-7 w-7",
            signal === "thumbs_up" && "bg-secondary text-foreground"
          )}
          onClick={() => submit("thumbs_up")}
        >
          <ThumbsUp className="h-3.5 w-3.5" />
        </Button>
        <Button
          size="icon"
          variant="ghost"
          aria-label="Poor suggestion"
          aria-pressed={signal === "thumbs_down"}
          className={cn(
            "h-7 w-7",
            signal === "thumbs_down" && "bg-secondary text-foreground"
          )}
          onClick={() => submit("thumbs_down")}
        >
          <ThumbsDown className="h-3.5 w-3.5" />
        </Button>
      </div>

      {showReasons && (
        <div className="flex flex-wrap gap-1">
          {REASONS.map((r) => (
            <button
              key={r.value}
              onClick={() => submitReason(r.value)}
              className={cn(
                "text-xs px-2 py-0.5 rounded-full border transition-colors",
                reason === r.value
                  ? "bg-secondary border-transparent"
                  : "border-border text-muted-foreground hover:bg-secondary/50"
              )}
            >
              {r.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
