"""Enmascarado de datos personales en la analítica (M11).

Las preguntas del historial pueden traer cédulas, números de cuenta o teléfonos. Antes
de mostrarlas en la analítica (preguntas frecuentes, brechas, exports) se reemplaza
cada número de 6 o más dígitos (contando dígitos aunque vengan separados por espacios,
puntos o guiones) por `[número]`.

No se enmascaran los montos precedidos por `$` ("$5.000.000"): son parte de la consulta
("CDT de $5.000.000") y no identifican a nadie. Un monto sin `$` ("5000000 pesos") sí
se enmascara: es preferible perder ese dato a mostrar una cédula.
"""

import re

MASCARA = "[número]"
MIN_DIGITOS = 6

# Un número: empieza y termina en dígito; en medio, dígitos y separadores sueltos.
# `+` opcional para teléfonos internacionales. No empieza a mitad de otro número
# ("5.000.000" no se parte en "5." + "000.000") ni justo después de `$`. Puede ir pegado
# a letras ("clave123456" también se enmascara).
_NUMERO = re.compile(r"(?<![\d$])(?<!\$ )(?<!\d[ .,\-])\+?\d(?:\d|[ .\-](?=\d))*\d")


def mask_numbers(texto: str) -> str:
    """Reemplaza por `[número]` los números de `MIN_DIGITOS` o más dígitos."""

    def reemplazo(m: re.Match[str]) -> str:
        digitos = sum(c.isdigit() for c in m.group(0))
        return MASCARA if digitos >= MIN_DIGITOS else m.group(0)

    return _NUMERO.sub(reemplazo, texto)
