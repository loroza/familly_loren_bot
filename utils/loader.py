# utils/loader.py
import json
from pathlib import Path

_CACHED = None

def load_categories():
    """
    Carrega e retorna o JSON de categorias (caching simples).
    Espera que 'categorias.json' esteja na raiz do projeto.
    """
    global _CACHED
    if _CACHED is not None:
        return _CACHED

    p = Path(__file__).parent.parent / "categorias.json"
    if not p.exists():
        # fallback: vazio
        _CACHED = {"receitas": {"categorias": {}}, "despesas": {"categorias": {}}}
        return _CACHED

    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Espera formato: { "receitas": { "categorias": {...} }, "despesas": { "categorias": {...} } }
    _CACHED = data
    return _CACHED