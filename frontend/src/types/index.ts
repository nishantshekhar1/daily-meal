export interface StockLot {
  lot_id: number;
  quantity: number;
  unit: string;
  source: string;
  acquired_at: string;
}

export interface PantryItem {
  ingredient_id: number;
  name: string;
  category: string;
  default_unit: string;
  total_quantity: number;
  unit: string;
  lots: StockLot[];
  toddler_flags: {
    high_sodium: boolean;
    choking_risk: boolean;
    forbidden_under_months: number | null;
  };
}

export interface HouseholdMember {
  id: number;
  name: string;
  kind: "adult_male" | "adult_female" | "child" | "toddler";
  age_months: number | null;
  dietary_notes: string | null;
  active: boolean;
}

export interface CanonicalIngredient {
  id: number;
  name: string;
  category: string;
  default_unit: string;
}

export interface ReceiptLine {
  id: number;
  raw_text: string;
  is_grocery: boolean | null;
  quantity: number | null;
  unit: string | null;
  ingredient_id: number | null;
  match_confidence: number;
  confirmed: boolean;
}

export interface ReceiptUploadResult {
  receipt_id: number;
  status: string;
  store_name: string | null;
  lines: ReceiptLine[];
}

export interface PlannedMeal {
  dish_name: string;
  dish_id: number;
  planned_meal_id: number;
  slot: string;
  audience: "main" | "toddler";
  cuisine?: string | null;
  recipe: {
    servings: number;
    prep_minutes: number;
    cook_minutes: number;
    ingredients: Array<{ name: string; quantity: number; unit: string; preparation: string }>;
    steps: string[];
  };
}

/** Whether a prewarmed plan is sitting ready for a slot. */
export interface SuggestStatus {
  slot: string;
  ready: boolean;
  count: number;
  generated_at: string | null;
}

export interface SuggestResult {
  session_id: string;
  plan_id?: number;
  /** True when served from the prewarm cache rather than generated on demand. */
  cached?: boolean;
  generated_at?: string;
  planned_meals: PlannedMeal[];
  pending_questions: Array<{ ingredient: string; question: string; answer: boolean | null }>;
  safety_reports?: Array<{ dish: string; safe: boolean; violations: Array<{ ingredient: string; rule: string; severity: string }> }>;
  message?: string;
  error?: string;
}

export type SafetyReport = NonNullable<SuggestResult["safety_reports"]>[number];

/** Events emitted by POST /meals/suggest/stream. */
export type SuggestEvent =
  | { type: "status"; step: string; message: string }
  | { type: "meal"; meal: PlannedMeal }
  | { type: "safety"; report: SafetyReport }
  | { type: "done"; result: SuggestResult }
  | { type: "error"; message: string };

export interface CookResult {
  cook_event_id: number;
  deductions: Array<{ lot_id: number; ingredient_name: string; quantity_deducted: number; unit: string; lot_exhausted: boolean }>;
  exhaustion_candidates: Array<{ lot_id: number; ingredient_name: string; quantity: number; unit: string }>;
}

/** Ratings a person can give. Cooked/skipped/rerolled are derived server-side. */
export type FeedbackSignal = "thumbs_up" | "thumbs_down";

export type FeedbackReason =
  | "too_bland"
  | "too_complex"
  | "disliked_ingredient"
  | "too_repetitive"
  | "wrong_portion";

export interface MealFeedbackResponse {
  status: string;
  planned_meal_id: number;
  signal: FeedbackSignal;
  reason: FeedbackReason | null;
}

/** Map of planned_meal_id -> existing rating, from GET /feedback/plan/{id}. */
export type PlanFeedback = Record<
  number,
  { signal: FeedbackSignal; reason: FeedbackReason | null }
>;
