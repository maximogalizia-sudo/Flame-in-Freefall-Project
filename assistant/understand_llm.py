"""LLM help to UNDERSTAND indirect questions (criterion 3: rules first, the LLM only as a validated fallback).

Used only when the rules found no fuel/material and no variable (e.g. "¿una vela aguanta más en una cabina con aire
enriquecido?"). The LLM returns JSON that is validated strictly:
  * substances only from our closed list (SUBSTANCES keys);
  * numbers only if the same number is written in the question (no invented values);
  * qualitative levels ("enriched air" -> oxygen 'high') become the VARIABLE to explain, never a number.
Anything invalid is dropped; if nothing valid remains, the rules' reading is kept.
"""
from __future__ import annotations

import json
import re

from flame.contracts import Conditions

from .understand import SUBSTANCES, Understanding, normalize

SYSTEM = ('You read a question about fire / combustion in space and return ONLY a JSON object, no prose. Keys: '
          '"substance" (one of: ' + ', '.join(sorted({k for k in SUBSTANCES if re.fullmatch(r'[a-z]+', k)})) + ' or null), '
          '"o2_percent", "co2_percent", "he_percent" (numbers ONLY if written in the question, else null), '
          '"pressure_value" and "pressure_unit" (atm|kpa|bar|psi, only if written), '
          '"variable" (one of: o2_frac, co2_frac, he_frac, pressure_kpa, droplet_d0_mm or null: the condition the '
          'question is about, e.g. "enriched air" -> o2_frac, "more CO2" -> co2_frac), '
          '"level" ("high", "low" or null: qualitative level of that variable), '
          '"intent" (one of: predict, explain_variable, risk, explain_doc, off_topic). '
          'Map everyday words: candle/vela -> candle, cotton/algodon -> cotton, acrylic/acrilico -> acrylic, '
          'enriched air / mas oxigeno / aire enriquecido -> variable o2_frac level high.')
INTENTS = {'predict', 'explain_variable', 'risk', 'explain_doc', 'off_topic'}
VARIABLES = {'o2_frac', 'co2_frac', 'he_frac', 'pressure_kpa', 'droplet_d0_mm'}
UNITS = {'atm': 101.325, 'kpa': 1.0, 'bar': 100.0, 'psi': 6.89476}


def needs_help(u: Understanding) -> bool:
    return not u.conditions.family and not u.variable and u.conditions.gravity_g is None and not u.unknown_terms


def _in_question(x, text: str) -> bool:
    if x is None:
        return False
    s = f'{float(x):g}'
    return bool(re.search(rf'(?<![\d.,]){re.escape(s)}(?![\d])', text.replace(',', '.')))


def llm_assist(u: Understanding, llm) -> Understanding:
    raw = llm.chat(SYSTEM, u.text, max_tokens=150, temperature=0.0)
    if not raw:
        return u
    m = re.search(r'\{.*\}', raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        return u
    vals: dict = {}
    sub = (d.get('substance') or '').lower().strip()
    if sub in SUBSTANCES:
        fam, fld, code = SUBSTANCES[sub]
        vals.update(family=fam, **{fld: code})
        u.substances = [SUBSTANCES[sub]]
    for key, fld in (('o2_percent', 'o2_frac'), ('co2_percent', 'co2_frac'), ('he_percent', 'he_frac')):
        if _in_question(d.get(key), u.text):
            vals[fld] = float(d[key]) / 100
    unit = (d.get('pressure_unit') or '').lower()
    if unit in UNITS and _in_question(d.get('pressure_value'), u.text):
        vals['pressure_kpa'] = round(float(d['pressure_value']) * UNITS[unit], 3)
    var = d.get('variable') if d.get('variable') in VARIABLES else None
    intent = d.get('intent') if d.get('intent') in INTENTS else None
    if not vals and not var:
        return u
    u.conditions = Conditions(**{**u.conditions.model_dump(exclude_none=True), **vals})
    if var:
        u.variable = var
        if d.get('level') in ('high', 'low'):
            u.notes.append(f'{var} level understood as {d["level"]} (no value given: no number is invented)')
    if intent and intent != 'off_topic':
        u.intent = intent
    elif var and u.intent == 'off_topic':
        u.intent = 'explain_variable'
    if u.intent == 'explain_variable' and not u.variable:
        u.intent = 'explain_doc'
    u.notes.append('question understood with LLM help (validated: closed lists, only numbers written in the question)')
    return u
