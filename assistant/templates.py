"""Deterministic answers in Spanish or English (criterion 4: everything works without an LLM).

Every sentence is built from a tool output: no number is written here that does not come from a tool.
The same texts are the fallback when the LLM is not available or its draft fails verification.
"""
from __future__ import annotations

from flame.contracts import Prediction

T = {
    'understood': {'es': 'Entendí: {x}.', 'en': 'Understood: {x}.'},
    'nothing_understood': {'es': 'no reconocí condiciones concretas', 'en': 'no specific conditions recognised'},
    'unmeasured': {'es': 'NASA no midió: {x}.', 'en': 'NASA did not measure: {x}.'},
    'off_topic': {'es': 'Solo puedo responder sobre los experimentos de combustión en microgravedad de NASA (gotas FLEX, llamas de gas CFI-G, materiales BASS/BASS-II). Probá, por ejemplo: "¿se apaga una gota de heptano con 18 % de oxígeno?"',
                  'en': 'I can only answer about NASA microgravity combustion experiments (FLEX droplets, CFI-G gas flames, BASS/BASS-II materials). Try, for example: "does a heptane droplet go out at 18 % oxygen?"'},
    'no_subject': {'es': 'No identifiqué el combustible o material (por ejemplo metanol, heptano, propano, algodón, Nomex).',
                   'en': 'I could not identify the fuel or material (e.g. methanol, heptane, propane, cotton, Nomex).'},
    'pred': {'es': '{t}: {p} % (intervalo 95 %: {lo}–{hi} %).', 'en': '{t}: {p} % (95 % interval {lo}–{hi} %).'},
    'sens': {'es': 'Si las pruebas interrumpidas se cuentan de otra forma: {lo}–{hi} %.', 'en': 'Counting interrupted tests differently: {lo}–{hi} %.'},
    'no_pred': {'es': 'No hay estimación para "{t}": {why}.', 'en': 'No estimate for "{t}": {why}.'},
    'assumed': {'es': 'Supuse: {x}.', 'en': 'Assumed: {x}.'},
    'nearest': {'es': 'Pruebas reales de NASA más parecidas: {x}.', 'en': 'Closest real NASA tests: {x}.'},
    'count': {'es': '{label}: NASA tiene {n} pruebas{flt}. Resultados: {x}.', 'en': '{label}: NASA has {n} tests{flt}. Outcomes: {x}.'},
    'count_zero': {'es': '{label}: NASA no tiene pruebas{flt}.', 'en': '{label}: NASA has no tests{flt}.'},
    'o2_window': {'es': ' con O₂ entre {a} y {b} %', 'en': ' with O₂ between {a} and {b} %'},
    'docs': {'es': 'Lo que dicen los informes de NASA (fragmentos más relevantes):', 'en': 'What NASA reports say (most relevant passages):'},
    'docs_unavailable': {'es': 'La búsqueda en los informes de NASA todavía no está disponible.', 'en': 'Search in the NASA reports is not available yet.'},
    'docs_none': {'es': 'No encontré fragmentos de informes de NASA sobre esto.', 'en': 'I found no NASA report passages about this.'},
    'tool_failed': {'es': 'No pude consultar {x}.', 'en': 'I could not query {x}.'},
    'template_note': {'es': 'Respuesta automática armada con plantillas (sin redacción del asistente).',
                      'en': 'Automatic template answer (no assistant drafting).'},
}
TARGETS = {
    'extinction_before_burnout': {'es': 'Probabilidad de que la llama se apague antes de que la gota se queme entera (si la gota no se rompe)',
                                  'en': 'Probability that the flame goes out before the droplet burns completely (if it does not break up)'},
    'droplet_breakup': {'es': 'Riesgo de que la gota se rompa (posible problema del experimento)', 'en': 'Risk that the droplet breaks up (possible experiment problem)'},
    'ignition': {'es': 'Ignición', 'en': 'Ignition'},
    'no_model': {'es': 'Materiales sólidos', 'en': 'Solid materials'},
}


def t(key: str, lang: str, **kw) -> str:
    return T[key][lang].format(**kw)


def pct(x: float | None) -> str:
    return '–' if x is None else f'{x * 100:.0f}'


def target_text(target: str, lang: str) -> str:
    if target.startswith('self_extinction_within_'):
        s = target.removeprefix('self_extinction_within_')
        return {'es': f'Probabilidad de que la llama se apague sola en menos de {s}',
                'en': f'Probability that the flame goes out by itself within {s}'}[lang]
    return TARGETS.get(target, {'es': target, 'en': target})[lang]


def prediction_sentences(p: Prediction, lang: str) -> list[str]:
    tt = target_text(p.target, lang)
    out = []
    if p.probability is None:
        if p.target == 'ignition' and p.notes:
            return [f'{tt}: ' + p.notes[0]]
        out.append(t('no_pred', lang, t=tt, why='; '.join(p.out_of_domain) or '—'))
    else:
        out.append(t('pred', lang, t=tt, p=pct(p.probability), lo=pct(p.ci_low), hi=pct(p.ci_high)))
        if p.range_sensitivity:
            out.append(t('sens', lang, lo=pct(p.range_sensitivity[0]), hi=pct(p.range_sensitivity[1])))
    return out
