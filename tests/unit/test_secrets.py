"""Las claves de API nunca se versionan (ADR-006, ADR-012, CLAUDE.md).

Revisa todos los archivos que git versiona o que se agregarían en el próximo commit
(no ignorados) y falla si alguno contiene algo con forma de clave de Gemini o de xAI,
o si `.env` dejara de estar ignorado. Si falla, no imprime la clave: solo el archivo.
"""

import re
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]

# Formatos de clave: Google AI Studio ("AIza…" clásica y "AQ.…" nueva) y xAI ("xai-…").
PATRONES_CLAVE = {
    "gemini_aiza": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "gemini_aq": re.compile(r"\bAQ\.[0-9A-Za-z_\-]{30,}"),
    "xai": re.compile(r"\bxai-[0-9A-Za-z]{40,}"),
}


def _git(*args: str) -> list[str]:
    try:
        salida = subprocess.run(
            ["git", *args], cwd=RAIZ, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("No es un repositorio git")
    return [linea for linea in salida.splitlines() if linea]


def test_ningun_archivo_del_repo_contiene_una_clave_de_api() -> None:
    archivos = set(_git("ls-files")) | set(_git("ls-files", "--others", "--exclude-standard"))
    hallazgos = []
    for nombre in sorted(archivos):
        ruta = RAIZ / nombre
        if not ruta.is_file():
            continue
        texto = ruta.read_text("utf-8", errors="ignore")
        hallazgos += [f"{nombre} ({tipo})" for tipo, p in PATRONES_CLAVE.items() if p.search(texto)]

    assert hallazgos == [], f"Posibles claves de API en archivos del repo: {hallazgos}"


def test_env_esta_ignorado_y_no_versionado() -> None:
    assert ".env" not in _git("ls-files")
    ignorado = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=RAIZ, check=False)
    assert ignorado.returncode == 0, ".env debe estar en .gitignore"


@pytest.mark.parametrize(
    ("texto", "tipo"),
    [
        ("GEMINI_API_KEY=AIza" + "x" * 35, "gemini_aiza"),
        ("GEMINI_API_KEY=AQ." + "Ab8" * 15, "gemini_aq"),
        ("XAI_API_KEY=xai-" + "a1" * 30, "xai"),
    ],
)
def test_los_patrones_detectan_cada_formato(texto: str, tipo: str) -> None:
    assert PATRONES_CLAVE[tipo].search(texto)


def test_los_patrones_no_marcan_textos_normales() -> None:
    normales = "XAI_API_KEY=\nGEMINI_API_KEY=\nempieza con 'xai-'\nAQ.*** (enmascarada)"
    assert not any(p.search(normales) for p in PATRONES_CLAVE.values())
