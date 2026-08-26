"""Excepciones propias del proyecto.

Sirven para distinguir "el proyecto detecto un problema y sabe explicarlo" de
"algo se rompio en una libreria". Regla de oro: el mensaje debe decir QUE HACER,
no solo que fallo.

Solo existen las que se usan hoy. Cuando la Fase 3 cargue el modelo YOLO, se
anadira `ModelError`.
"""


class GondolaError(Exception):
    """Error base del proyecto. Captura esto para atrapar todo lo nuestro."""


class ConfigError(GondolaError):
    """La configuracion (.env) esta incompleta o fuera de rango."""


class ContractError(GondolaError):
    """Un evento no cumple el contrato de datos o no se pudo leer."""


class PipelineError(GondolaError):
    """La cadena de etapas se pidio mal: etapa inexistente o rutas invalidas."""


class MissingInputError(PipelineError):
    """Falta un requisito para correr la etapa: el video o el archivo anterior.

    Se trata aparte del resto porque NO es un fallo del programa: es que todavia
    no toca. La CLI la traduce al codigo de salida 2, distinto del 1 de error.
    """
