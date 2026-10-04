"""Verifica que ninguna clave de API esté en el repositorio ni en su historial.

Toma los primeros 10 caracteres de GEMINI_API_KEY y XAI_API_KEY (de `.env`, que está
ignorado por git) y cuenta cuántas veces aparecen en: archivos versionados, diff,
cambios preparados, commits de la rama frente a `main` y archivos nuevos no ignorados.
También busca en todo el historial (`git log -p --all`) esos prefijos y cualquier
texto con forma de clave de Gemini (`AIza…`, `AQ.…`) o de xAI (`xai-…`).

Solo imprime conteos: nunca muestra las claves. Sale con código 1 si encuentra alguna.

Uso (antes de cada commit): python scripts/check_keys.py
"""

import re
import subprocess
import sys

from rag_bbva.config import Settings

FORMAS_DE_CLAVE = [
    r"AIza[0-9A-Za-z_\-]{35}",
    r"\bAQ\.[0-9A-Za-z_\-]{30,}",
    r"\bxai-[0-9A-Za-z]{40,}",
]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, errors="ignore").stdout


def main() -> int:
    ajustes = Settings()
    prefijos = [
        clave.get_secret_value()[:10]
        for clave in (ajustes.gemini_api_key, ajustes.xai_api_key)
        if clave and len(clave.get_secret_value()) >= 10
    ]
    textos = [
        _git("grep", "-I", "-h", "-e", "."),
        _git("diff"),
        _git("diff", "--cached"),
        _git("log", "-p", "main..HEAD"),
    ]
    for nombre in _git("ls-files", "--others", "--exclude-standard").split("\n"):
        if nombre:
            try:
                with open(nombre, encoding="utf-8", errors="ignore") as archivo:
                    textos.append(archivo.read())
            except OSError:
                pass
    locales = sum(texto.count(p) for texto in textos for p in prefijos)
    historial = _git("log", "-p", "--all", "--text")
    en_historial = sum(historial.count(p) for p in prefijos) + sum(
        len(re.findall(forma, historial)) for forma in FORMAS_DE_CLAVE
    )
    commits = _git("rev-list", "--all", "--count").strip()
    print(
        f"claves revisadas: {len(prefijos)} · apariciones en grep/diff/cached/rama/archivos "
        f"nuevos: {locales} · en git log -p --all ({commits} commits, prefijos y formas): "
        f"{en_historial}"
    )
    return 1 if locales or en_historial else 0


if __name__ == "__main__":
    sys.exit(main())
