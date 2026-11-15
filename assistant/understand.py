"""Step 2 of the pipeline: understand the question with RULES (criterion 3). Spanish and English.

Extracts `Conditions` (O2, CO2, He, pressure, flow, fuel/material, droplet size, support, flame type, gravity,
time horizon), the intent, the language and the terms NASA did not measure. Starting point: MG's
`parse_question` (Backend/api/modeling.py), extended to Spanish, more units and more intents.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from flame.contracts import Conditions

# word (accent-free, lower case) -> (family, field, code understood by flame.models / tests_search)
SUBSTANCES: dict[str, tuple[str, str, str]] = {
    "metanol": ("flex", "fuel", "methanol"), "methanol": ("flex", "fuel", "methanol"),
    "heptano": ("flex", "fuel", "heptane"), "n-heptano": ("flex", "fuel", "heptane"),
    "heptane": ("flex", "fuel", "heptane"), "n-heptane": ("flex", "fuel", "heptane"),
    "etano": ("cfig", "fuel", "ethane"), "ethane": ("cfig", "fuel", "ethane"),
    "propano": ("cfig", "fuel", "propane"), "propane": ("cfig", "fuel", "propane"),
    "butano": ("cfig", "fuel", "butane"), "n-butano": ("cfig", "fuel", "butane"),
    "butane": ("cfig", "fuel", "butane"), "n-butane": ("cfig", "fuel", "butane"),
    "algodon": ("solids", "material", "sibal"), "cotton": ("solids", "material", "sibal"),
    "sibal": ("solids", "material", "sibal"), "tela": ("solids", "material", "sibal"),
    "fabric": ("solids", "material", "sibal"), "pmma": ("solids", "material", "pmma"),
    "acrilico": ("solids", "material", "pmma"), "acrylic": ("solids", "material", "pmma"),
    "plexiglas": ("solids", "material", "pmma"), "plexiglass": ("solids", "material", "pmma"),
    "nomex": ("solids", "material", "nomex"), "ultem": ("solids", "material", "ultem"),
    "cera": ("solids", "material", "wax"), "vela": ("solids", "material", "wax"),
    "wax": ("solids", "material", "wax"), "candle": ("solids", "material", "wax"),
}
# material code -> family name used in the curated BASS table (tests_search `material`)
SOLID_FAMILY_NAME = {"sibal": "SIBAL fabric", "nomex": "Nomex", "ultem": "Ultem", "wax": "wax", "pmma": None}
GRAVITY = {"lunar": 0.165, "luna": 0.165, "moon": 0.165, "marte": 0.38, "martian": 0.38, "mars": 0.38,
           "microgravedad": 0.0, "microgravity": 0.0, "orbita": 0.0, "orbit": 0.0, "estacion espacial": 0.0,
           "space station": 0.0, "iss": 0.0}
UNMEASURED = {"humedad": "humidity", "humidity": "humidity", "radiacion": "radiation", "radiation": "radiation",
              "polvo": "dust", "dust": "dust", "regolito": "regolith", "regolith": "regolith",
              "temperatura exterior": "outside temperature", "outside temperature": "outside temperature",
              "viento solar": "solar wind", "vacio": "vacuum", "vacuum": "vacuum"}
PRESSURE_UNITS = {"psia": 6.89476, "psi": 6.89476, "kpa": 1.0, "atm": 101.325, "bar": 100.0, "mmhg": 0.133322}
NUM = r"(\d+(?:[.,]\d+)?)"
GASES = {"o2_frac": r"(?:oxigeno|oxygen|o2)", "co2_frac": r"(?:co2|dioxido de carbono|carbon dioxide)",
         "he_frac": r"(?:helio|helium|he)"}
EXPERIMENTS = {r"\bflex\b": "PSI-69", r"\bcfi-?g\b|cool flames investigation": "PSI-159", r"\bbass-?ii\b|\bbass 2\b": "PSI-25",
               r"\bbass\b(?!-?ii)": "PSI-26", r"\bsaffire\b": "PSI-98"}
COMBUSTION_WORDS = r"llama|fuego|incendi|combust|apag|arde|quem|prend|encend|ignic|ignit|flame|fire|burn|extingu|extinc|radiat|" \
                   r"hollin|soot|propagac|spread|microgravedad|microgravity|oxigeno|oxygen|supresi|suppress|" \
                   r"gota|droplet|oxigen|oxygen|nasa|experiment|prueba|ensayo|test|material|flex|cfi|bass|saffire|" \
                   r"dataset|\bdatos\b|\bdata\b|supreso|suppressant|nitrogen|helio|helium|cabina|cabin|habitat|" \
                   r"marte|mars|\bluna\b|\bmoon\b|espacio|space|\biss\b|inflamab|flammab|atmosfera|atmosphere"

VARIABLES = {   # word -> SweepVariable (mentors' "how does X act" / "what if X changes")
    "aire enriquecido": "o2_frac", "enriched air": "o2_frac", "enriquecid[ao] en oxigeno": "o2_frac", "oxygen.?rich": "o2_frac",
    "co2": "co2_frac", "dioxido de carbono": "co2_frac", "carbon dioxide": "co2_frac",
    "helio": "he_frac", "helium": "he_frac", "oxigeno": "o2_frac", "oxygen": "o2_frac", "o2": "o2_frac",
    "presion": "pressure_kpa", "pressure": "pressure_kpa", "tamano de (?:la )?gota": "droplet_d0_mm",
    "gotas? (?:mas )?(?:grandes?|chic[ao]s?|pequen[ao]s?)": "droplet_d0_mm", "(?:big|large|small)(?:er)? droplets?": "droplet_d0_mm",
    "tamano": "droplet_d0_mm", "size": "droplet_d0_mm",
    "diametro": "droplet_d0_mm", "droplet size": "droplet_d0_mm", "diameter": "droplet_d0_mm",
}

INTENT_RULES: list[tuple[str, str]] = [   # order matters: first match wins
    # --- analytic questions over ALL the data (ranges/thresholds, rankings, gaps) ---
    ("gaps", r"\bfalta\w*|limitacion\w*|limitation\w*|que (?:condiciones|combinaciones|cosas|variables) nunca|que no se (?:probo|midio)|"
             r"what (?:was|were) never (?:tested|measured)|which conditions were never|futur\w*|suger\w*|suggest\w*|recomend\w*|recommend\w*|"
             r"nuev[oa]s? (?:ensayos|experimentos|pruebas|tests)|new (?:tests|experiments)|que experimentos|which experiments|"
             r"que habria que|gaps?\b|huecos|lagunas|weak(?:ness|nesses)? of the data|debilidad\w*"),
    ("rank", r"\b(?:las|los|top)\s+\d+\b|\b\d+\s+(?:configuraciones|condiciones|combinaciones|pruebas|atmosferas|materiales|conditions|configurations|combinations|tests)|"
             r"ranking|\brank\b|mas (?:tienden|probable\w*|peligros\w*|inflamable\w*|dificil\w* de apagar|tardan|duran|tiempo|rapido)|"
             r"menos (?:inflamable\w*|tardan|duran)|(?:cuales|que) (?:fueron|son) (?:las|los) (?:configuraciones|condiciones|pruebas|combinaciones|atmosferas|materiales)|"
             r"(?:que|cuales) (?:configuraciones|combinaciones|atmosferas|materiales)|which (?:\d+ )?(?:conditions|configurations|combinations|tests|atmospheres|materials)|"
             r"\b(?:most|least) (?:often|flammable|likely)|longest|fastest|quickest|mas rapido|se apagaron siempre|always went out"),
    ("profile", r"\brango\w*|umbral\w*|a partir de que|a partir de cuant\w*|desde que|por debajo de|por encima de|minim\w*|maxim\w*|"
                r"threshold\w*|\brange\b|below which|above which|at which|hay alg[uo]n\w* (?:nivel|porcentaje|tamano|valor|rango)|"
                r"is there (?:any|a) .{0,20}(?:level|value|size|range)|en que (?:nivel|niveles|porcentaje\w*|rango|valores)|con que niveles|"
                r"what (?:range|level)|que niveles"),
    ("risk", r"riesgo de (?:la|esta|una) atmosfera|atmosfera (?:de (?:la )?(?:cabina|iss|estacion)|de exploracion|del habitat)|es segur[ao]|"
             r"(?:que tan |cuan )?(?:riesgos[ao]|peligros[ao]) (?:es|seria) (?:la|esta|una) (?:atmosfera|cabina)|"
             r"alerta|alarma|semaforo|cabin atmosphere|exploration atmosphere|habitat atmosphere|is (?:it|this) safe|early warning|"
             r"risk (?:of|for) (?:this|the|a) (?:atmosphere|cabin)|que tan peligros|how dangerous"),
    ("scenario", r"que pasa(?:ria)? si|que pasaria con|y si (?:sub|baj|aument|reduc|pong|agreg)|what if|what happens if|si (?:subo|bajo|aumento|reduzco)|"
                 r"(?:subiendo|bajando|aumentando|reduciendo|variando) (?:el|la)|(?:increase|decrease|vary|varying|raise|lower)\w* (?:the )?"
                 r"(?:co2|oxygen|o2|helium|pressure|droplet)|escenario|scenario|de \d+(?:[.,]\d+)? a \d+(?:[.,]\d+)? ?%"),
    ("explain_variable", r"como (?:incide|influye|afecta|actua|impacta)|que (?:efecto|papel|rol) (?:tiene|cumple)|efecto del?|"
                         r"how does (?:the )?(?:co2|oxygen|o2|helium|pressure|droplet size|carbon dioxide) (?:affect|influence|act|matter)|"
                         r"effect of|role of|con que (?:variables )?se relaciona|what (?:is it|does it) relate|influencia del?"),
    ("count", r"cuant[ao]s|how many|numero de (?:pruebas|ensayos|experimentos)|number of (?:tests|experiments)|en cuant[ao]s|"
              r"que porcentaje de (?:las )?pruebas|what share of (?:the )?tests|cuantas veces"),
    ("coverage", r"hay datos|hay pruebas|is there (?:any )?data|are there (?:any )?(?:data|tests)|se probo|se probaron|probo nasa|"
                 r"was (?:it )?tested|were .* tested|nunca se probo|never tested|sin datos|no data|que rango|what range|cobertura|coverage"),
    ("measure", r"(?:diametro|tamano|size|diameter)\w* (?:de la gota )?(?:al apagarse|a la extincion|de extincion|al extinguirse|at extinction)|"
                r"(?:extinction|extincion) (?:diameter|diametro)|tasa de quemado|velocidad de quemado|burn(?:ing)? rate|"
                r"(?:cuant[oa]s?|typical|tipic\w*|median\w*|promedio|average|valor\w*|rango|range|con que|en que (?:condiciones|rango|diametro)|"
                r"how often|how many|frecuencia|which conditions|que condiciones|what was the).{0,60}"
                r"(?:llamas? frias?|cool flames?|hollin|soot)|(?:llamas? frias?|cool flames?).{0,60}(?:diametro|diameter|oxigeno|oxygen|rango|range)"),
    ("compare", r"compar|versus|\bvs\.?\b|diferencia entre|difference between|que es mas|which is more|mejor que|better than"),
    ("explain_doc", r"por que|porque se|why|que es (?:una|un|el|la)|what is (?:a|an|the)|como funciona|how does|explica|explain|"
                    r"que dice (?:la )?nasa|what does nasa say|que (?:causa|causo)|what caus|mecanismo|mechanism|que significa|what does .* mean|"
                    r"como se hizo|how was .* (?:done|run|performed)|objetivo|objective|que se aprendio|what was learned"),
    ("predict", r"se apaga|se apagaria|se extingu|apagaria|goes out|go out|extinguish|arde|arderia|burn|se quema|prende|prenderia|"
                r"enciende|ignite|ignition|ignicion|probabilidad|probability|chance|riesgo|risk|sobrevive|survive|self.?exting|"
                r"cuanto tarda|how long"),
]


@dataclass
class Understanding:
    text: str
    lang: str                                   # "es" / "en"
    intent: str                                 # predict / count / coverage / compare / explain_doc / off_topic
    conditions: Conditions
    substances: list[tuple[str, str, str]] = field(default_factory=list)   # all substances mentioned, in order
    accessions: list[str] = field(default_factory=list)                    # experiments named in the question (PSI ids)
    unknown_terms: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    variable: str | None = None                 # variable asked about (scenario / explain_variable / profile)
    analytic_family: str | None = None          # flex / cfig / solids for analytic questions (None = all)
    rank_target: str | None = None              # persistence / extinction / burn_time / fastest_out
    quantity: str | None = None                 # measure: dext / burn_rate / burn_time / cool_flame / soot (DT-025)


FAMILY_WORDS = [('flex', r"\bgotas?\b|droplets?|liquid|liquidos?|metanol|methanol|heptan\w*|\bflex\b"),
                ('cfig', r"llamas? de gas|gas flames?|\bgases\b|\bgas\b|etano|ethane|propan\w*|butan\w*|cfi-?g|quemador|burner"),
                ('solids', r"solid\w*|materiales|materials?|telas?|fabric|nomex|pmma|acrilic\w*|acrylic|algodon|cotton|sibal|"
                           r"ultem|velas?|candles?|bass")]


def find_family(t: str) -> str | None:
    hits = [(m.start(), fam) for fam, pat in FAMILY_WORDS for m in [re.search(pat, t)] if m]
    return sorted(hits)[0][1] if hits else None


def find_rank_target(t: str) -> str:
    if re.search(r"tard\w* en apagar|duran|dura mas|mas tiempo|ardieron|longest|kept burning the longest|burn\w* (?:the )?longest|"
                 r"longer|tiempo de quemado|burn time", t):
        return 'burn_time'
    if re.search(r"mas rapido|apagaran? mas rapido|se apagan? primero|fastest|quickest|faster", t):
        return 'fastest_out'
    if re.search(r"se apagaron siempre|always went out|apagan? mas|menos inflamable|least (?:flammable|likely to burn)|"
                 r"mas facil\w* de apagar|easiest to (?:put out|extinguish)", t):
        return 'extinction'
    return 'persistence'


def find_quantity(t: str) -> str | None:
    """A quantity NASA measured in each test (DT-025): diameter at extinction, burning rate, burn time, cool flames, soot."""
    for q, pat in (('dext', r"extincion|extinction|al apagarse|al extinguirse"), ('burn_rate', r"tasa de quemado|velocidad de quemado|burn(?:ing)? rate|d.?squared|d2"),
                   ('cool_flame', r"llamas? frias?|cool flames?"), ('soot', r"hollin|soot"),
                   ('burn_time', r"tiempo de quemado|burn(?:ing)? time|cuanto (?:tiempo )?(?:tarda|dura)|how long")):
        if re.search(pat, t):
            return q
    return None


def find_variable(t: str) -> str | None:
    """The variable the question is about: the one right after 'subo/how does/efecto del', else the first mentioned."""
    m = re.search(r"(?:subo|bajo|aumento|reduzco|subiendo|bajando|aumentando|reduciendo|variando|incide|influye|afecta|actua|"
                  r"impacta|efecto del?|influencia del?|increase|decrease|vary|varying|raise|lower|does|effect of|role of)\s+(?:el |la |the )?(.{0,25})", t)
    for seg in ([m.group(1)] if m else []) + [t]:
        hits = [(mm.start(), var) for word, var in VARIABLES.items() for mm in [re.search(rf"\b{word}\b", seg)] if mm]
        if hits:
            return sorted(hits)[0][1]
    return None


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return t.replace("₂", "2").replace("–", "-")


def detect_lang(text: str) -> str:
    t = normalize(text)
    if "¿" in text or "¡" in text or "ñ" in text.lower():
        return "es"
    es = len(re.findall(r"\b(que|como|cuant\w*|por|se|una?|los|las|del|con|sin|gota|llama|oxigeno|presion|prueba\w*|hay|es|para|apaga\w*)\b", t))
    en = len(re.findall(r"\b(what|how|does|the|is|are|with|without|droplet|flame|oxygen|pressure|test\w*|there|for|of)\b", t))
    return "es" if es > en else "en"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _pct(v: float) -> float:
    return v / 100 if v > 1 else v       # "30 %" -> 0.30 ; "0.3" -> 0.30


def extract_conditions(t: str) -> tuple[dict, list[tuple[str, str, str]], list[str]]:
    vals: dict = {}
    notes: list[str] = []
    for fld, gas in GASES.items():
        m = (re.search(NUM + r"\s*%\s*(?:de\s+|of\s+)?" + gas + r"\b", t)
             or re.search(r"\b" + gas + r"\b\s*(?:al|a|de|=|:|of|at)?\s*" + NUM + r"\s*%", t)
             or re.search(r"\b" + gas + r"\b\s*(?:=|:)\s*" + NUM + r"\b", t))
        if m:
            vals[fld] = round(_pct(_num(m[1])), 4)
    if m := re.search(NUM + r"\s*(psia|psi|kpa|atm|bar|mmhg)\b", t):
        vals["pressure_kpa"] = round(_num(m[1]) * PRESSURE_UNITS[m[2]], 3)
    if m := re.search(NUM + r"\s*cm/s", t):
        vals["flow_cm_s"] = _num(m[1])
    if m := re.search(NUM + r"\s*mm\b", t):
        vals["droplet_d0_mm"] = _num(m[1])
    if m := re.search(NUM + r"\s*(?:s|seg|segundos|sec|seconds)\b", t):
        vals["horizon_s"] = _num(m[1])
    if m := re.search(NUM + r"\s*mg/s", t):
        vals["fuel_flow_mg_s"] = _num(m[1])
    if re.search(r"\b(?:fibra|fiber|fibre|hilo|tethered|suspendida en)\b", t):
        vals["support"] = "fiber"
    elif re.search(r"\b(?:libre|free.?floating|free droplet|flotando)\b", t):
        vals["support"] = "free"
    if re.search(r"\binvers[ao]s?\b|\binverse\b", t):
        vals["flame_type"] = "inverse"
    for word, g in GRAVITY.items():
        if re.search(rf"\b{word}\b", t):
            vals["gravity_g"] = g
            break
    found = []
    for word, sub in SUBSTANCES.items():
        if (m := re.search(rf"(?<![\w-]){re.escape(word)}\b", t)) and sub not in [s for _, s in found]:
            found.append((m.start(), sub))
    substances = [s for _, s in sorted(found)]
    if substances:
        family, fld, code = substances[0]
        vals["family"] = family
        vals[fld] = code
    return vals, substances, notes


def matched_intents(t: str) -> list[str]:
    """Every intent whose keyword pattern matches (0 = the rules fell back to a default; >1 = ambiguous wording)."""
    return [intent for intent, pattern in INTENT_RULES if re.search(pattern, t)]


def classify(t: str, has_substance: bool, has_numbers: bool, has_variable: bool = False) -> str:
    on_topic = has_substance or has_numbers or has_variable or bool(re.search(COMBUSTION_WORDS, t))
    for intent, pattern in INTENT_RULES:
        if re.search(pattern, t):
            return intent if (on_topic or intent == "risk") else "off_topic"   # "best pizza?" is not a document question
    if has_substance and has_numbers:
        return "predict"
    if has_substance or re.search(COMBUSTION_WORDS, t):
        return "explain_doc"
    return "off_topic"


def understand(text: str) -> Understanding:
    t = normalize(text)
    vals, substances, notes = extract_conditions(t)
    has_numbers = any(k in vals for k in ("o2_frac", "co2_frac", "he_frac", "pressure_kpa", "droplet_d0_mm"))
    variable = find_variable(t)
    intent = classify(t, bool(substances), has_numbers, bool(variable))
    if intent in ("scenario", "explain_variable") and not variable:
        intent = "predict" if intent == "scenario" else "explain_doc"
    unknown = sorted({v for k, v in UNMEASURED.items() if re.search(rf"\b{k}\b", t)})
    if intent == "off_topic" and re.search(COMBUSTION_WORDS, t):
        intent = "explain_doc"
    if intent == "off_topic" and ("gravity_g" in vals or unknown):
        intent = "coverage"              # "what about lunar gravity / humidity?" asks what NASA measured
    if intent == "explain_doc" and substances and "gravity_g" in vals:
        intent = "predict"               # "what happens on Mars with a methanol droplet?" asks for an outcome
    if intent in ("explain_doc", "predict") and variable and not has_numbers and re.search(
            r"\b(mas|menos|more|less|enriquecid\w*|enriched|aument\w*|reduc\w*|sub\w+|baj\w+|increas\w*|decreas\w*|"
            r"rich|alto|alta|bajo|baja|high|low|cuesta|aguanta|dura|influ\w*|afect\w*|lower\w*|rais\w*|help\w*|"
            r"conviene|en vez de|instead of|better|mejor|grandes?|chic[ao]s?|big\w*|small\w*|large\w*|"
            r"ayuda\w*|sirve\w*|favorec\w*|dificult\w*|facilit\w*|hace que|makes?)\b", t):
        intent = "explain_variable"      # "with more CO2, is it harder for the droplet to keep burning?" -> learned effect
    if intent in ("off_topic", "explain_doc") and variable and not substances and re.search(
            r"peligr\w*|riesg\w*|danger\w*|risk\w*|efecto|effect|impact\w*|influ\w*|afect\w*", t):
        intent = "explain_variable"      # "what is the danger of high pressure?" -> what the models learned about pressure
    if intent == "off_topic" and re.search(r"configuracion\w*|combinacion\w*|condiciones|configurations?|conditions|experiment", t) \
            and re.search(r"incendi|prend|inflamab|flammab|apag|extingu|burn|arde", t):
        intent = "rank"
    accessions = [acc for pat, acc in EXPERIMENTS.items() if re.search(pat, t)]
    analytic_family = (vals.get("family") or find_family(t)) if intent in ("profile", "rank", "gaps", "measure") else None
    quantity = find_quantity(t) if intent == "measure" else None
    if intent == "measure" and not quantity:
        intent = "explain_doc"
    if intent == "profile" and not variable:
        variable = "pressure_kpa" if re.search(r"presion|pressure", t) else "o2_frac"
    return Understanding(text=text, lang=detect_lang(text), intent=intent, conditions=Conditions(**vals),
                         substances=substances, unknown_terms=unknown, notes=notes, accessions=accessions, variable=variable,
                         analytic_family=analytic_family, rank_target=find_rank_target(t) if intent == "rank" else None,
                         quantity=quantity)
