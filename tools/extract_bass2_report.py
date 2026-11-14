"""Extract per-test observations from Appendix A of the BASS-II Summary Report (NASA/TM-20210011385).

The PSI CSV (PSI-25) has conditions but not the outcome text; the report's Appendix A has a column
"Test observations and anomalies (ignited, spread, extinguished, burn time, color, etc.)" per test.
Input: text extracted with pypdf (one '=== pN' marker per page). Output: CSV with test_id, investigator, page, observation.
Usage: python tools/extract_bass2_report.py <report.txt> <out.csv>
"""
import re, sys, csv

src, out = sys.argv[1], sys.argv[2]
text = open(src, encoding='utf-8').read()
start = text.index('These summary test matrices are grouped')
end = text.index('Appendix B.', start) if 'Appendix B.' in text[start:] else len(text)
app = text[start:end]

NUMTOK = re.compile(r'^(?:[–-]?\d+(?:[.,]\d+)?|OR|---+|NA|–|-|>?\d+|failed)$')   # 'failed' = sensor reading failed (P3b)
# a record starts with the as-run number (digits or dashes) followed by the test id, e.g. "--------- 57 B6" / "17:38 97 M10" / "15 M1"
REC = re.compile(r'(?m)(?:^|\s)(\d{1,3}|-{3,})\s+([BFMOPT]\d{1,2}[a-z]?)\s+(?!\d,?\d{3}\b)(\S*\d\S*)\s')
# the sample number contains a digit; this excludes 'F2 1,000' / 'F2 1000' / 'F2 1,600' (camera F-stop + ISO) and 'O2 reading' inside the text

pages = {m.start(): int(m.group(1)) for m in re.finditer(r'=== p(\d+)', app)}
def page_of(pos):
    p = [v for k, v in pages.items() if k <= pos]
    return p[-1] if p else None

ms = list(REC.finditer(app))
rows = []
for k, m in enumerate(ms):
    seg = app[m.start(): ms[k + 1].start() if k + 1 < len(ms) else len(app)]
    seg = re.split(r'\n[a-h](?:Greenwich|Polymethyl|Potentiometer|Continuous|Manual|Exposure|Number|Seconds)', seg)[0]   # drop table footnotes
    seg = re.split(r'\n\d{1,2}/\d{1,2}/\d{2,4}\s*\n', seg)[0]                                  # drop the next row's date
    seg = re.split(r'\nTABLE A\.\d', seg)[0]
    toks = seg.replace('\n', ' \n ').split(' ')
    toks = [t for t in toks if t != '']
    # find the last run of >= 8 consecutive numeric-like tokens (initial/final O2, CO2, CO); observation = what follows
    best = None; run = 0
    for i, t in enumerate(toks):
        if t == '\n':
            continue
        if NUMTOK.match(t):
            run += 1
            if run >= 8:
                best = i
        else:
            run = 0
    obs = ' '.join(t for t in toks[best + 1:] if t != '\n') if best is not None else ''
    obs = re.sub(r'\s*NASA/TM-20210011385 \d+.*$', '', obs)
    obs = re.sub(r'=== p\d+', ' ', obs)
    obs = re.sub(r'\s+', ' ', obs).strip()
    obs = re.sub(r'\s+\d{1,2}/\d{1,2}/\d{2,4}\b.*$', '', obs)   # next row's date leaking in
    rows.append({'test_id': m.group(2), 'investigator': m.group(2)[0], 'as_run_no': m.group(1), 'page': page_of(m.start()), 'observation': obs})

with open(out, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(len(rows), 'records ->', out)
print('empty observations:', [r['test_id'] for r in rows if not r['observation']])
