"""Tests de la agregacion por zona (gondola/stages/metrics.py, Persona 6).

Aritmetica pura sobre eventos construidos a mano: corre con solo
`pip install -r requirements-dev.txt`, sin YOLO, sin OpenCV y sin base de
datos. Lo que se protege aqui son los tres errores que este modulo existe
para no cometer -contar filas en vez de personas, promediar un dwell_time
acumulado, y contar como cliente una deteccion que parpadeo-.
"""

import pytest

from gondola.contract import BBox, Detection, Event, InteractionEvent
from gondola.stages.metrics import (
    PRESENCIA_MINIMA_S,
    Resumen,
    _AgregadoZona,
    acumular_evento,
    cerrar_zona,
)


def evento(timestamp, track_id=1, zone_id="gondola_A", segment=None,
           dwell=None, interaccion=None) -> Event:
    """Un evento como los que llegan a `metrics`: ya con zona e interaccion."""
    e = Event(
        video_id="video_001",
        frame=int(timestamp * 30),
        timestamp=timestamp,
        track_id=track_id,
        detection=Detection(confidence=0.9, bbox=BBox(x=10, y=10, width=40, height=100)),
    )
    e.zone.zone_id = zone_id
    e.zone.segment = segment
    e.metrics.dwell_time = dwell
    e.interaction.event = interaccion
    return e


def acumular(eventos) -> dict[str, _AgregadoZona]:
    agregados: dict[str, _AgregadoZona] = {}
    resumen = Resumen()
    for e in eventos:
        acumular_evento(agregados, e, resumen)
    return agregados


# --------------------------------------------------------------------------
# El error clasico: contar filas en vez de personas
# --------------------------------------------------------------------------

def test_people_count_cuenta_personas_no_filas():
    """300 eventos de UNA persona son una persona, no 300."""
    agregados = acumular(
        evento(t / 30, track_id=1, dwell=t / 30) for t in range(300)
    )
    assert cerrar_zona(agregados["gondola_A"])["people_count"] == 1


def test_people_count_cuenta_track_ids_distintos():
    eventos = [evento(t / 10, track_id=tid, dwell=t / 10)
               for tid in (1, 2, 3) for t in range(30)]
    assert cerrar_zona(acumular(eventos)["gondola_A"])["people_count"] == 3


# --------------------------------------------------------------------------
# Presencia minima: un parpadeo de deteccion no es un cliente
# --------------------------------------------------------------------------

def test_una_silueta_de_un_suspiro_no_cuenta_como_persona():
    """El caso medido en video_004: tracks de 1 o 2 frames inflaban el conteo."""
    largo = [evento(t / 10, track_id=1, dwell=t / 10) for t in range(60)]  # 5.9s
    parpadeo = [evento(2.0, track_id=99), evento(2.03, track_id=99)]       # 0.03s
    fila = cerrar_zona(acumular(largo + parpadeo)["gondola_A"])
    assert fila["people_count"] == 1


def test_el_umbral_es_inclusivo_y_esta_donde_dice_la_constante():
    """Justo en PRESENCIA_MINIMA_S ya cuenta; un pelo por debajo, no."""
    justo = [evento(0.0, track_id=1), evento(PRESENCIA_MINIMA_S, track_id=1)]
    corto = [evento(0.0, track_id=2), evento(PRESENCIA_MINIMA_S - 0.01, track_id=2)]
    assert cerrar_zona(acumular(justo)["gondola_A"])["people_count"] == 1
    assert cerrar_zona(acumular(corto)["gondola_A"])["people_count"] == 0


def test_un_track_que_solo_aparece_una_vez_no_cuenta():
    assert cerrar_zona(acumular([evento(4.2, track_id=7)])["gondola_A"])["people_count"] == 0


# --------------------------------------------------------------------------
# Permanencia: promedio por PERSONA, y sobre las mismas personas que se cuentan
# --------------------------------------------------------------------------

def test_dwell_promedia_el_maximo_de_cada_persona_no_todas_las_filas():
    """dwell_time es acumulado: promediar las filas sesga hacia la mitad."""
    a = [evento(t / 10, track_id=1, dwell=t / 10) for t in range(101)]  # max 10.0
    b = [evento(t / 10, track_id=2, dwell=t / 10) for t in range(21)]   # max 2.0
    assert cerrar_zona(acumular(a + b)["gondola_A"])["average_dwell_time_s"] == 6.0


def test_el_promedio_de_permanencia_ignora_a_quien_no_conto_como_persona():
    """Las dos cifras de la misma fila tienen que hablar de la misma gente."""
    real = [evento(t / 10, track_id=1, dwell=t / 10) for t in range(101)]  # max 10.0
    parpadeo = [evento(3.0, track_id=9, dwell=0.0)]
    fila = cerrar_zona(acumular(real + parpadeo)["gondola_A"])
    assert fila["people_count"] == 1
    assert fila["average_dwell_time_s"] == 10.0


def test_sin_nadie_las_tasas_son_none_y_no_cero():
    """None significa 'no hay base para calcularlo'; 0.0 mentiria."""
    fila = cerrar_zona(_AgregadoZona())
    assert fila["people_count"] == 0
    assert fila["interaction_rate"] is None
    assert fila["conversion_rate"] is None
    assert fila["average_dwell_time_s"] is None


# --------------------------------------------------------------------------
# Tasas y filas de estante
# --------------------------------------------------------------------------

def test_las_tasas_nunca_pasan_de_uno():
    """schema.sql tiene un CHECK (... BETWEEN 0 AND 1): recortar aqui evita
    que el INSERT de la Persona 7 reviente por una tasa de 1.3."""
    persona = [evento(t / 10, track_id=1, dwell=t / 10) for t in range(30)]
    tomas = [evento(1.0 + i / 10, track_id=1, interaccion=InteractionEvent.PICK_UP)
             for i in range(5)]
    fila = cerrar_zona(acumular(persona + tomas)["gondola_A"])
    assert fila["conversion_rate"] == 1.0


def test_un_evento_con_estante_suma_a_la_gondola_y_al_estante():
    eventos = [evento(t / 10, track_id=1, segment="estante_2", dwell=t / 10)
               for t in range(30)]
    agregados = acumular(eventos)
    assert set(agregados) == {"gondola_A", "gondola_A:estante_2"}
    assert cerrar_zona(agregados["gondola_A"])["people_count"] == 1
    assert cerrar_zona(agregados["gondola_A:estante_2"])["people_count"] == 1


def test_los_eventos_sin_zona_no_generan_fila():
    agregados: dict[str, _AgregadoZona] = {}
    resumen = Resumen()
    for t in range(30):
        acumular_evento(agregados, evento(t / 10, zone_id=None), resumen)
    assert agregados == {}
    assert resumen.eventos_sin_zona == 30


# --------------------------------------------------------------------------
# Las tasas son proporciones de PERSONAS, no de eventos
# --------------------------------------------------------------------------

def test_interaction_rate_cuenta_personas_que_interactuaron_no_interacciones():
    """Caso real (video_005): 19 interacciones entre 8 personas daba 2.4, que
    recortado a 1.0 mostraba "100%" y se leia como "todo el mundo interactuo"."""
    a = [evento(t / 10, track_id=1, dwell=t / 10) for t in range(30)]
    b = [evento(t / 10, track_id=2, dwell=t / 10) for t in range(30)]
    # SOLO la persona 1 interactua, pero lo hace cuatro veces.
    toques = [evento(1.0 + i / 10, track_id=1, interaccion=InteractionEvent.APPROACH)
              for i in range(4)]
    fila = cerrar_zona(acumular(a + b + toques)["gondola_A"])
    assert fila["people_count"] == 2
    assert fila["interaction_count"] == 4
    assert fila["interaction_rate"] == 0.5      # 1 de 2 personas, no 4/2=2 recortado a 1


def test_conversion_rate_cuenta_personas_que_tomaron_algo():
    a = [evento(t / 10, track_id=1, dwell=t / 10) for t in range(30)]
    b = [evento(t / 10, track_id=2, dwell=t / 10) for t in range(30)]
    tomas = [evento(1.0 + i / 10, track_id=1, interaccion=InteractionEvent.PICK_UP)
             for i in range(3)]
    fila = cerrar_zona(acumular(a + b + tomas)["gondola_A"])
    assert fila["pick_up_count"] == 3
    assert fila["conversion_rate"] == 0.5       # 1 de 2 personas tomo algo


def test_ninguna_tasa_necesita_recorte_con_datos_normales():
    """Si una tasa llega a 1.0 tiene que ser porque de verdad fue todo el
    mundo, no porque se recorto un numero que se paso."""
    personas = [evento(t / 10, track_id=tid, dwell=t / 10)
                for tid in (1, 2, 3) for t in range(30)]
    toques = [evento(1.0 + i / 10, track_id=tid, interaccion=InteractionEvent.PICK_UP)
              for tid in (1, 2, 3) for i in range(5)]
    fila = cerrar_zona(acumular(personas + toques)["gondola_A"])
    assert fila["interaction_rate"] == 1.0      # las 3 personas, sin recortar
    assert fila["conversion_rate"] == 1.0


def test_un_parpadeo_que_interactua_no_infla_la_tasa():
    """Quien no cuenta como persona tampoco cuenta en el numerador: si no,
    la tasa podria pasar de 1 por la puerta de atras."""
    real = [evento(t / 10, track_id=1, dwell=t / 10) for t in range(30)]
    parpadeo = [evento(2.0, track_id=99, interaccion=InteractionEvent.PICK_UP)]
    fila = cerrar_zona(acumular(real + parpadeo)["gondola_A"])
    assert fila["people_count"] == 1
    assert fila["conversion_rate"] == 0.0       # la unica persona real no tomo nada
