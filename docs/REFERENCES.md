# References

## Data

All experimental data comes from **NASA Physical Sciences Informatics (PSI)**,
https://psi.nasa.gov, licensed CC0-1.0 (PSI-10 does not declare a license). Each
investigation must be cited by its DOI; the dashboard links every test to it.

| PSI | Investigation | DOI |
|---|---|---|
| PSI-10 | ACME / Flame Design | https://doi.org/10.60555/w4zy-bt45 |
| PSI-20 | ACME Burning Rate Emulator | https://doi.org/10.60555/vb93-3y72 |
| PSI-21 | ACME CLD Flame | https://doi.org/10.60555/23e1-9950 |
| PSI-22 | ACME E-FIELD Flames | https://doi.org/10.60555/se5a-8c73 |
| PSI-23 | ACME s-Flame | https://doi.org/10.60555/k57n-p030 |
| PSI-25 | BASS-II | https://doi.org/10.60555/4qc4-de67 |
| PSI-26 | BASS | https://doi.org/10.60555/58h4-dy80 |
| PSI-39 | Cool Flames Investigation (CFI) | https://doi.org/10.60555/m6pj-a914 |
| PSI-47 | DAFT / DAFT-2 | https://doi.org/10.60555/ztgj-d202 |
| PSI-68 | FLEX-2 | https://doi.org/10.60555/byp8-h018 |
| PSI-69 | FLEX | https://doi.org/10.60555/mbq8-0451 |
| PSI-98 | SAFFIRE-I | https://doi.org/10.60555/0t15-1z43 |
| PSI-99 | SAFFIRE-II | https://doi.org/10.60555/2chp-m469 |
| PSI-100 | SAFFIRE-III | https://doi.org/10.60555/040q-4h36 |
| PSI-101 | SAME-R | https://doi.org/10.60555/2np7-jy14 |
| PSI-102 | SAME | https://doi.org/10.60555/gd4k-8h62 |
| PSI-106 | SLICE | https://doi.org/10.60555/pm12-rg61 |
| PSI-107 | SPICE | https://doi.org/10.60555/9kbh-e962 |
| PSI-159 | ACME CFI-G | https://doi.org/10.60555/cyt1-kh64 |

The full list (24 investigations) is in `GET /investigations`.

## Context

- Exploration atmosphere reference point (34 % O2 at 8.2 psia ≈ 56.5 kPa): NASA
  Exploration Atmospheres Working Group recommendations, used only as a labelled
  reference on the extinction map.

## Software

| Software | License |
|---|---|
| Python, pandas, pyarrow | BSD / Apache-2.0 |
| FastAPI, Starlette, Pydantic, Uvicorn, HTTPX | MIT / BSD |
| psycopg2 | LGPL |
| PostgreSQL | PostgreSQL License |
| PGlite (validation only) | Apache-2.0 |
| pytest | MIT |
