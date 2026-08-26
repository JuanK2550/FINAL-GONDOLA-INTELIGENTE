"""Tests de la CLI (gondola/cli.py).

Se llama a `main(argv)` directamente en vez de lanzar un proceso: es mas rapido
y permite leer la salida con capsys. `main` devuelve el codigo de salida en vez
de llamar a sys.exit justamente para esto.
"""

import pytest

from gondola.cli import EXIT_ERROR, EXIT_FALTA_REQUISITO, EXIT_OK, main
from gondola.pipeline import STAGE_NAMES, STAGES

# 'detect' ya esta implementada (Fase 3), asi que no imprime el texto de
# placeholder. Los tests de placeholder van sobre las que siguen pendientes.
ETAPAS_PENDIENTES = [e for e in STAGES if e.name != "detect"]


@pytest.fixture(autouse=True)
def entorno_limpio(tmp_path, monkeypatch):
    """Apunta la configuracion a carpetas temporales, sin tocar el .env real."""
    monkeypatch.setenv("VIDEO_ID", "video_001")
    monkeypatch.setenv("VIDEO_PATH", str(tmp_path / "videos" / "scapder.mp4"))
    monkeypatch.setenv("MODEL_PATH", str(tmp_path / "models" / "yolo11n.pt"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    return tmp_path


# --------------------------------------------------------------------------
# doctor: informa, nunca falla
# --------------------------------------------------------------------------

def test_doctor_devuelve_cero_aunque_falte_todo(capsys):
    """No hay video ni modelo y aun asi doctor sale con 0: su trabajo es informar."""
    assert main(["doctor"]) == EXIT_OK
    salida = capsys.readouterr().out
    assert "FALTA" in salida  # y lo reporta


def test_doctor_devuelve_cero_aunque_el_env_este_roto(monkeypatch, capsys):
    """Si doctor reventara con un .env malo seria inutil justo cuando mas se necesita."""
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "no_es_un_numero")
    assert main(["doctor"]) == EXIT_OK
    assert "CONFIDENCE_THRESHOLD" in capsys.readouterr().out


def test_doctor_muestra_las_cinco_etapas_y_sus_responsables(capsys):
    main(["doctor"])
    salida = capsys.readouterr().out
    for etapa in STAGES:
        assert etapa.name in salida
        assert etapa.owner in salida


def test_doctor_muestra_la_configuracion_resuelta(capsys):
    main(["doctor"])
    salida = capsys.readouterr().out
    assert "video_001" in salida
    assert "CONFIDENCE_THRESHOLD" in salida


# --------------------------------------------------------------------------
# Las etapas: placeholders
# --------------------------------------------------------------------------

@pytest.mark.parametrize("nombre", STAGE_NAMES)
def test_un_placeholder_sin_su_entrada_avisa_que_falta_el_requisito(nombre):
    """Falta la entrada: es un problema distinto de que falte el codigo, y sale con 2."""
    assert main([nombre]) == EXIT_FALTA_REQUISITO


@pytest.mark.parametrize("etapa", ETAPAS_PENDIENTES, ids=lambda e: e.name)
def test_un_placeholder_con_su_entrada_lista_devuelve_error(etapa, entorno_limpio):
    """Ya no falta nada salvo el codigo: sale con 1 para que `run` se detenga."""
    _crear_entrada_de(etapa, entorno_limpio)
    assert main([etapa.name]) == EXIT_ERROR


def _crear_entrada_de(etapa, tmp_path):
    """Crea el archivo (o el video falso) que esa etapa espera encontrar."""
    if etapa.input_suffix is None:
        entrada = tmp_path / "videos" / "scapder.mp4"
    else:
        entrada = tmp_path / "output" / f"video_001{etapa.input_suffix}"
    entrada.parent.mkdir(parents=True, exist_ok=True)
    entrada.write_text("", encoding="utf-8")


@pytest.mark.parametrize("etapa", ETAPAS_PENDIENTES, ids=lambda e: e.name)
def test_el_placeholder_dice_de_quien_es_y_que_archivos_usa(etapa, capsys):
    """Es lo primero que vera el companero al que le toque el modulo."""
    main([etapa.name])
    salida = capsys.readouterr().out
    assert etapa.owner in salida
    assert f"gondola/stages/{etapa.name}.py" in salida
    assert etapa.output_suffix in salida  # que archivo debe producir


def test_el_placeholder_de_track_dice_que_lee_lo_de_detect(capsys):
    main(["track"])
    salida = capsys.readouterr().out
    assert "video_001.detect.jsonl" in salida
    assert "video_001.track.jsonl" in salida
    assert "detect" in salida


def test_un_subcomando_inventado_no_arranca():
    """argparse rechaza el comando antes de tocar nada."""
    with pytest.raises(SystemExit):
        main(["deteccion"])


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------

def test_run_se_detiene_en_detect_si_no_hay_video(capsys):
    """Sin video, detect no puede arrancar: codigo 2 y se dice donde paro."""
    assert main(["run"]) == EXIT_FALTA_REQUISITO
    salida = capsys.readouterr().out
    assert "se detiene en 'detect'" in salida
    assert "[metrics]" not in salida  # no sigue con las siguientes


def test_run_dice_donde_paro_aunque_la_etapa_lance_una_excepcion(entorno_limpio, capsys):
    """Un video corrupto revienta dentro de detect; run debe seguir diciendo donde paro.

    Si el error se dejara subir hasta main(), el codigo de salida seria correcto
    pero se perderia el mensaje de "la cadena se detiene en X".
    """
    pytest.importorskip("cv2")
    video = entorno_limpio / "videos" / "scapder.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("esto no es un video", encoding="utf-8")

    assert main(["run"]) == EXIT_ERROR
    salida = capsys.readouterr().out
    assert "se detiene en 'detect'" in salida
    assert "[track]" not in salida


# --------------------------------------------------------------------------
# purge
# --------------------------------------------------------------------------

def test_purge_pide_confirmacion_y_no_borra_si_dices_que_no(entorno_limpio, monkeypatch, capsys):
    video = entorno_limpio / "videos" / "scapder.mp4"
    video.parent.mkdir(parents=True)
    video.write_text("contenido falso", encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert main(["purge"]) == EXIT_OK
    assert video.exists()
    assert "Cancelado" in capsys.readouterr().out


def test_purge_borra_cuando_confirmas(entorno_limpio, monkeypatch):
    video = entorno_limpio / "videos" / "scapder.mp4"
    video.parent.mkdir(parents=True)
    video.write_text("contenido falso", encoding="utf-8")
    salida = entorno_limpio / "output" / "video_001.detect.jsonl"
    salida.parent.mkdir(parents=True)
    salida.write_text("", encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda _: "si")
    assert main(["purge"]) == EXIT_OK
    assert not video.exists()
    assert not salida.exists()


def test_purge_no_borra_los_readme(entorno_limpio, monkeypatch):
    """Los README explican que va en cada carpeta: borrarlos deja la carpeta muda."""
    carpeta = entorno_limpio / "videos"
    carpeta.mkdir(parents=True)
    readme = carpeta / "README.md"
    readme.write_text("COLOCAR AQUI EL VIDEO", encoding="utf-8")
    (carpeta / "scapder.mp4").write_text("falso", encoding="utf-8")

    monkeypatch.setattr("builtins.input", lambda _: "si")
    main(["purge"])
    assert readme.exists()


def test_purge_con_yes_no_pregunta(entorno_limpio):
    video = entorno_limpio / "videos" / "scapder.mp4"
    video.parent.mkdir(parents=True)
    video.write_text("falso", encoding="utf-8")

    # Si intentara preguntar, input() reventaria: en los tests no hay teclado.
    assert main(["purge", "--yes"]) == EXIT_OK
    assert not video.exists()


def test_purge_sin_nada_que_borrar(capsys):
    assert main(["purge"]) == EXIT_OK
    assert "No hay nada que borrar" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Codigos de salida
# --------------------------------------------------------------------------

def test_los_tres_codigos_de_salida_son_distintos():
    """0 exito, 1 error, 2 falta un requisito. Un script que llame a la CLI los distingue."""
    assert len({EXIT_OK, EXIT_ERROR, EXIT_FALTA_REQUISITO}) == 3
