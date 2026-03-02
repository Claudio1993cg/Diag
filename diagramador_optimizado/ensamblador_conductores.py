"""
Ensamblador de conductores: construye eventos_conductores y eventos_completos.
Parte del motor (Fase 2/3). Recibe turnos + eventos_bus, produce eventos con conductor asignado.
Valida con reglas, asigna tipos de eventos, determina trazabilidad.
La exportación solo escribe; este módulo realiza la asignación y merge.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from diagramador_optimizado.core.domain.logistica import GestorDeLogistica


def preparar_eventos_para_excel(
    eventos_bus: List[List[Dict[str, Any]]],
    turnos_seleccionados: List[Dict[str, Any]],
    viajes_comerciales: List[Dict[str, Any]],
    metadata_tareas: Dict[Any, Dict[str, Any]],
    gestor: "GestorDeLogistica",
    verbose: bool = False,
) -> Tuple[List[List[Dict[str, Any]]], List[Dict[str, Any]], Dict[int, Optional[str]]]:
    """
    Construye eventos_conductores y eventos_completos (merge con trazabilidad).
    Lógica del motor: asigna conductores, valida reglas, determina tipos de eventos.
    Returns: (eventos_bus_normalizados, eventos_completos, bus_tipo_map)
    """
    # Importar la función de ensamblado desde el módulo de builders
    from diagramador_optimizado.core.builders.eventos_conductor import ensamblar_eventos_conductores
    
    # Ensamblar eventos de conductores usando la lógica del motor
    eventos_cond = ensamblar_eventos_conductores(
        turnos_seleccionados, eventos_bus, viajes_comerciales, metadata_tareas, gestor, verbose
    )
    
    # Construir mapa de tipos de bus (bus_idx -> tipo_bus)
    bus_tipo_map: Dict[int, Optional[str]] = {}
    for bus_idx, eventos in enumerate(eventos_bus):
        if eventos:
            # Buscar el primer evento comercial para determinar el tipo de bus
            for evento in eventos:
                tipo_bus = evento.get("tipo_bus")
                if tipo_bus:
                    bus_tipo_map[bus_idx] = tipo_bus
                    break
            # Si no se encontró tipo_bus en eventos, usar None
            if bus_idx not in bus_tipo_map:
                bus_tipo_map[bus_idx] = None
    
    return eventos_bus, eventos_cond, bus_tipo_map
