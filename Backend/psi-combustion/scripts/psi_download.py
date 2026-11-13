"""Descarga archivos públicos de NASA PSI a partir de los CSV del catálogo.

Uso (desde tu máquina; el sandbox donde se armó el catálogo no llega a psi.nasa.gov):
    python psi_download.py ../catalog/rag_documentos_pdf.csv --out data/psi --skip-duplicates
    python psi_download.py ../catalog/psi_archivos_utiles.csv --out data/psi --kind experimental_table tabular
    python psi_download.py ../catalog/psi_archivos_utiles.csv --out data/psi --acc PSI-69 PSI-159 --max-mb 50

Endpoints (API pública de PSI, sin login, no documentada oficialmente):
    GET  https://psi.nasa.gov/geode-py/ws/repo/investigations/<PSI-ID>           -> metadatos JSON
    GET  https://psi.nasa.gov/geode-py/ws/studies/<PSI-ID>/download?file=<nombre>&version=<v>&redirect=false
         -> devuelve como texto una URL S3 firmada y de vida corta; después se baja esa URL.
Los archivos públicos tienen licencia CC0-1.0 (PSI-10 no declara licencia). Citar siempre el DOI.

NOTA: este script no se pudo probar contra PSI desde el entorno donde se escribió (host bloqueado).
Sigue el mismo patrón que usan otros equipos del reto; si un endpoint falla, revisá la pestaña Network
del navegador en https://psi.nasa.gov/physci/repo/ al hacer click en "Download".
"""
import argparse, csv, json, os, sys, time, urllib.parse, urllib.request

WS = "https://psi.nasa.gov/geode-py/ws"
UA = {"User-Agent": "psi-catalog-downloader/0.1 (research)"}


def get(url, timeout=120):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def file_versions(acc, cache={}):
    """Mapa nombre_de_archivo -> versión, leído de los metadatos de la investigación."""
    if acc not in cache:
        meta = json.loads(get(f"{WS}/repo/investigations/{acc}"))
        versions = {}

        def walk(o):
            if isinstance(o, dict):
                if "file_name" in o:
                    versions[o["file_name"]] = o.get("version", 1)
                    return
                for v in o.values(): walk(v)
            elif isinstance(o, list):
                for v in o: walk(v)
        walk(meta)
        cache[acc] = (versions, meta.get("version", 1))
    return cache[acc]


def signed_url(acc, fname, version):
    q = urllib.parse.urlencode({"file": fname, "version": version, "redirect": "false"}, quote_via=urllib.parse.quote)
    return get(f"{WS}/studies/{acc}/download?{q}").decode().strip().strip('"')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default="data/psi")
    ap.add_argument("--acc", nargs="*", help="solo estas investigaciones (PSI-xx)")
    ap.add_argument("--kind", nargs="*", help="solo estos tipos (experimental_table, document, tabular...)")
    ap.add_argument("--max-mb", type=float, default=60.0)
    ap.add_argument("--skip-duplicates", action="store_true")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.csv, encoding="utf-8")))
    ok = fail = skip = 0
    for r in rows:
        if a.acc and r["accession"] not in a.acc: continue
        if a.kind and r["kind"] not in a.kind: continue
        if a.skip_duplicates and r.get("duplicate_of_other_investigation") == "True": continue
        if int(r["bytes"] or 0) > a.max_mb * 1e6: skip += 1; continue
        dest = os.path.join(a.out, r["accession"], r["category"], r["file"])
        if os.path.exists(dest): skip += 1; continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            versions, study_v = file_versions(r["accession"])
            v = versions.get(r["file"], study_v)
            data = get(signed_url(r["accession"], r["file"], v), timeout=300)
            with open(dest + ".part", "wb") as fh: fh.write(data)
            os.replace(dest + ".part", dest)
            ok += 1
            print(f"ok   {r['accession']} {r['file']} ({len(data):,} B)")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"FAIL {r['accession']} {r['file']}: {e}", file=sys.stderr)
        time.sleep(0.2)
    print(f"descargados={ok} fallidos={fail} salteados={skip}")


if __name__ == "__main__":
    main()
