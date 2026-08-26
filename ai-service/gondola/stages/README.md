# stages/

Una etapa del pipeline por archivo. Cada etapa recibe eventos, **rellena sus
propios campos del contrato** y los devuelve. Ninguna etapa inventa un formato
nuevo: todas hablan el mismo `Event` de `gondola/contract.py`.

| Archivo (futuro) | Responsable | Campos que rellena |
|---|---|---|
| `detect.py`   | Persona 2 | `detection` |
| `track.py`    | Persona 3 | `track_id` |
| `zones.py`    | Persona 4 | `zone`, `metrics.dwell_time` |
| `interact.py` | Persona 5 | `interaction` |
| `metrics.py`  | Persona 6 | agregados finales |

Vacía en la Fase 1. Se llena a partir de la Fase 3.
