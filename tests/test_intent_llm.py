"""LLM help to classify the kind of question (DT-022): closed lists, policies, and what each intent gets filled with."""
import json

from assistant.intent_llm import choose, classify_llm, refine
from assistant.understand import matched_intents, normalize, understand


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, system, user, max_tokens=120, temperature=0.0):
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def test_classify_keeps_only_closed_list_values():
    r = classify_llm('x', FakeLLM({'intent': 'rank', 'variable': 'humidity', 'target': 'burn_time', 'family': 'mars'}))
    assert r == {'intent': 'rank', 'second': None, 'variable': None, 'target': 'burn_time', 'quantity': None, 'family': None}
    assert classify_llm('x', FakeLLM({'intent': 'write_a_poem'})) is None
    assert classify_llm('x', FakeLLM('not json at all')) is None


def test_policies():
    llm_read = {'intent': 'rank', 'variable': None, 'target': None, 'family': None}
    assert choose('explain_doc', [], llm_read, 'rules') == 'explain_doc'
    assert choose('explain_doc', [], llm_read, 'llm') == 'rank'
    assert choose('explain_doc', [], llm_read, 'unsure') == 'rank'                 # no keyword matched: LLM
    assert choose('gaps', ['gaps'], llm_read, 'unsure') == 'gaps'                  # one clear keyword: rules
    assert choose('gaps', ['gaps', 'profile'], llm_read, 'unsure') == 'rank'      # ambiguous: LLM
    assert choose('gaps', ['gaps'], None, 'llm') == 'gaps'                         # LLM unusable: rules


def test_refine_fills_what_the_new_intent_needs():
    q = '¿Cuál fue el material que menos sostuvo la llama?'
    u = refine(understand(q), FakeLLM({'intent': 'rank', 'target': 'extinction', 'family': 'solids'}), 'llm')
    assert u.intent == 'rank' and u.rank_target == 'extinction' and u.analytic_family == 'solids'
    assert any('read by the LLM' in n for n in u.notes)


def test_refine_falls_back_when_the_intent_cannot_run():
    u = refine(understand('¿Qué es una llama fría?'), FakeLLM({'intent': 'explain_variable'}), 'llm')
    assert u.intent == 'explain_doc'                    # explain_variable without a variable cannot run


def test_matched_intents_exposes_ambiguity():
    assert matched_intents(normalize('¿Qué experimentos faltan?')) == ['gaps']
    assert matched_intents(normalize('hola como estas')) == []


def test_guarded_policy_protections():
    on_topic = understand('¿Qué pasa en Marte con una gota de metanol?')
    assert choose(on_topic.intent, [], {'intent': 'off_topic', 'variable': None}, 'guarded', on_topic) == on_topic.intent
    no_o2 = understand('¿Qué peligro tiene subir el oxígeno en una cabina?')
    assert choose(no_o2.intent, [], {'intent': 'risk', 'variable': None}, 'guarded', no_o2) == no_o2.intent
    ok = understand('¿Cuál fue el material que menos sostuvo la llama?')
    assert choose(ok.intent, [], {'intent': 'rank', 'variable': None}, 'guarded', ok) == 'rank'


def test_guarded_uses_the_second_choice_when_the_first_cannot_run():
    u = understand('el fuego en el espacio es más peligroso o menos que acá?')
    read = {'intent': 'risk', 'second': 'explain_doc', 'variable': None}
    assert choose(u.intent, ['rank'], read, 'guarded', u) == 'explain_doc'


def test_verifier_rejects_model_attribution_without_model_evidence():
    from assistant.verify import verify
    ev = '[D1] NASA report: in microgravity the flame is spherical.'
    bad = verify('La tendencia aprendida por los modelos es que la llama es esférica [D1].', 'answer', 'q', ev, {'D1'},
                 'answer', model_evidence=False)
    good = verify('Los informes de NASA dicen que la llama es esférica [D1].', 'answer', 'q', ev, {'D1'}, 'answer',
                  model_evidence=False)
    assert any('attributes to the ML models' in p for p in bad) and not good


def test_follow_ups_inherit_only_when_worded_as_follow_ups():
    from assistant.pipeline import answer
    from assistant.tools import LocalTools
    from flame.contracts import AskRequest, ChatTurn
    h = [ChatTurn(role='user', text='¿Se apaga una gota de metanol con 18 % de oxígeno?'), ChatTurn(role='assistant', text='.')]
    other = answer(AskRequest(text='¿Cuántas pruebas de CFI-G no encendieron?', history=h), tools=LocalTools())
    assert other.understood.fuel is None and '333' in other.text
    follow = answer(AskRequest(text='¿Y con 30 % de CO2?', history=h), tools=LocalTools())
    assert follow.understood.fuel == 'methanol' and follow.understood.co2_frac == 0.3


def test_profile_answers_for_the_asked_material_first():
    from flame.analytics import profile, profile_groups
    d = profile('o2', 'solids', 'nomex')
    assert 'Nomex' in profile_groups(d)[0]['group']
    m = profile_groups(profile('o2', 'flex', 'methanol'))[0]
    assert 'methanol' in m['group'] and m['zones_always']
