"""Contract between the two halves of the project.

The source of truth is `flame/contracts.py` (v2), owned by Lautaro [LC]. This module only re-exports it so the
API keeps importing `from .contracts import ...`. Never redefine a model here: request changes in NOTAS.md
as "CAMBIO DE CONTRATO".
"""
from flame.contracts import *  # noqa: F401,F403
