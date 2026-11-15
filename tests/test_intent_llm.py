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
    assert r == {'intent': 'rank', 'variable': None, 'target': 'burn_time', 'family': None}
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
