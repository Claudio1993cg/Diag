"""Paquete modular para la diagramación optimizada de buses y conductores."""

from diagramador_optimizado.core.domain.logistica import GestorDeLogistica
from .main import main

__all__ = ["GestorDeLogistica", "main"]
__version__ = "1.0.0"

