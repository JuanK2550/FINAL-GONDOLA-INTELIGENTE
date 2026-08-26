# Gondola Inteligente

Percepcion visual soberana para optimizar el espacio en retail. Reto de Scapder.

A partir del video de una tienda, el sistema cuantifica la dinamica de los
clientes alrededor de las gondolas (flujo, permanencia e interaccion con
productos) para recomendar mejoras de planograma. **No identifica personas, no
reconoce rostros, no infiere emociones y no crea perfiles biometricos.**

## Estado: Fase 2 de 5 (esqueleto ejecutable)

Las etapas todavia no analizan nada, pero la cadena ya existe y se puede
recorrer. Lo que hay hoy:

- El **contrato de datos** (`ai-service/gondola/contract.py`), documentado en
  [`docs/data-contract.md`](docs/data-contract.md).
- El **registro de etapas** (`ai-service/gondola/pipeline.py`): el codigo decide
  los nombres de archivo, no las personas.
- La **CLI** (`python -m gondola`), con `doctor`, `run` y `purge`.
- Configuracion centralizada y tests unitarios en verde.

## Empezar

```bash
cp .env.example .env
pip install -r requirements-dev.txt
pytest

cd ai-service
python -m gondola doctor
```

`doctor` es lo primero que debes correr si algo no funciona: dice que hay
instalado, que archivos faltan y en que va la cadena.

Para trabajar con el video: dejalo en `data/videos/` (ver el README de esa
carpeta) y ajusta `VIDEO_PATH` en tu `.env`.

## Comandos

```
python -m gondola doctor     Diagnostico del entorno
python -m gondola detect     Deteccion de personas      (Persona 2)
python -m gondola track      Seguimiento                (Persona 3)
python -m gondola zones      Zonas y permanencia        (Persona 4)
python -m gondola interact   Interaccion con productos  (Persona 5)
python -m gondola metrics    Metricas finales           (Persona 6)
python -m gondola run        La cadena completa
python -m gondola purge      Borra videos y salidas de data/
```

## Documentacion

- [Contrato de datos](docs/data-contract.md) — **empieza por aqui**.
- [CLAUDE.md](CLAUDE.md) — reglas y convenciones del proyecto.

> README completo (arquitectura, uso, resultados): Fase 5.
