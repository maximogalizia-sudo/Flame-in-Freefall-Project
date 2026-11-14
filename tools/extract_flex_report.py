"""Extract per-test information from NASA/TP-2015-216046 (FLEX detailed results) that the PSI CSV lacks.

Source: Dietrich et al. (2015), "Detailed Results From the Flame Extinguishment Experiment (FLEX),
March 2009 to December 2011", NASA/TP-2015-216046, NTRS 20150023456.
Usage: python tools/extract_flex_report.py <report.pdf> <out.csv>

Output, one row per test description in Appendix B (Figures 5-280):
  flex_no (official FLEX number), flex_id (CIR identifier, from Table IX), figure, page (PDF page),
  support (free / fiber), caption environment (O2, pressure), quality flags (mentions in the text),
  caption (verbatim). Flags record that the report MENTIONS the issue; absence of a mention is not
  proof that the issue did not happen.
"""
import re, sys, csv
from pypdf import PdfReader

pdf, out = sys.argv[1], sys.argv[2]
pages = [(i + 1, re.sub(r'\s+', ' ', p.extract_text() or '')) for i, p in enumerate(PdfReader(pdf).pages)]
flat, starts = '', []
for n, t in pages:
    starts.append((len(flat), n)); flat += t + ' '

def page_of(pos):
    return [n for s, n in starts if s <= pos][-1]

# Table IX: official number -> CIR identifier
ids = {}
for m in re.finditer(r'FLEX–(\d{3}) (\S+) \d{1,2}/\d{1,2}/\d{2}', flat):
    ids.setdefault(int(m.group(1)), m.group(2))

FLAGS = {
    'left_fov': r'(out of|left|exited|drifted out of) (the |all the )?[\w\s()-]{0,60}(FOV|field of view)',
    'extrapolated': r'extrapolat',
    'not_reliable': r'not reliable|unreliable',
    'not_measurable': r'could not be measured|could not be determined|not be measured',
    'fiber_dislodged': r'dislodg',
    'oscillation': r'oscillat',
    'cool_flame': r'cool[- ]flame',
    'soot_mentioned': r'soot',
}
rows = []
for m in re.finditer(r'Figure (\d+)\.?\s*—\s*Tests? FLEX\s*[–−-]\s*(\d{3})\.(.*?)(?=Figure \d+\.?\s*—|Appendix C|$)', flat):
    fig, no, cap = int(m.group(1)), int(m.group(2)), m.group(3).strip()
    cap = re.sub(r'NASA/TP—2015-216046 \d+', ' ', cap).strip()
    low = cap[:150].lower()
    support = 'free' if 'free-floating' in low or 'free floating' in low else ('fiber' if ('fiber' in low or 'tethered' in low) else '')
    env = re.search(r'in an? ([\w\s()]*?)\(?([\d./]+)\s*/?\s*([\d./]*)\s*/?\s*([\d./]*)\)?\s*([A-Z0-9/]+)[, ]+\s*([\d.]+)-atm', cap)
    o2 = p_atm = ''
    if env:
        o2 = env.group(2).split('/')[0]; p_atm = env.group(6)
    r = {'flex_no': no, 'flex_id': ids.get(no, ''), 'figure': fig, 'page': page_of(m.start()),
         'support': support, 'caption_o2_frac': o2, 'caption_pressure_atm': p_atm}
    for k, pat in FLAGS.items():
        r[k] = bool(re.search(pat, cap, re.I))
    r['caption'] = cap
    rows.append(r)
seen = set(); rows = [r for r in rows if not (r['flex_no'] in seen or seen.add(r['flex_no']))]
with open(out, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(f'{len(rows)} tests written to {out}')

# Table IX (results by test), verbatim values: '–' kept as written. Written next to `out`.
NUM = r'(–|~?-?\d*\.?\d+)'
pat = (r'FLEX–(\d{3}) (\S+) (\d{1,2}/\d{1,2}/\d{2}) (\d{1,2}:\d{2}:\d{2}|–) (Methanol|Heptane) '
       + ' '.join([NUM] * 9) + r' (Extinction|Disruption|Completion)')
t9 = []
for m in re.finditer(pat, flat):
    g = m.groups()
    t9.append({'flex_no': int(g[0]), 'flex_id': g[1], 'date': g[2], 'gmt': g[3], 'fuel': g[4],
               'pressure_mmhg': g[5], 'o2': g[6], 'n2': g[7], 'co2': g[8], 'he': g[9],
               'd0_mm': g[10], 'dext_mm': g[11], 'burn_rate_mm2_s': g[12], 'burn_time_s': g[13],
               'test_end': g[14], 'page': page_of(m.start())})
out9 = out.replace('flex_report_tests', 'flex_table_ix')
with open(out9, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(t9[0])); w.writeheader(); w.writerows(t9)
print(f'{len(t9)} Table IX rows written to {out9}')
