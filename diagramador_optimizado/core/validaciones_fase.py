"""
Validaciones OBLIGATORIAS por fase. REGLA DURA: no se negocian.
Si alguna falla, se lanza ValueError para detener el flujo.

- Fase 1: Ningún bus puede tener viajes solapados (mismo bus, mismo tiempo).
- Fase 2/3: Ningún conductor puede tener dos viajes solapados en el mismo bus.
- Eventos: Ningún conductor+bus puede tener dos Comerciales solapados.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _inicio_fin_viaje(v: Dict) -> Tuple[int, int]:
    """Retorna (inicio, fin) en minutos."""
    ini = v.get("inicio", 0) or 0
    fin = v.get("fin", ini) or ini
    return int(ini), int(fin)


def validar_fase1_sin_solapamiento_bloques(bloques_bus: List[List[Dict[str, Any]]]) -> None:
    """
    REGLA DURA: En cada bloque (bus), ningún par de viajes puede solaparse.
    Lanza ValueError si se detecta solapamiento.
    """
    for bus_idx, bloque in enumerate(bloques_bus):
        if not bloque:
            continue
        ordenados = sorted(bloque, key=lambda x: (_inicio_fin_viaje(x)[0], _inicio_fin_viaje(x)[1]))
        for i in range(1, len(ordenados)):
            _, fin_ant = _inicio_fin_viaje(ordenados[i - 1])
            ini_act, fin_act = _inicio_fin_viaje(ordenados[i])
            if ini_act < fin_ant:
                vid_ant = ordenados[i - 1].get("id") or ordenados[i - 1].get("_tmp_id", "?")
                vid_act = ordenados[i].get("id") or ordenados[i].get("_tmp_id", "?")
                raise ValueError(
                    f"[FASE 1 - REGLA DURA] Bus {bus_idx + 1}: solapamiento detectado. "
                    f"Viaje {vid_ant} termina {fin_ant} min, viaje {vid_act} empieza {ini_act} min (diff={ini_act - fin_ant}). "
                    f"Un bus no puede hacer dos viajes simultáneamente."
                )


def validar_fase2_sin_solapamiento_turnos(
    turnos: List[Dict[str, Any]],
    mapa_viajes: Dict[Any, Dict[str, Any]],
) -> None:
    """
    REGLA DURA: En cada turno, para cada bus, ningún par de tareas (viajes) puede solaparse.
    Lanza ValueError si se detecta solapamiento.
    """
    for t_idx, turno in enumerate(turnos):
        tareas = turno.get("tareas_con_bus", [])
        por_bus: Dict[int, List[Tuple[Any, int, int]]] = {}
        todos: List[Tuple[Any, int, int, int]] = []  # (tid, ini, fin, bus)
        for tid, bus in tareas:
            v = mapa_viajes.get(tid) or mapa_viajes.get(str(tid))
            if not v:
                continue
            ini, fin = _inicio_fin_viaje(v)
            por_bus.setdefault(bus, []).append((tid, ini, fin))
            todos.append((tid, ini, fin, bus))
        for bus, lst in por_bus.items():
            lst.sort(key=lambda x: (x[1], x[2]))
            for i in range(1, len(lst)):
                _, _, fin_ant = lst[i - 1]
                tid_act, ini_act, _ = lst[i]
                if ini_act < fin_ant:
                    raise ValueError(
                        f"[FASE 2 - REGLA DURA] Turno {t_idx + 1} bus {bus + 1}: solapamiento detectado. "
                        f"Viaje anterior termina {fin_ant} min, viaje {tid_act} empieza {ini_act} min. "
                        f"Un conductor no puede tener dos viajes simultáneos en el mismo bus."
                    )

        # REGLA DURA adicional: dentro de un mismo turno, un conductor no puede
        # tener dos viajes solapados aunque sean de buses distintos.
        if len(todos) > 1:
            todos.sort(key=lambda x: (x[1], x[2]))  # ordenar por inicio, fin
            for i in range(1, len(todos)):
                tid_ant, ini_ant, fin_ant, bus_ant = todos[i - 1]
                tid_act, ini_act, fin_act, bus_act = todos[i]
                if ini_act < fin_ant:
                    raise ValueError(
                        f"[FASE 2 - REGLA DURA] Turno {t_idx + 1}: solapamiento global detectado entre buses."
                        f" Viaje {tid_ant} (bus {bus_ant + 1}) termina {fin_ant} min,"
                        f" viaje {tid_act} (bus {bus_act + 1}) empieza {ini_act} min (diff={ini_act - fin_ant}). "
                        f"Un conductor no puede operar dos viajes simultáneos en buses distintos."
                    )


def validar_fase3_sin_solapamiento_turnos(
    turnos: List[Dict[str, Any]],
    mapa_viajes: Dict[Any, Dict[str, Any]],
) -> None:
    """
    REGLA DURA: Igual que Fase 2. Los turnos unidos no pueden tener solapamientos.
    Lanza ValueError si se detecta solapamiento.
    """
    validar_fase2_sin_solapamiento_turnos(turnos, mapa_viajes)


def _to_min(val: Any) -> int:
    """Convierte HH:MM o número a minutos."""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip()
    if not s:
        return 0
    try:
        if ":" in s:
            parts = s.split(":")
            return int(parts[0]) * 60 + int(parts[1]) if len(parts) > 1 else int(parts[0]) * 60
        return int(s)
    except Exception:
        return 0


def validar_eventos_sin_solapamiento_conductor_bus(eventos: List[Dict[str, Any]]) -> None:
    """
    REGLA DURA: Para cada (conductor, bus), ningún par de eventos COMERCIAL puede solaparse.
    Lanza ValueError si se detecta solapamiento.
    """
    por_clave: Dict[Tuple[Any, Any], List[Dict]] = {}
    for ev in eventos:
        if str(ev.get("evento", "")).strip().upper() != "COMERCIAL":
            continue
        c, b = ev.get("conductor"), ev.get("bus")
        if c is None or b is None or str(b) == "":
            continue
        try:
            bid = int(b)
        except (TypeError, ValueError):
            continue
        clave = (c, bid)
        por_clave.setdefault(clave, []).append(ev)
    for (c, bid), evs in por_clave.items():
        ordenados = sorted(evs, key=lambda x: (_to_min(x.get("inicio", 0)), _to_min(x.get("fin", 0))))
        for i in range(1, len(ordenados)):
            fin_ant = _to_min(ordenados[i - 1].get("fin", ordenados[i - 1].get("inicio", 0)))
            ini_act = _to_min(ordenados[i].get("inicio", 0))
            if ini_act < fin_ant:
                raise ValueError(
                    f"[EVENTOS - REGLA DURA] Conductor {c} bus {bid}: solapamiento Comercial-Comercial. "
                    f"Anterior termina {fin_ant} min, siguiente empieza {ini_act} min (diff={ini_act - fin_ant}). "
                    f"Un conductor no puede operar dos Comerciales simultáneos en el mismo bus."
                )
