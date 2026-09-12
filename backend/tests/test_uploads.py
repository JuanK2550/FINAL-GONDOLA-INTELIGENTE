"""Tests de la subida de video (backend/uploads.py).

Estos NO necesitan PostgreSQL ni YOLO: prueban las barreras que actuan
ANTES de gastar recursos -las condiciones de uso, los trabajos que no
existen, y la validacion de las zonas dibujadas-. El prevuelo de verdad
(abrir el video, pasarle YOLO) no se prueba aqui a proposito: necesitaria
los 3 GB de PyTorch y un video real, y entonces los 7 companeros ya no
podrian correr `pytest` sin instalarlo todo.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import uploads  # noqa: E402
from api import app  # noqa: E402

cliente = TestClient(app)


def _archivo_falso() -> dict:
    """Un 'video' de cuatro bytes. Sirve porque estos tests nunca llegan a
    abrirlo: se rechazan antes, en las condiciones de uso."""
    return {"file": ("prueba.mp4", io.BytesIO(b"\x00\x00\x00\x00"), "video/mp4")}


@pytest.mark.parametrize(
    "terminos",
    [
        {"acepta_terminos": "false", "confirma_gondola": "true"},
        {"acepta_terminos": "true", "confirma_gondola": "false"},
        {"acepta_terminos": "false", "confirma_gondola": "false"},
    ],
)
def test_sin_aceptar_las_condiciones_no_se_sube(terminos):
    """Las casillas del navegador no son la unica barrera: el servidor las
    vuelve a exigir. Si no, bastaria con llamar al endpoint a mano."""
    respuesta = cliente.post("/uploads", files=_archivo_falso(), data=terminos)
    assert respuesta.status_code == 400
    assert "condiciones" in respuesta.json()["detail"].lower()


def test_el_video_rechazado_no_queda_en_disco():
    """Un video que no se acepta no debe dejar rastro: contiene personas
    reales y no se va a usar para nada."""
    antes = set(uploads.VIDEOS_DIR.glob("*.mp4")) if uploads.VIDEOS_DIR.exists() else set()
    cliente.post("/uploads", files=_archivo_falso(), data={"acepta_terminos": "false", "confirma_gondola": "true"})
    despues = set(uploads.VIDEOS_DIR.glob("*.mp4")) if uploads.VIDEOS_DIR.exists() else set()
    assert antes == despues


def test_un_trabajo_que_no_existe_da_404_con_instrucciones():
    respuesta = cliente.get("/uploads/no_existe_este_trabajo")
    assert respuesta.status_code == 404
    assert "vuelve a subir" in respuesta.json()["detail"].lower()


def test_no_se_aceptan_zonas_de_un_trabajo_que_no_espera_zonas():
    """Mandar zonas dos veces, o antes de que el prevuelo termine, tiene que
    dar un error claro y no volver a lanzar la cadena."""
    trabajo = uploads.Trabajo(job_id="t_estado", video_id="t_estado", nombre_original="x.mp4")
    trabajo.estado = "procesando"
    uploads._TRABAJOS[trabajo.job_id] = trabajo
    try:
        respuesta = cliente.post(
            "/uploads/t_estado/zones",
            json={"gondola_name": "G", "shelves": [{"name": "E1", "x": 0, "y": 0, "width": 10, "height": 10}]},
        )
        assert respuesta.status_code == 409
        assert "procesando" in respuesta.json()["detail"]
    finally:
        uploads._TRABAJOS.pop(trabajo.job_id, None)


def test_un_estante_fuera_del_frame_se_rechaza():
    """El rectangulo llega en pixeles del frame original; si se sale, el
    archivo de zonas quedaria invalido y `zones` fallaria mucho despues."""
    trabajo = uploads.Trabajo(job_id="t_fuera", video_id="t_fuera", nombre_original="x.mp4")
    trabajo.estado = "esperando_zonas"
    trabajo.detalles = {"width": 640, "height": 480}
    uploads._TRABAJOS[trabajo.job_id] = trabajo
    try:
        respuesta = cliente.post(
            "/uploads/t_fuera/zones",
            json={"gondola_name": "G", "shelves": [{"name": "Se sale", "x": 600, "y": 0, "width": 200, "height": 100}]},
        )
        assert respuesta.status_code == 422
        assert "se sale del frame" in respuesta.json()["detail"]
    finally:
        uploads._TRABAJOS.pop(trabajo.job_id, None)


def test_hacen_falta_estantes():
    """Sin ningun rectangulo no hay nada que medir por zona: se corta aqui,
    no despues de diez minutos de pipeline."""
    trabajo = uploads.Trabajo(job_id="t_vacio", video_id="t_vacio", nombre_original="x.mp4")
    trabajo.estado = "esperando_zonas"
    trabajo.detalles = {"width": 640, "height": 480}
    uploads._TRABAJOS[trabajo.job_id] = trabajo
    try:
        respuesta = cliente.post("/uploads/t_vacio/zones", json={"gondola_name": "G", "shelves": []})
        assert respuesta.status_code == 422
    finally:
        uploads._TRABAJOS.pop(trabajo.job_id, None)


@pytest.mark.parametrize(
    "nombre, esperado",
    [
        ("Video de la Tienda.mp4", "video_de_la_tienda"),
        # Path().stem se queda solo con el ultimo tramo y sin extension, asi
        # que la ruta entera desaparece: no hay forma de escribir fuera.
        ("../../etc/passwd", "passwd"),
        ("cámara_3.MP4", "camara_3"),
        ("!!!.mp4", "video"),
    ],
)
def test_el_nombre_del_archivo_se_sanea(nombre, esperado):
    """El nombre que manda el navegador acaba en un nombre de archivo, en la
    URL y en la base de datos. Subir '../../algo' no puede escribir fuera de
    data/videos/."""
    assert uploads._sanear(nombre) == esperado


# --------------------------------------------------------------------------
# CORS: quien puede escribir en esta API
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "origen, permitido",
    [
        ("null", True),                     # el dashboard abierto con file://
        ("http://localhost:8000", True),    # servido por HTTP en la misma maquina
        ("http://127.0.0.1:5500", True),    # cualquier puerto local
        ("https://sitio-malicioso.com", False),
        ("http://localhost.evil.com", False),  # el punto no es un separador valido
        ("null.evil.com", False),
    ],
)
def test_solo_los_origenes_locales_pueden_escribir(origen, permitido):
    """El navegador de alguien de la tienda esta DENTRO de la red local, asi
    que "esto no se expone a internet" no protege de una web cualquiera que
    dispare peticiones a 127.0.0.1. La lista de origenes es la barrera."""
    respuesta = cliente.options(
        "/uploads/x/zones",
        headers={
            "Origin": origen,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    permitido_por_la_api = respuesta.headers.get("access-control-allow-origin") == origen
    assert permitido_por_la_api is permitido


# --------------------------------------------------------------------------
# Las zonas de una subida son SUYAS: no le pisan las zonas a otro video
# --------------------------------------------------------------------------
# `zones.zone_id` es UNIQUE en toda la tabla (ver backend/database/schema.sql):
# dos videos de la misma gondola comparten su fila a proposito. Eso vale para
# videos de la misma tienda, no para una subida cualquiera: con un
# "gondola_A" fijo, CADA video subido reclamaba esa unica fila y le pisaba el
# nombre y la categoria a todos los demas -bug real: los seis clips del MERL
# pasaron a mostrar "Gondola subida" y "Estante 1/2/3" en vez de "Cereales" y
# "Snacks y pasabocas"-.


@pytest.fixture
def trabajo_esperando_zonas(tmp_path, monkeypatch):
    """Un trabajo listo para recibir zonas, con el disco redirigido a tmp y
    sin lanzar la cadena de verdad (tarda minutos y necesita PyTorch)."""
    monkeypatch.setattr(uploads, "ZONES_DIR", tmp_path / "zones")
    monkeypatch.setattr(uploads.threading, "Thread", lambda *a, **k: _HiloFalso())
    trabajo = uploads.Trabajo(
        job_id="t_zonas", video_id="subido_prueba_120000", nombre_original="x.mp4"
    )
    trabajo.estado = "esperando_zonas"
    trabajo.detalles = {"width": 640, "height": 480}
    uploads._TRABAJOS[trabajo.job_id] = trabajo
    yield trabajo, tmp_path / "zones"
    uploads._TRABAJOS.pop(trabajo.job_id, None)


class _HiloFalso:
    def start(self):
        return None


def _zonas_de_ejemplo(n=2):
    return {
        "gondola_name": "Gondola de la tienda",
        "shelves": [
            {"name": f"Estante {i}", "product_category": None,
             "x": 10 + i * 100, "y": 10, "width": 80, "height": 80}
            for i in range(1, n + 1)
        ],
    }


def test_la_zona_de_una_subida_lleva_su_video_id(trabajo_esperando_zonas):
    """Sin esto, dos subidas distintas (o una subida y los videos del MERL)
    pelean por la misma fila de `zones` y la ultima gana."""
    trabajo, zones_dir = trabajo_esperando_zonas
    respuesta = cliente.post(f"/uploads/{trabajo.job_id}/zones", json=_zonas_de_ejemplo())
    assert respuesta.status_code == 200

    documento = json.loads((zones_dir / f"{trabajo.video_id}.json").read_text(encoding="utf-8"))
    zone_id = documento["gondolas"][0]["zone_id"]
    assert zone_id == f"{trabajo.video_id}_gondola_A"
    assert zone_id != "gondola_A"          # el id generico que causaba el choque


def test_dos_subidas_distintas_no_comparten_zona(trabajo_esperando_zonas, tmp_path):
    """Dos tiendas distintas subidas desde el mismo dashboard tienen que
    acabar en filas de `zones` distintas."""
    trabajo, zones_dir = trabajo_esperando_zonas
    cliente.post(f"/uploads/{trabajo.job_id}/zones", json=_zonas_de_ejemplo())
    primero = json.loads((zones_dir / f"{trabajo.video_id}.json").read_text(encoding="utf-8"))

    otro = uploads.Trabajo(job_id="t_zonas_2", video_id="subido_otra_tienda_130000",
                           nombre_original="y.mp4")
    otro.estado = "esperando_zonas"
    otro.detalles = {"width": 640, "height": 480}
    uploads._TRABAJOS[otro.job_id] = otro
    try:
        cliente.post(f"/uploads/{otro.job_id}/zones", json=_zonas_de_ejemplo())
        segundo = json.loads((zones_dir / f"{otro.video_id}.json").read_text(encoding="utf-8"))
    finally:
        uploads._TRABAJOS.pop(otro.job_id, None)

    assert primero["gondolas"][0]["zone_id"] != segundo["gondolas"][0]["zone_id"]


def test_el_archivo_de_zonas_que_escribe_la_subida_lo_entiende_el_pipeline(
    trabajo_esperando_zonas,
):
    """Prueba de integracion entre las dos capas: lo que la Persona 7 escribe
    tiene que poder leerlo la Persona 4 sin traducciones por el medio. Si el
    formato se desincroniza, hoy nadie se entera hasta que la cadena lleva
    diez minutos corriendo y revienta en la etapa `zones`."""
    trabajo, zones_dir = trabajo_esperando_zonas
    cliente.post(f"/uploads/{trabajo.job_id}/zones", json=_zonas_de_ejemplo(3))

    AI = Path(__file__).resolve().parents[2] / "ai-service"
    if str(AI) not in sys.path:
        sys.path.insert(0, str(AI))
    from gondola.zones_config import load_zones_config

    config = load_zones_config(zones_dir / f"{trabajo.video_id}.json")
    assert config.frame_width == 640 and config.frame_height == 480
    assert len(list(config.shelves())) == 3
    for gondola, estante in config.shelves():
        assert gondola.zone_id.startswith(trabajo.video_id)
        assert estante.floor_zone.width > 0 and estante.floor_zone.height > 0


# --------------------------------------------------------------------------
# Barreras de seguridad
# --------------------------------------------------------------------------

@pytest.mark.parametrize("job_id", [
    "../../../etc/passwd",
    "..%2F..%2Fsecreto",
    "t_zonas/../../otro",
])
def test_un_job_id_con_rutas_no_llega_al_disco(job_id):
    """El job_id viaja en la URL y se usa para buscar en memoria; nunca debe
    servir para salir de las carpetas del proyecto."""
    respuesta = cliente.get(f"/uploads/{job_id}")
    assert respuesta.status_code in (404, 400)


def test_un_estante_de_area_cero_se_rechaza(trabajo_esperando_zonas):
    """Un rectangulo sin area no es una zona: ningun pie cae dentro y la
    fila de metrics saldria vacia sin que nadie sepa por que."""
    trabajo, _ = trabajo_esperando_zonas
    respuesta = cliente.post(
        f"/uploads/{trabajo.job_id}/zones",
        json={"gondola_name": "G",
              "shelves": [{"name": "Plano", "x": 10, "y": 10, "width": 0, "height": 50}]},
    )
    assert respuesta.status_code == 422


def test_no_se_aceptan_coordenadas_negativas(trabajo_esperando_zonas):
    trabajo, _ = trabajo_esperando_zonas
    respuesta = cliente.post(
        f"/uploads/{trabajo.job_id}/zones",
        json={"gondola_name": "G",
              "shelves": [{"name": "Fuera", "x": -50, "y": 10, "width": 40, "height": 40}]},
    )
    assert respuesta.status_code == 422


def test_hay_un_tope_de_estantes(trabajo_esperando_zonas):
    """Sin tope, alguien puede mandar mil rectangulos y dejar el archivo de
    zonas (y la tabla) llenos de filas que nadie dibujo a conciencia."""
    trabajo, _ = trabajo_esperando_zonas
    respuesta = cliente.post(f"/uploads/{trabajo.job_id}/zones", json=_zonas_de_ejemplo(20))
    assert respuesta.status_code == 422


def test_un_nombre_de_gondola_larguisimo_se_rechaza(trabajo_esperando_zonas):
    trabajo, _ = trabajo_esperando_zonas
    respuesta = cliente.post(
        f"/uploads/{trabajo.job_id}/zones",
        json={"gondola_name": "G" * 500,
              "shelves": [{"name": "E1", "x": 0, "y": 0, "width": 10, "height": 10}]},
    )
    assert respuesta.status_code == 422
