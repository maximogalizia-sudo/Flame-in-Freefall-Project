"""LLM help to CLASSIFY the question (what kind of question it is -> which tool answers it).

Why (2026-11-14, NOTAS / docs/DECISIONES_TECNICAS.md DT-011): the keyword rules recognised the kind of question in only
6 of 12 never-seen analytic questions. They fail when no keyword matches ("the material that least sustained the
flame") or when a keyword means something else ("hace falta" = "is needed", read as "data gaps"). The LLM reads the
meaning; the rules stay as the first reading and as the fallback.

Safety (same criterion as understand_llm): the LLM only picks from closed lists (intent, variable, ranking target,
family). It never writes a number, a condition or a substance here. Anything outside the lists is dropped.
Which reading wins is decided by `choose` (policy measured on the labeled questions: assistant/bench/intent_eval.py).
"""
from __future__ import annotations

import json
import re

INTENT_HELP = {
    'predict': 'the outcome for ONE specific case: a given fuel or material under given conditions (does it go out, '
               'keep burning, how long does it last)',
    'scenario': 'what happens to a specific fire when ONE condition changes (from a value to another, more / less of it)',
    'explain_variable': 'how a condition (oxygen, CO2, helium, pressure, droplet size) affects flames in general: its '
                        'effect, role, danger or influence',
    'profile': 'a range, threshold or limit of a condition over ALL the data: at / above / below which level flames '
               'always or never go out, lowest or highest level where they still burned, how much of it is needed',
    'rank': 'a ranking or list of the configurations, conditions, materials or tests that most / least burned, lasted '
            'longest, went out fastest or sustained the flame least',
    'gaps': 'what is missing or weak in the data: untested conditions, limitations, which new experiments or data are needed',
    'measure': 'the typical MEASURED value of a quantity in the tests (not a prediction for one case): droplet diameter '
               'at extinction, burning rate, burn time, how often cool flames or soot appeared and under which conditions',
    'count': 'how many tests or experiments, how often something happened in the data',
    'coverage': 'whether NASA has data at all about something (e.g. lunar gravity, humidity, a material)',
    'compare': 'a direct comparison between two named fuels, materials or conditions',
    'risk': 'whether a whole cabin or habitat atmosphere (oxygen and pressure) is safe or risky, early warning',
    'explain_doc': 'an explanation of a phenomenon, concept, experiment or reason, answered from the NASA reports '
                   '(why, what is, how does it work)',
    'off_topic': 'not about fire, combustion or these NASA experiments',
}
VARIABLES = {'o2_frac', 'co2_frac', 'he_frac', 'pressure_kpa', 'droplet_d0_mm'}
TARGETS = {'persistence': 'most likely to keep burning', 'extinction': 'most likely to go out / least flammable',
           'burn_time': 'burned or lasted the longest', 'fastest_out': 'went out the fastest'}
QUANTITIES = {'dext': 'droplet diameter at extinction', 'burn_rate': 'burning rate', 'burn_time': 'burn time',
              'cool_flame': 'cool flames', 'soot': 'soot'}
FAMILIES = {'flex': 'fuel droplets (methanol, heptane)', 'cfig': 'gas flames (ethane, propane, butane)',
            'solids': 'solid materials (fabrics, Nomex, acrylic)'}

SYSTEM = ('You classify one question about the NASA microgravity combustion data (fuel droplets, gas flames, solid '
          'materials). Return ONLY a JSON object, no prose, with keys:\n'
          '"intent": one of\n' + '\n'.join(f'  - {k}: {v}' for k, v in INTENT_HELP.items()) + '\n'
          '"variable": the condition the question is about, one of ' + ', '.join(sorted(VARIABLES)) + ' or null\n'
          '"target": only for rank, one of ' + '; '.join(f'{k} ({v})' for k, v in TARGETS.items()) + ', else null\n'
          '"quantity": only for measure, one of ' + '; '.join(f'{k} ({v})' for k, v in QUANTITIES.items()) + ', else null\n'
          '"family": the kind of fire if the question is limited to one, one of '
          + '; '.join(f'{k} ({v})' for k, v in FAMILIES.items()) + ', else null\n'
          'Classify by what the person wants to know, not by single words.')


def classify_llm(text: str, llm) -> dict | None:
    """{'intent', 'variable', 'target', 'family'} validated against the closed lists, or None if unusable."""
    raw = llm.chat(SYSTEM, text, max_tokens=120, temperature=0.0)
    if not raw:
        return None
    m = re.search(r'\{.*\}', raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        return None
    intent = d.get('intent') if d.get('intent') in INTENT_HELP else None
    if intent is None:
        return None
    return {'intent': intent,
            'variable': d.get('variable') if d.get('variable') in VARIABLES else None,
            'target': d.get('target') if d.get('target') in TARGETS else None,
            'quantity': d.get('quantity') if d.get('quantity') in QUANTITIES else None,
            'family': d.get('family') if d.get('family') in FAMILIES else None}


def refine(u, llm, policy: str = 'guarded'):
    """Apply the policy to an Understanding: ask the LLM, pick the intent, fill what that intent needs from the LLM's
    validated reading (or from the rules), and fall back where the intent cannot run (no variable)."""
    from .understand import find_family, find_rank_target, matched_intents, normalize
    if llm is None or policy == 'rules':
        return u
    t = normalize(u.text)
    r = classify_llm(u.text, llm)
    new = choose(u.intent, matched_intents(t), r, policy, u)
    if r is None or new == u.intent:
        return u
    old = u.intent
    u.intent = new
    u.variable = u.variable or r['variable']
    if new in ('explain_variable', 'scenario') and not u.variable:
        u.intent = 'explain_doc' if new == 'explain_variable' else 'predict'
    if new == 'profile' and not u.variable:
        u.variable = 'pressure_kpa' if re.search(r'presion|pressure', t) else 'o2_frac'
    if new == 'rank':
        u.rank_target = r['target'] or find_rank_target(t)
    if new == 'measure':
        from .understand import find_quantity
        u.quantity = r.get('quantity') or find_quantity(t)
        if not u.quantity:
            u.intent = old
    if u.intent in ('profile', 'rank', 'gaps', 'measure'):
        u.analytic_family = r['family'] or u.conditions.family or find_family(t)
    u.notes.append(f'kind of question read by the LLM: {u.intent} (rules said {old}; closed list, policy "{policy}")')
    return u


def can_run(intent: str, u, llm_read: dict) -> bool:
    """The tool behind an intent needs some inputs; switching to an intent whose tool cannot run makes things worse."""
    if intent == 'risk':
        return u.conditions.o2_frac is not None                   # a cabin atmosphere needs at least its oxygen
    if intent == 'compare':                                   # two named fuels or materials, or two variables (DT-027)
        return len(u.substances) >= 2 or len(getattr(u, 'variables', [])) >= 2
    if intent in ('scenario', 'explain_variable'):
        return bool(u.variable or llm_read.get('variable'))
    if intent == 'predict':
        return bool(u.conditions.family)
    if intent == 'measure':
        from .understand import find_quantity, normalize
        return bool(llm_read.get('quantity') or find_quantity(normalize(u.text)))
    return True


def choose(rules_intent: str, matched: list[str], llm_read: dict | None, policy: str = 'guarded', u=None) -> str:
    """Which reading wins.
    'rules'   : always the rules (behaviour before 2026-11-15).
    'llm'     : always the LLM when it gave a valid answer.
    'unsure'  : the LLM only when the rules were unsure: no keyword pattern matched (the intent came from a default),
                or more than one pattern matched (ambiguous wording).
    'guarded' : the LLM, with two protections (criteria, not tuned to questions):
                (1) it may not turn an on-topic question into off_topic (refusing a valid question costs more than trying);
                (2) it may not switch to an intent whose tool cannot run with what was understood (see can_run)."""
    if llm_read is None or policy == 'rules':
        return rules_intent
    new = llm_read['intent']
    if policy == 'llm':
        return new
    if policy == 'unsure':
        return new if len(matched) != 1 else rules_intent
    if new == 'off_topic' and rules_intent != 'off_topic':
        return rules_intent
    if u is not None and not can_run(new, u, llm_read):
        return rules_intent
    return new
