"""Etapa final: metricas agregadas por zona. Responsable: Persona 6.

Lee `<video_id>.interact.jsonl` y produce `<video_id>.metrics.json` con los
agregados por gondola y por estante, que es lo que importa
`backend/importer.py`.

Aritmetica pura: no toca video, ni YOLO, ni PostgreSQL -el pipeline de
vision NUNCA escribe en la base de datos, ver `docs/architecture.md`-. Por
eso aqui no hace falta el patron de imports pesados dentro de funciones.

LAS DOS TRAMPAS QUE ESTE MODULO EXISTE PARA EVITAR
---------------------------------------------------
1. CONTAR FILAS EN VEZ DE PERSONAS. Un mismo track_id aparece en cientos de
   eventos, uno por frame. "Cuanta gente paso" son IDENTIFICADORES DISTINTOS,
   jamas numero de filas: contando filas, una tienda por la que pasaron 3
   personas mostraria "3801 personas".

2. PROMEDIAR UN dwell_time ACUMULADO. Ese valor se repite y crece en cada
   evento de la misma persona, asi que promediar todas las filas cuenta el
   mismo recorrido cientos de veces. Aqui se toma el MAXIMO por
   (track_id, zone_id) -su ultimo acumulado- y se promedian esos maximos,
   uno por persona.

CADA EVENTO APORTA A DOS FILAS
-------------------------------
La de su GONDOLA y la de su ESTANTE. La fila de gondola sirve para el total
de la vitrina; las de estante permiten comparar "Cereales" contra "Snacks"
dentro de la misma gondola.

El id de una fila de estante es `<zone_id>:<segment>` -la MISMA convencion
que `_zone_id_de_estante()` en `backend/importer.py`-. Si aqui se usara otro
identificador, el importador no encontraria la fila que le hace match y
`GET /videos/{id}/zones` devolveria la gondola sin sus estantes.

Los eventos sin zona (alguien en el pasillo) se cuentan aparte en
'sin_zona' y se informan por pantalla, pero no generan fila: la tabla exige
un zone_id valido.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from gondola import pipeline
from gondola.config import Config
from gondola.contract import CONTRACT_VERSION
from gondola.jsonl import read_events


@dataclass
class _AgregadoZona:
    """Acumuladores en crudo para UNA gondola, mientras se recorre el archivo.

    Todo lo que aqui es un set() o un dict() existe para no repetir el error
    de contar filas: se guardan IDENTIFICADORES, y al final se cuenta cuantos
    hay, no cuantas veces aparecieron.
    """

    track_ids: set[int] = field(default_factory=set)
    interaction_count: int = 0
    pick_up_count: int = 0
    put_back_count: int = 0
    # dwell_time_s MAXIMO visto por cada track_id en esta zona. Un dict, no
    # una lista: si el mismo track_id aparece 500 veces, solo interesa su
    # valor mas alto, no las 500 filas.
    dwell_maximo_por_persona: dict[int, float] = field(default_factory=dict)
    # Primer y ultimo instante en que se vio cada track_id en esta zona. Solo
    # sirven para descartar las siluetas de un suspiro, ver
    # PRESENCIA_MINIMA_S y `personas_reales`.
    primer_visto: dict[int, float] = field(default_factory=dict)
    ultimo_visto: dict[int, float] = field(default_factory=dict)
    # QUIENES interactuaron y quienes tomaron algo, no cuantas veces. Ver
    # `cerrar_zona`: interaction_rate y conversion_rate son proporciones de
    # PERSONAS, y para eso hacen falta identificadores, no contadores.
    tracks_con_interaccion: set[int] = field(default_factory=set)
    tracks_con_pick_up: set[int] = field(default_factory=set)

    def personas_reales(self) -> set[int]:
        """Los track_id que estuvieron al menos PRESENCIA_MINIMA_S en la zona.

        Es el conjunto que cuenta como "personas": el resto son fragmentos de
        deteccion, no gente (ver la constante para los numeros medidos).
        """
        return {
            tid for tid in self.track_ids
            if self.ultimo_visto.get(tid, 0.0) - self.primer_visto.get(tid, 0.0)
            >= PRESENCIA_MINIMA_S
        }


@dataclass
class Resumen:
    """Lo que se informa por pantalla al terminar. Los MISMOS numeros que
    quedan en el JSON, para que sea imposible que la consola diga una cosa
    y el archivo diga otra."""

    eventos_leidos: int = 0
    zonas_con_datos: int = 0
    eventos_sin_zona: int = 0
    personas_totales: int = 0


# --------------------------------------------------------------------------
# Logica pura: se prueba con eventos construidos a mano, sin archivos.
# --------------------------------------------------------------------------

SEPARADOR_ESTANTE = ":"  # igual que backend/importer.py:_zone_id_de_estante

PRESENCIA_MINIMA_S = 1.0
"""Segundos que una silueta tiene que durar para contar como UNA PERSONA.

No es un numero elegido a gusto: sale de mirar la duracion de los 89 tracks
de los seis videos reales. Hay un corte limpio, no un degradado:

    video_002 -> los tracks de 0.00s a 1.27s tienen entre 1 y 13
                 observaciones; el siguiente ya salta a 64.
    video_004 -> de 0.00s a 1.13s, entre 1 y 28 observaciones; el
                 siguiente, 26 y subiendo hasta 525.

Es decir: por debajo de un segundo no hay nadie caminando, hay deteccion
que parpadeo. Sin este filtro, `people_count` cuenta esos parpadeos como
clientes distintos y el dashboard dice "26 personas" de un clip en el que
se ven bastantes menos. Con el, video_004 pasa de 26 a 15.

Lo que NO arregla, y hay que decirlo: si el rastreador pierde a alguien
durante mas de MAX_AGE_S y lo vuelve a enganchar con otro numero, los DOS
fragmentos duran mas de un segundo y los dos se cuentan. `people_count`
sigue siendo una cota superior de la gente real, no un censo. Ver
`ai-service/gondola/stages/track.py`, seccion "QUE NO RESUELVE".
"""


def _sumar_a_fila(agregados: dict[str, _AgregadoZona], fila_id: str, evento) -> None:
    """Suma un evento a UNA fila (gondola o estante). Compartida entre las
    dos filas a las que puede aportar un mismo evento -ver acumular_evento-
    para no repetir la aritmetica dos veces."""
    zona = agregados.setdefault(fila_id, _AgregadoZona())

    if evento.track_id is not None:
        zona.track_ids.add(evento.track_id)
        zona.primer_visto.setdefault(evento.track_id, evento.timestamp)
        zona.ultimo_visto[evento.track_id] = evento.timestamp
        if evento.metrics.dwell_time is not None:
            actual = zona.dwell_maximo_por_persona.get(evento.track_id, 0.0)
            if evento.metrics.dwell_time > actual:
                zona.dwell_maximo_por_persona[evento.track_id] = evento.metrics.dwell_time

    if evento.interaction.event is not None:
        zona.interaction_count += 1
        valor = evento.interaction.event.value
        if evento.track_id is not None:
            zona.tracks_con_interaccion.add(evento.track_id)
            if valor == "PICK_UP":
                zona.tracks_con_pick_up.add(evento.track_id)
        if valor == "PICK_UP":
            zona.pick_up_count += 1
        elif valor == "PUT_BACK":
            zona.put_back_count += 1


def acumular_evento(agregados: dict[str, _AgregadoZona], evento, resumen: Resumen) -> None:
    """Suma un evento a su gondola y, si trae estante, tambien a la fila de
    ese estante. Si no tiene ni zona, solo se cuenta aparte (ver
    'sin_zona' en Resumen).

    Eventos sin track_id (una deteccion que el tracker todavia no engancho a
    nadie, ver Persona 3) no aportan a people_count ni a dwell, pero SI
    pueden aportar a interaction_count si de casualidad trajeran una
    interaccion (en la practica no deberia pasar, pero no se asume).
    """
    resumen.eventos_leidos += 1

    zone_id = evento.zone.zone_id
    if zone_id is None:
        resumen.eventos_sin_zona += 1
        return

    _sumar_a_fila(agregados, zone_id, evento)

    segment = evento.zone.segment
    if segment is not None:
        _sumar_a_fila(agregados, f"{zone_id}{SEPARADOR_ESTANTE}{segment}", evento)


def _tasa(numerador: int, denominador: int) -> float | None:
    """Una tasa entre 0 y 1, o None si no hay base para calcularla.

    Acotada a 1.0 a proposito: metrics.interaction_rate/pick_up_rate/
    conversion_rate tienen un CHECK (... BETWEEN 0 AND 1) en schema.sql. Un
    PUT_BACK de algo tomado fuera de la ventana puede hacer que, en teoria,
    un conteo supere a otro; recortar aqui evita que el INSERT de la Persona
    7 se rompa por una tasa de 1.3.
    """
    if denominador <= 0:
        return None
    return round(min(1.0, numerador / denominador), 4)


def cerrar_zona(zona: _AgregadoZona) -> dict:
    """Convierte los acumuladores en crudo de una gondola en las columnas
    exactas que espera backend/importer.py (y, detras, metrics.people_count
    etc. de schema.sql)."""
    personas = zona.personas_reales()   # DISTINCT, nunca len(filas)
    people_count = len(personas)

    # La permanencia promedio se promedia entre LAS MISMAS personas que se
    # contaron arriba. Si no, un fragmento de 0.3s tira el promedio hacia
    # abajo sin aparecer en el denominador de ninguna tasa: dos numeros de
    # la misma fila hablando de poblaciones distintas.
    average_dwell_time_s = None
    valores = [
        v for tid, v in zona.dwell_maximo_por_persona.items() if tid in personas
    ]
    if valores:
        average_dwell_time_s = round(sum(valores) / len(valores), 3)

    return {
        "people_count": people_count,
        "interaction_count": zona.interaction_count,
        "pick_up_count": zona.pick_up_count,
        "put_back_count": zona.put_back_count,
        "average_dwell_time_s": average_dwell_time_s,
        # PROPORCIONES DE PERSONAS, no de eventos.
        #
        # Antes esto era `interaction_count / people_count` -lo que el
        # comentario de schema.sql llama "interacciones / personas"-, y eso
        # no es una proporcion: una misma persona puede alcanzar el estante
        # tres veces, asi que el numero pasa de 1 con facilidad. Como la
        # columna tiene un CHECK (BETWEEN 0 AND 1), `_tasa` lo recortaba a
        # 1.0 y el dashboard mostraba "100.0%" en TODAS las filas -leido por
        # cualquiera como "el 100% de la gente interactuo", que es una
        # afirmacion distinta y mas fuerte que la que sostienen los datos
        # (bug real, visto en pantalla con video_005: 19 interacciones entre
        # 8 personas daba 2.4, recortado a 1.0)-.
        #
        # Contando PERSONAS DISTINTAS que interactuaron al menos una vez, el
        # numero ya es una proporcion de verdad, nunca pasa de 1 sin recortar
        # nada, y dice lo que su nombre promete. `pick_up_rate` se queda como
        # estaba: ahi si se comparan eventos con eventos (cuantas de las
        # interacciones acabaron en toma) y un PICK_UP siempre es una
        # interaccion, asi que no puede pasar de 1.
        "interaction_rate": _tasa(len(zona.tracks_con_interaccion & personas), people_count),
        "pick_up_rate": _tasa(zona.pick_up_count, zona.interaction_count),
        "conversion_rate": _tasa(len(zona.tracks_con_pick_up & personas), people_count),
    }


# --------------------------------------------------------------------------
# Punto de entrada de la etapa
# --------------------------------------------------------------------------

def run(cfg: Config) -> int:
    """Ejecuta el calculo completo. Devuelve el codigo de salida.

    En STREAMING: se recorre interact.jsonl una vez, evento por evento, sin
    cargar el archivo entero en memoria (puede tener decenas de miles de
    lineas). Solo se acumulan sets/dicts pequenos, uno por gondola.
    """
    rutas = pipeline.stage_paths("metrics", cfg)
    pipeline.require_input("metrics", cfg)

    print(f"[metrics] Entrada: {rutas.input_path}")

    agregados: dict[str, _AgregadoZona] = {}
    resumen = Resumen()

    for evento in read_events(rutas.input_path):
        acumular_evento(agregados, evento, resumen)

    zonas_json = {zone_id: cerrar_zona(zona) for zone_id, zona in agregados.items()}
    resumen.zonas_con_datos = len(zonas_json)
    # Mismo criterio que people_count de cada fila: un parpadeo de
    # deteccion no es una persona (ver PRESENCIA_MINIMA_S).
    resumen.personas_totales = len(
        {tid for z in agregados.values() for tid in z.personas_reales()}
    )

    salida = {
        "contract_version": CONTRACT_VERSION,
        "stage": "metrics",
        "video_id": cfg.video_id,
        "zones": zonas_json,
    }

    rutas.output_path.parent.mkdir(parents=True, exist_ok=True)
    rutas.output_path.write_text(
        json.dumps(salida, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    _imprimir_resultado(resumen, zonas_json, rutas.output_path)
    return 0


def _imprimir_resultado(resumen: Resumen, zonas_json: dict, destino: Path) -> None:
    print()
    print("-" * 66)
    print(f"  Eventos leidos          {resumen.eventos_leidos}")
    print(f"  Zonas con datos         {resumen.zonas_con_datos}")
    print(f"  Personas distintas      {resumen.personas_totales}")
    if resumen.eventos_sin_zona:
        print(f"  Eventos sin zona        {resumen.eventos_sin_zona}  "
              f"(gente en pasillos, entre gondolas: no generan fila de metrics)")
    print("-" * 66)
    for zone_id, agregado in zonas_json.items():
        print(f"  [{zone_id}]")
        print(f"      personas={agregado['people_count']}  "
              f"interacciones={agregado['interaction_count']}  "
              f"pick_up={agregado['pick_up_count']}  "
              f"put_back={agregado['put_back_count']}  "
              f"dwell_prom={agregado['average_dwell_time_s']}")
    print("-" * 66)
    print(f"  Metricas  {destino}")
    print()
    print("  Siguiente paso:  importar a PostgreSQL (Persona 7)")
    print("    cd backend && python importer.py --video-id <video_id>")
