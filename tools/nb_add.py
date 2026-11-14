"""Append cells written in 'percent' format to a notebook and execute it in place (keeps outputs).

Cells file format:
    # %% [markdown]
    text...
    # %%
    code...
Usage: python tools/nb_add.py <notebook.ipynb> <cells.py>
"""
import sys, re, nbformat
from nbconvert.preprocessors import ExecutePreprocessor

path, cells_path = sys.argv[1], sys.argv[2]
src = open(cells_path, encoding='utf-8').read()
chunks = re.split(r'^# %%( \[markdown\])?[ \t]*$', src, flags=re.M)
new = []
for i in range(1, len(chunks), 2):
    is_md, body = chunks[i], chunks[i + 1].strip('\n')
    if is_md:
        body = '\n'.join(l[2:] if l.startswith('# ') else ('' if l == '#' else l) for l in body.split('\n'))
        new.append(nbformat.v4.new_markdown_cell(body))
    else:
        new.append(nbformat.v4.new_code_cell(body))
nb = nbformat.read(path, as_version=4)
nb.cells.extend(new)
ExecutePreprocessor(timeout=600, kernel_name='python3').preprocess(nb, {'metadata': {'path': 'notebooks'}})
nbformat.write(nb, path)
for c in nb.cells[-len(new):]:
    if c.cell_type == 'code':
        for o in c.get('outputs', []):
            if o.get('output_type') == 'stream':
                print(o['text'])
            elif 'data' in o and 'image/png' in o['data']:
                print('[figure]')
            elif 'data' in o and 'text/plain' in o['data']:
                print(o['data']['text/plain'])
