"""Helper: append cells to a notebook and execute it in place (keeps outputs)."""
import sys, json, nbformat
from nbconvert.preprocessors import ExecutePreprocessor
path, cells_file = sys.argv[1], sys.argv[2]
try:
    nb = nbformat.read(path, as_version=4)
except FileNotFoundError:
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
for c in json.load(open(cells_file, encoding="utf-8")):
    nb.cells.append(nbformat.v4.new_markdown_cell(c["src"]) if c["type"] == "md" else nbformat.v4.new_code_cell(c["src"]))
ExecutePreprocessor(timeout=600, kernel_name="python3").preprocess(nb, {"metadata": {"path": "notebooks"}})
nbformat.write(nb, path)
for c in nb.cells[-len(json.load(open(cells_file, encoding="utf-8"))):]:
    if c.cell_type == "code":
        for o in c.get("outputs", []):
            if o.get("output_type") == "stream": print(o["text"])
            elif "data" in o and "text/plain" in o["data"] and "image/png" not in o["data"]: print(o["data"]["text/plain"])
            elif "data" in o and "image/png" in o["data"]: print("[figure]")
            elif o.get("output_type") == "error": print("ERROR:", o["ename"], o["evalue"])
