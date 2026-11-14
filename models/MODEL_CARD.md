# Model card — Flame in Freefall (LC models v1, 2026-11-14)

Trained with `python -m flame.train` from `data/processed/*.parquet`. Used through `flame.models.predict / coverage / explain` (contract: `flame/contracts.py`). Models are stored as JSON (coefficients, scaling, bootstrap draws): the API needs only numpy, pandas and pydantic.

All metrics are **grouped cross-validation** (tests that share conditions or a chamber atmosphere are never split between training and testing; DD-023, DD-030). Model choice (feature set, regularization) was made on the same CV, so the numbers are slightly optimistic.

| Model | Question | Data | Out-of-sample quality |
|---|---|---|---|
| `flex_main` | If the droplet does not break up, does the flame go out before the droplet burns completely? | 200 clean FLEX tests, 0.7–1 atm (172 out / 28 burned completely), 98 condition groups | AUC 0.91, log loss 0.23 (base rate 0.41) |
| `flex_breakup` | Is the test interrupted by droplet break-up (Disruption)? | 254 FLEX tests, 0.7–1 atm (54 break-ups) | AUC 0.88, log loss 0.35 (base rate 0.52) |
| `cfig_selfext_normal` | Does a lit gas flame (fuel into oxygen) go out by itself within T seconds? | 133 flames, 102 went out, 22 chamber atmospheres; operator stops = censored | C-index 0.82 |
| `cfig_selfext_inverse` | Same, oxygen into a fuel atmosphere | 138 flames, 80 went out, 68 atmospheres | C-index 0.71 |
| ~~`cfig_ignition`~~ | Does a normal gas flame light? | 170 tests, 37 failed to light | **Not used** (LC, 2026-11-14): too flat at the edges (says 37 % at 2 % fuel, NASA saw 0 of 14). Answered with NASA's observed shares instead (`ignition_evidence`) |

## What the models say (plain words)
- **FLEX:** more oxygen → less likely to go out; more CO₂ or helium → more likely. **Droplet size acts opposite by fuel:** small heptane droplets burn completely, large ones go out (radiative heat loss); methanol goes out at almost any size. Free droplets go out more often than droplets held on a fiber.
- **FLEX break-up:** more likely at high oxygen and for heptane — consistent with NASA's contaminated-needle explanation; shown as "possible experiment problem".
- **CFI-G normal flames:** each extra point of oxygen makes the flame last longer; higher pressure and bigger flames make it go out sooner. At ~21 % O₂ every lit flame went out by itself.
- **CFI-G inverse flames:** more fuel in the surrounding atmosphere and bigger flames keep it burning longer; jet oxygen barely matters.
- **Ignition:** almost only "is there enough fuel in the jet" — shown as NASA evidence: ≤ 3 % fuel 0 of 14 lit; 3–6 % 14 of 26; 6–20 % 10 of 10; > 20 % 109 of 120.

## Honesty rules in `predict`
- A number is given only when NASA ran clean tests with the **same combination** (fuel, suppressor, pressure level) near the asked oxygen / suppressor dose / droplet size; otherwise `probability=None`, the reasons, and the closest real tests.
- FLEX always reports the break-up risk and a **sensitivity range** (every Disruption counted as "went out" vs "burned completely"); a note is added when the range crosses 50 %.
- Partial gravity, CO₂+He together, 2–3 atm (FLEX), and solids are never predicted. Solids (BASS/BASS-II) are **evidence only** (DD-033).
- Inputs not given are filled with typical tested values and listed in `assumptions`.

## Review by LC (notebook `04_modelos.ipynb`, 2026-11-14)
- Approved as they are: FLEX main, FLEX break-up, CFI-G normal.
- CFI-G inverse: kept with a "moderate confidence" note in every answer (C 0.71, rough calibration at high probabilities).
- Ignition: model replaced by NASA's observed shares.
- FLEX + methanol + CO₂: answers carry a note that the CO₂ effect cannot be separated from O₂ in these data.

## Known limitations
- Few "burned completely" cases (28) in FLEX; intervals are wide near the edges.
- CFI-G: 12 flames were stopped because the burner overheated (strong flames): that censoring is not random.
- No partial-gravity data at all; no solid-material model; no data for solids above 22.7 % O₂.
