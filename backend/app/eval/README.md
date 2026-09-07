# Offline evaluation

Answers "did suggestion quality get worse" with a number instead of an opinion.

This is separate from `backend/tests/`. Those assert that the code behaves; these assert that the *model* behaves. They need a running Ollama and take minutes, so they are a CLI you point at a box rather than something CI runs on every commit.

## Why it exists

The VRAM profiles in [`config/profiles/`](../../../config/profiles/) ship four model lineups and there was previously no way to tell whether the 12 GB one produces meaningfully worse plans than the 24 GB one. Every check here is structural and decidable without human judgement, so the comparison produces evidence rather than a guess.

## Running it

```bash
cd backend

# Evaluate whatever CONFIG_PATH currently points at
python -m app.eval.runner

# One scenario only, while iterating
python -m app.eval.runner --scenario toddler_household

# Compare profiles against each other
python -m app.eval.runner --profiles \
    ../config/models.yaml \
    ../config/profiles/16gb.yaml \
    ../config/profiles/12gb.yaml \
    ../config/profiles/8gb.yaml \
    --out eval-report.json
```

Exit code is 0 only when every scenario passes every check, so it can gate a change.

Each profile runs in its own subprocess. `get_settings` and the LLM client are both cached, so swapping profiles in-process would silently evaluate the wrong model — precisely the failure this harness exists to catch.

## Scenarios

Each gets a throwaway in-memory database seeded with the production ingredient vocabulary, so nothing touches your real pantry. They are chosen to cover the ways planning goes wrong, not the happy path alone.

| Scenario | What it probes |
|---|---|
| `stocked_omnivore` | The ordinary case: two adults, full pantry |
| `toddler_household` | A 10-month-old with honey, whole almonds and grapes in stock, so the age gates must actually bite |
| `sparse_pantry` | Barely enough for one meal — does the model invent ingredients to pad the plan out |
| `breakfast_only` | Slot-appropriate suggestions from breakfast staples |
| `empty_pantry` | Must degrade to a clarification or an empty plan, never a crash |

## Checks

| Check | Fails when |
|---|---|
| `completed` | The planner returned an error. This is the one small profiles fail: a 4B model breaks the nested shortlist schema and the graph has no repair path |
| `min_dishes` | Fewer dishes than the scenario requires |
| `recipes_complete` | A dish has no steps, no ingredients, or no servings |
| `ingredients_known` | An ingredient does not exist in the pantry vocabulary at all — invented food |
| `ingredients_in_stock` | The plan is not actually cookable from stock, re-checked from scratch |
| `toddler_safety` | A toddler dish violates an age-gated rule |
| `toddler_dish_present` | A household with a toddler got no toddler dish |
| `distinct_dishes` | The same dish appears twice in one plan |
| `cuisine_tagged` | A dish has no cuisine tag, so ranking cannot order it |
| `slot_appropriate` | A breakfast turned up at dinner |
| `cuisine_adherence` | Never fails — scores how much of the plan landed in a plausible cuisine |

`ingredients_in_stock` and `toddler_safety` deliberately re-verify invariants the planner already enforces. If either fails, a dish reached the user without passing `InventoryAllocator` or `validate_toddler_dish`, which is an application bug rather than a weak model.

## Adding a scenario

Add it to `SCENARIOS` in [`scenarios.py`](scenarios.py). Ingredient names must exist in the canonical seed set (`app.db.seed`), otherwise the fixture build fails loudly rather than evaluating something meaningless. `test_scenarios_only_reference_seeded_ingredients` enforces this.
