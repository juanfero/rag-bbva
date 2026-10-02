"""Parser y política de `robots.txt` según RFC 9309.

Se implementa a mano porque `urllib.robotparser` no soporta los comodines `*` y `$`,
que el sitio objetivo usa ampliamente (p. ej. `Disallow: /*pdf*`).
"""

import re
from dataclasses import dataclass, field
from functools import cached_property
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RobotsRule:
    """Regla `Allow`/`Disallow` con su patrón de ruta."""

    allow: bool
    pattern: str

    @cached_property
    def _regex(self) -> re.Pattern[str]:
        """Compila el patrón: `*` = cualquier secuencia, `$` final = fin de la ruta."""
        anclado = self.pattern.endswith("$")
        cuerpo = self.pattern[:-1] if anclado else self.pattern
        regex = ".*".join(re.escape(parte) for parte in cuerpo.split("*"))
        return re.compile(regex + ("$" if anclado else ""))

    def matches(self, path: str) -> bool:
        """Indica si la regla aplica a la ruta (incluida la query string)."""
        return self._regex.match(path) is not None


@dataclass(frozen=True)
class RobotsGroup:
    """Grupo de reglas asociado a uno o más User-Agents."""

    user_agents: tuple[str, ...]
    rules: tuple[RobotsRule, ...] = ()
    crawl_delay: float | None = None


@dataclass(frozen=True)
class RobotsTxt:
    """Contenido interpretado de un `robots.txt`."""

    groups: tuple[RobotsGroup, ...] = ()
    sitemaps: tuple[str, ...] = field(default_factory=tuple)

    def groups_for(self, user_agent: str) -> tuple[RobotsGroup, ...]:
        """Devuelve los grupos que aplican al User-Agent.

        Se compara el *product token* (parte previa a `/`) sin distinguir mayúsculas.
        Si ningún grupo lo nombra se usan los grupos `*`. Varios grupos que coinciden
        se combinan, como indica la RFC 9309.
        """
        token = user_agent.split("/", 1)[0].strip().lower()
        propios = tuple(g for g in self.groups if any(ua.lower() == token for ua in g.user_agents))
        if propios:
            return propios
        return tuple(g for g in self.groups if "*" in g.user_agents)

    def is_allowed(self, url: str, user_agent: str) -> bool:
        """Indica si el User-Agent puede descargar la URL (o ruta) indicada.

        Gana la regla con el patrón más largo; ante empate gana `Allow`.
        `/robots.txt` siempre está permitido.
        """
        partes = urlsplit(url)
        path = partes.path or "/"
        if partes.query:
            path = f"{path}?{partes.query}"
        if path == "/robots.txt":
            return True

        mejor: RobotsRule | None = None
        for group in self.groups_for(user_agent):
            for rule in group.rules:
                if not rule.matches(path):
                    continue
                if (
                    mejor is None
                    or len(rule.pattern) > len(mejor.pattern)
                    or (len(rule.pattern) == len(mejor.pattern) and rule.allow)
                ):
                    mejor = rule
        return mejor is None or mejor.allow

    def crawl_delay(self, user_agent: str) -> float | None:
        """Devuelve el mayor `Crawl-delay` declarado para el User-Agent, si existe."""
        delays = [g.crawl_delay for g in self.groups_for(user_agent) if g.crawl_delay is not None]
        return max(delays) if delays else None


def parse_robots(text: str) -> RobotsTxt:
    """Interpreta el texto de un `robots.txt`.

    Las líneas `User-agent` consecutivas forman un mismo grupo; una línea `User-agent`
    después de reglas abre un grupo nuevo. `Sitemap` es global. Las claves no
    reconocidas y las líneas mal formadas se ignoran, y una `Disallow` vacía no
    restringe nada.
    """
    grupos: list[RobotsGroup] = []
    sitemaps: list[str] = []
    agentes: list[str] = []
    reglas: list[RobotsRule] = []
    delay: float | None = None
    leyendo_agentes = False

    def cerrar_grupo() -> None:
        if agentes:
            grupos.append(RobotsGroup(tuple(agentes), tuple(reglas), delay))

    for linea in text.splitlines():
        linea = linea.split("#", 1)[0].strip()
        if ":" not in linea:
            continue
        clave, valor = (parte.strip() for parte in linea.split(":", 1))
        clave = clave.lower()

        if clave == "user-agent":
            if not leyendo_agentes:
                cerrar_grupo()
                agentes, reglas, delay = [], [], None
                leyendo_agentes = True
            if valor:
                agentes.append(valor)
        elif clave == "sitemap":
            if valor:
                sitemaps.append(valor)
        elif clave in {"allow", "disallow"}:
            leyendo_agentes = False
            if valor and agentes:
                reglas.append(RobotsRule(allow=clave == "allow", pattern=valor))
        elif clave == "crawl-delay":
            leyendo_agentes = False
            try:
                delay = float(valor)
            except ValueError:
                continue

    cerrar_grupo()
    return RobotsTxt(groups=tuple(grupos), sitemaps=tuple(sitemaps))
