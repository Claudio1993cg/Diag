"""
Ensamblador de Conductores V2.0
=================================
Toma los eventos_bus (Fase 1) y los turnos de conductores (Fase 2/3)
y genera la lista de eventos completa de cada conductor.

Principio nodo-a-nodo (sin teletransportaciones):
  InS(depot) → [Desp(depot→relay)?] → eventos del bus → [Desp(relay→depot)?] → FnS(depot)

REGLAS ESTRICTAS:
  1. NO crear Vacio, Parada, Comercial ni Recarga (solo asignar conductor a los existentes).
  2. Crear InS, Desplazamiento y FnS.
  3. InS y FnS siempre en el depósito.
  4. Desplazamiento solo si hay desplazamiento habilitado en configuración.
  5. El último evento de un conductor debe terminar en depósito (directo o via desplazamiento).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from diagramador_optimizado.core.engines.fase2_conductores import _es_deposito, _es_relevo_valido
from diagramador_optimizado.core.validaciones_fase import validar_eventos_sin_solapamiento_conductor_bus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nodos_iguales(a: str, b: str) -> bool:
    """Comparación exacta de nodos (insensible a mayúsculas/espacios).
    No usa substring para evitar falsos positivos entre nodos distintos como
    'PIE ANDINO' y 'DEPOSITO PIE ANDINO'."""
    if not a or not b:
        return False
    return a.strip().upper() == b.strip().upper()


def _es_deposito_nodo(nodo: str, gestor: Any) -> bool:
    """True si el nodo es un depósito configurado."""
    return _es_deposito(nodo, gestor.deposito_base)


def _overlap_con_tramos(
    ini_ev: int, fin_ev: int, tramos: List[Tuple[int, int]]
) -> int:
    """Retorna minutos de solapamiento entre [ini_ev, fin_ev] y los tramos de trabajo."""
    total = 0
    for a, b in tramos:
        solap = min(fin_ev, b) - max(ini_ev, a)
        if solap > 0:
            total += solap
    return total


# ---------------------------------------------------------------------------
# Asignación de conductor a eventos de bus
# ---------------------------------------------------------------------------

def _asignar_conductor_a_eventos(
    eventos_bus: List[List[Dict[str, Any]]],
    turnos: List[Dict[str, Any]],
    ids_comerciales: Set[Any],
    mapa_viajes: Dict[Any, Dict],
    gestor: Any,
) -> List[Dict[str, Any]]:
    """
    Recorre todos los eventos de bus y asigna el conductor correcto.
    Retorna lista plana de eventos con campo 'conductor'.

    Regla de asignación:
      - Comercial: conductor que tiene ese viaje_id en su turno
      - Vacio/Parada/Recarga: conductor cuyo rango de trabajo cubre ese intervalo en ese bus
    """
    deposito = gestor.deposito_base
    ids_com_str = {str(v) for v in ids_comerciales}

    # Mapa viaje_id → conductor_id (para el caso sin colisión de viaje_id)
    viaje_a_conductor: Dict[Tuple[int, Any], int] = {}
    for c_id, turno in enumerate(turnos, start=1):
        for tid, bus in turno.get("tareas_con_bus", []):
            if tid in ids_comerciales or str(tid) in ids_com_str:
                viaje_a_conductor[(bus, tid)] = c_id
                viaje_a_conductor[(bus, str(tid))] = c_id

    # Lista de TODOS los conductores que tienen un viaje_id dado en un bus dado.
    # Necesario para rutas circulares donde el mismo viaje_id se repite en múltiples ciclos.
    viaje_a_conductores_lista: Dict[Tuple[int, Any], List[int]] = {}
    for c_id, turno in enumerate(turnos, start=1):
        for tid, bus in turno.get("tareas_con_bus", []):
            if tid in ids_comerciales or str(tid) in ids_com_str:
                key = (bus, tid)
                viaje_a_conductores_lista.setdefault(key, [])
                if c_id not in viaje_a_conductores_lista[key]:
                    viaje_a_conductores_lista[key].append(c_id)
                key_str = (bus, str(tid))
                viaje_a_conductores_lista.setdefault(key_str, [])
                if c_id not in viaje_a_conductores_lista[key_str]:
                    viaje_a_conductores_lista[key_str].append(c_id)

    # TRAMOS reales de trabajo por (conductor, bus): lista de (inicio, fin) ordenados.
    # Evita que rango único (min, max) incluya huecos y asigne eventos en esos huecos al conductor equivocado.
    tramos_cond_bus: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for c_id, turno in enumerate(turnos, start=1):
        por_bus: Dict[int, List[Tuple[int, int]]] = {}
        for tid, bus in turno.get("tareas_con_bus", []):
            v = mapa_viajes.get(tid) or mapa_viajes.get(str(tid))
            if not v:
                continue
            ini, fin = v.get("inicio", 0), v.get("fin", 0)
            por_bus.setdefault(bus, []).append((ini, fin))
        for bus, intervalos in por_bus.items():
            # Ordenar y fusionar solapados
            intervalos.sort(key=lambda x: x[0])
            fusionados: List[Tuple[int, int]] = []
            for a, b in intervalos:
                if fusionados and a <= fusionados[-1][1] + 5:
                    fusionados[-1] = (fusionados[-1][0], max(fusionados[-1][1], b))
                else:
                    fusionados.append((a, b))
            tramos_cond_bus[(c_id, bus)] = fusionados

    # Rango legacy (min, max) para compatibilidad donde se usa
    rango_cond_bus: Dict[Tuple[int, int], Tuple[int, int]] = {}
    for (c_id, bus), tramos in tramos_cond_bus.items():
        if tramos:
            rango_cond_bus[(c_id, bus)] = (min(t[0] for t in tramos), max(t[1] for t in tramos))

    # Último fin de trabajo por (conductor, bus) para saber cuándo el conductor deja el bus
    ultimo_fin_cond_bus: Dict[Tuple[int, int], int] = {}
    for c_id, turno in enumerate(turnos, start=1):
        tareas_com = [(tid, bus) for tid, bus in turno.get("tareas_con_bus", [])
                      if tid in ids_comerciales or str(tid) in ids_com_str]
        if not tareas_com:
            continue
        last_tid, last_bus = tareas_com[-1]
        last_v = mapa_viajes.get(last_tid) or mapa_viajes.get(str(last_tid))
        if last_v:
            dest = (last_v.get("destino") or "").strip()
            if dest and not _es_deposito_nodo(dest, gestor):
                # Conductor deja el bus en relay: registrar cuándo
                ultimo_fin_cond_bus[(c_id, last_bus)] = last_v.get("fin", 0)

    resultado: List[Dict[str, Any]] = []
    # Comerciales ya asignados por (conductor, bus): lista de (inicio, fin) para detectar solapamientos
    comerciales_por_cond_bus: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

    for bus_idx, lista_ev in enumerate(eventos_bus):
        # Ordenar eventos por (inicio, fin) para procesar Comerciales en orden cronológico
        # (importante para detectar solapamientos correctamente)
        lista_ev_ordenada = sorted(lista_ev, key=lambda e: (e.get("inicio", 0), e.get("fin", 0)))
        for ev in lista_ev_ordenada:
            tipo = str(ev.get("evento", "")).strip().upper()
            if tipo in ("INS", "FNS"):
                continue
            if tipo not in ("VACIO", "PARADA", "COMERCIAL", "RECARGA"):
                continue

            ev_out = {**ev, "bus": bus_idx + 1, "conductor": None}

            if tipo == "COMERCIAL":
                vid = ev.get("viaje_id")
                if vid is not None:
                    c = viaje_a_conductor.get((bus_idx, vid)) or viaje_a_conductor.get((bus_idx, str(vid)))
                    ini_ev_c = ev.get("inicio", 0)
                    fin_ev_c = ev.get("fin", ini_ev_c)
                    candidatos = (viaje_a_conductores_lista.get((bus_idx, vid))
                                  or viaje_a_conductores_lista.get((bus_idx, str(vid))) or [])
                    # Priorizar candidatos con este viaje_id, exigir solapamiento real con tramos
                    # y que NO solape con Comerciales ya asignados a ese conductor en este bus
                    mejor_c = None
                    mejor_overlap = -1
                    for cand in (candidatos if candidatos else [c] if c else []):
                        if cand is None:
                            continue
                        tramos_c = tramos_cond_bus.get((cand, bus_idx), [])
                        overlap = _overlap_con_tramos(ini_ev_c, fin_ev_c, tramos_c)
                        if overlap <= 0:
                            continue
                        ya_asignados = comerciales_por_cond_bus.get((cand, bus_idx), [])
                        if any(min(fin_ev_c, b) > max(ini_ev_c, a) for a, b in ya_asignados):
                            continue
                        if overlap > mejor_overlap:
                            mejor_overlap = overlap
                            mejor_c = cand
                    if mejor_overlap <= 0:
                        for (cid, b), tramos in tramos_cond_bus.items():
                            if b != bus_idx:
                                continue
                            overlap = _overlap_con_tramos(ini_ev_c, fin_ev_c, tramos)
                            if overlap <= 0:
                                continue
                            ya_asignados = comerciales_por_cond_bus.get((cid, bus_idx), [])
                            if any(min(fin_ev_c, y) > max(ini_ev_c, x) for x, y in ya_asignados):
                                continue
                            if overlap > mejor_overlap:
                                mejor_overlap = overlap
                                mejor_c = cid
                    if mejor_c is not None and mejor_overlap > 0:
                        c = mejor_c
                        comerciales_por_cond_bus.setdefault((c, bus_idx), []).append((ini_ev_c, fin_ev_c))
                    elif mejor_overlap <= 0 and c is not None:
                        ya = comerciales_por_cond_bus.get((c, bus_idx), [])
                        if not any(min(fin_ev_c, y) > max(ini_ev_c, x) for x, y in ya):
                            c = c
                            comerciales_por_cond_bus.setdefault((c, bus_idx), []).append((ini_ev_c, fin_ev_c))
                        else:
                            c = None
                    ev_out["conductor"] = c
                ev_out["evento"] = "Comercial"
                resultado.append(ev_out)
                continue

            ini_ev = ev.get("inicio", 0)
            fin_ev = ev.get("fin", ini_ev)

            if tipo == "VACIO":
                orig = (ev.get("origen") or "").strip()
                dest = (ev.get("destino") or "").strip()

                # Vacio nodo→depot: conductor cuyo último comercial terminó en ese nodo
                if orig and not _es_deposito_nodo(orig, gestor) and dest and _es_deposito_nodo(dest, gestor):
                    mejor_c, mejor_fin = None, -1
                    for (b, vid), cid in viaje_a_conductor.items():
                        if b != bus_idx:
                            continue
                        v = mapa_viajes.get(vid) or mapa_viajes.get(str(vid))
                        if not v:
                            continue
                        if not _nodos_iguales(v.get("destino", ""), orig):
                            continue
                        fin_v = v.get("fin", 0)
                        if abs(ini_ev - fin_v) <= 120 and fin_v > mejor_fin:
                            mejor_fin, mejor_c = fin_v, cid
                    ev_out["conductor"] = mejor_c
                    ev_out["evento"] = "Vacio"
                    resultado.append(ev_out)
                    continue

                # Vacio depot→nodo: conductor cuyo primer comercial empieza en ese nodo
                if orig and _es_deposito_nodo(orig, gestor) and dest and not _es_deposito_nodo(dest, gestor):
                    mejor_c, mejor_ini = None, float("inf")
                        for (b, vid), cid in viaje_a_conductor.items():
                            if b != bus_idx:
                                continue
                        v = mapa_viajes.get(vid) or mapa_viajes.get(str(vid))
                        if not v:
                            continue
                        if not _nodos_iguales(v.get("origen", ""), dest):
                                continue
                        ini_v = v.get("inicio", 9999)
                        # El Vacio termina (fin_ev) antes de que empiece el viaje
                        if abs(fin_ev - ini_v) <= 30 and ini_v < mejor_ini:
                            mejor_ini, mejor_c = ini_v, cid
                    # Validar que el fin del Vacio esté dentro del rango del conductor.
                    # Se usa fin_ev (llegada al nodo) y NO ini_ev (salida del depósito),
                    # porque el bus puede salir del depósito mucho antes de que el conductor
                    # inicie su primer viaje comercial.
                    if mejor_c is not None:
                        rango = rango_cond_bus.get((mejor_c, bus_idx))
                        if rango and not (rango[0] - 90 <= fin_ev <= rango[1] + 90):
                            mejor_c = None
                    ev_out["conductor"] = mejor_c
                    ev_out["evento"] = "Vacio"
                    resultado.append(ev_out)
                                continue

                # Vacio nodo→nodo (tránsito del bus): asignar al conductor activo en ese instante
                mejor_c = _conductor_activo(bus_idx, ini_ev, fin_ev, tramos_cond_bus, ultimo_fin_cond_bus)
                ev_out["conductor"] = mejor_c
                ev_out["evento"] = "Vacio"
                resultado.append(ev_out)
                continue

            if tipo in ("PARADA", "RECARGA"):
                mejor_c = _conductor_activo(bus_idx, ini_ev, fin_ev, tramos_cond_bus, ultimo_fin_cond_bus)
                ev_out["conductor"] = mejor_c
                ev_out["evento"] = tipo.capitalize()
                resultado.append(ev_out)
                continue

    return resultado


def _conductor_activo(
    bus_idx: int,
    ini_ev: int,
    fin_ev: int,
    tramos_cond_bus: Dict[Tuple[int, int], List[Tuple[int, int]]],
    ultimo_fin_cond_bus: Dict[Tuple[int, int], int],
) -> Optional[int]:
    """Devuelve el conductor activo en el bus durante [ini_ev, fin_ev].
    Usa tramos reales (no rango único) para no asignar eventos en huecos entre bloques."""
    mejor_c = None
    mejor_overlap = -1
    for (cid, bus), tramos in tramos_cond_bus.items():
        if bus != bus_idx:
            continue
        # Verificar que el conductor no haya dejado el bus antes de este evento
        ultimo = ultimo_fin_cond_bus.get((cid, bus))
        if ultimo is not None and ini_ev >= ultimo:
            continue
        overlap = _overlap_con_tramos(ini_ev, fin_ev, tramos)
        if overlap > mejor_overlap:
            mejor_overlap = overlap
            mejor_c = cid
    return mejor_c if mejor_overlap > 0 else None


# ---------------------------------------------------------------------------
# Creación de InS, Desplazamiento y FnS
# ---------------------------------------------------------------------------

def _crear_eventos_marco(
    c_id: int,
    turno: Dict[str, Any],
    eventos_asignados: List[Dict[str, Any]],
    gestor: Any,
    solo_eventos_fase_1: bool,
) -> List[Dict[str, Any]]:
    """
    Crea InS, Desplazamiento(s) y FnS para un conductor.

    Lógica nodo-a-nodo:
      InS(depot, duración=tiempo_toma)
      → [Desp(depot→primer_nodo_no_depot)?]
      → [eventos existentes del bus]
      → [Desp(ultimo_nodo→depot)?]  (si no hay vacio asignado que ya lo haga)
      → FnS(depot)
    """
    deposito = gestor.deposito_base
    tiempo_toma = gestor.tiempo_toma
    dep_ini = (turno.get("deposito_inicio") or deposito).strip()

    # Ordenar eventos ya asignados
    otros = sorted(
        [e for e in eventos_asignados if str(e.get("evento", "")).upper() not in ("INS", "FNS")],
        key=lambda x: (x.get("inicio", 0), x.get("bus", 0))
    )
    if not otros:
        return []

    nuevos: List[Dict[str, Any]] = []
    primer_ev = otros[0]
    ultimo_ev = otros[-1]

    # --- InS ---
    # InS termina cuando empieza el primer evento del conductor
    # Si hay Desplazamiento depot→N, el InS termina cuando empieza ese desplazamiento
    origen_primer = (primer_ev.get("origen") or "").strip()

    if _es_deposito(origen_primer, dep_ini):
        # El conductor ya está en el depósito cuando empieza su primer evento
        ins_fin = primer_ev.get("inicio", 0)
        ins_inicio = max(0, ins_fin - tiempo_toma)
    else:
        # Hay un desplazamiento del conductor desde el depósito al primer nodo
        if not solo_eventos_fase_1:
            hab, t_desp = gestor.buscar_info_desplazamiento(dep_ini, origen_primer,
                                                             max(0, primer_ev.get("inicio", 0) - 60))
            if hab and t_desp is not None:
                desp_inicio = primer_ev.get("inicio", 0) - t_desp
                desp_fin = primer_ev.get("inicio", 0)
                nuevos.append({
                    "evento": "Desplazamiento",
                    "bus": primer_ev.get("bus", ""),
                    "conductor": c_id,
                    "inicio": desp_inicio,
                    "fin": desp_fin,
                    "origen": dep_ini,
                    "destino": origen_primer,
                })
                ins_fin = desp_inicio
                ins_inicio = max(0, ins_fin - tiempo_toma)
            else:
                ins_fin = primer_ev.get("inicio", 0)
                ins_inicio = max(0, ins_fin - tiempo_toma)
        else:
            ins_fin = primer_ev.get("inicio", 0)
            ins_inicio = max(0, ins_fin - tiempo_toma)

    nuevos.insert(0, {
        "evento": "InS",
        "bus": "",
        "conductor": c_id,
        "inicio": ins_inicio,
        "fin": ins_fin,
        "origen": dep_ini,
        "destino": dep_ini,
    })

    # --- Desplazamiento(s) entre buses (cambio de bus en Fase 3) ---
    # Cuando el conductor cambia de bus, necesita un desplazamiento desde el
    # destino del último evento del bus anterior al origen del primer evento del nuevo bus.
    if not solo_eventos_fase_1:
        # DEBUG TEMPORAL
        if c_id == 86:
            print(f"[DEBUG-MARCO-C86] otros ordenados: {[(e.get('evento'), repr(e.get('bus')), e.get('inicio'), e.get('fin'), e.get('origen','')[:12], e.get('destino','')[:12]) for e in otros]}")
        for idx in range(len(otros) - 1):
            ev_a = otros[idx]
            ev_b = otros[idx + 1]
            bus_a = ev_a.get("bus")
            bus_b = ev_b.get("bus")
            if bus_a == bus_b:
                continue
            dest_a = (ev_a.get("destino") or "").strip()
            orig_b = (ev_b.get("origen") or "").strip()
            fin_a = ev_a.get("fin", 0)
            ini_b = ev_b.get("inicio", 0)
            gap = ini_b - fin_a
            # Si ya hay continuidad de nodo o gap pequeño (< 5 min), no crear desplazamiento
            if _nodos_iguales(dest_a, orig_b) or gap < 5:
                continue
            # Si ya existe un Desplazamiento o Vacio que cubre este intervalo, no duplicar
            ya_cubre = any(
                str(e.get("evento", "")).upper() in ("DESPLAZAMIENTO", "VACIO")
                and abs(e.get("inicio", 0) - fin_a) <= 15
                for e in nuevos
            )
            if ya_cubre:
                continue
            # Calcular tiempo de desplazamiento
            hab_d, t_d = gestor.buscar_info_desplazamiento(dest_a, orig_b, fin_a)
            if hab_d and t_d is not None:
                desp_fin = fin_a + t_d
            else:
                t_v, _ = gestor.buscar_tiempo_vacio(dest_a, orig_b, fin_a)
                desp_fin = fin_a + (t_v or min(gap, 90))
            # El desplazamiento no puede solaparse con el próximo evento
            desp_fin = min(desp_fin, ini_b)
            # DEBUG TEMPORAL
            if c_id == 86:
                print(f"[DEBUG-MARCO-C86] CREA Desplazamiento inicio={fin_a} fin={desp_fin} {dest_a} -> {orig_b}")
            nuevos.append({
                "evento": "Desplazamiento",
                "bus": "",
                "conductor": c_id,
                "inicio": fin_a,
                "fin": desp_fin,
                "origen": dest_a,
                "destino": orig_b,
            })

    # --- FnS ---
    # FnS empieza cuando termina el último evento del conductor (o cuando llega al depósito)
    destino_ultimo = (ultimo_ev.get("destino") or "").strip()

    if _es_deposito(destino_ultimo, dep_ini):
        fns_inicio = ultimo_ev.get("fin", 0)
    else:
        # Verificar si ya hay un Vacio/Desplazamiento que lleve al depósito
        hay_retorno = any(
            str(e.get("evento", "")).upper() in ("VACIO", "DESPLAZAMIENTO")
            and _nodos_iguales(e.get("origen", ""), destino_ultimo)
            and _es_deposito(e.get("destino", ""), dep_ini)
            for e in otros
        )
        if hay_retorno:
            # Usar el fin del último evento de retorno
            retorno_ev = [
                e for e in otros
                if str(e.get("evento", "")).upper() in ("VACIO", "DESPLAZAMIENTO")
                and _nodos_iguales(e.get("origen", ""), destino_ultimo)
                and _es_deposito(e.get("destino", ""), dep_ini)
            ]
            fns_inicio = max(e.get("fin", 0) for e in retorno_ev)
        elif not solo_eventos_fase_1:
            # Crear Desplazamiento nodo→depot
            hab, t_dep = gestor.buscar_info_desplazamiento(destino_ultimo, dep_ini,
                                                            ultimo_ev.get("fin", 0))
            if not hab or t_dep is None:
                # Fallback: usar vacío
                t_dep, _ = gestor.buscar_tiempo_vacio(destino_ultimo, dep_ini, ultimo_ev.get("fin", 0))
                t_dep = t_dep or 20
            fin_desp = ultimo_ev.get("fin", 0) + t_dep
            nuevos.append({
                "evento": "Desplazamiento",
                "bus": ultimo_ev.get("bus", ""),
                "conductor": c_id,
                "inicio": ultimo_ev.get("fin", 0),
                "fin": fin_desp,
                "origen": destino_ultimo,
                "destino": dep_ini,
            })
            fns_inicio = fin_desp
        else:
            fns_inicio = turno.get("fin", ultimo_ev.get("fin", 0))

    nuevos.append({
        "evento": "FnS",
        "bus": "",
        "conductor": c_id,
        "inicio": fns_inicio,
        "fin": fns_inicio,
        "origen": dep_ini,
        "destino": dep_ini,
    })

    return nuevos


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def ensamblar_eventos_conductores(
    turnos_seleccionados: List[Dict[str, Any]],
    eventos_bus: List[List[Dict[str, Any]]],
    viajes_comerciales: List[Dict[str, Any]],
    metadata_tareas: Dict[Any, Dict[str, Any]],
    gestor: Any,
    verbose: bool = False,
    solo_eventos_fase_1: bool = False,
) -> List[Dict[str, Any]]:
    """
    Ensambla la lista completa de eventos de conductores.

    Pasos:
      1. Asignar conductor a cada evento de bus (Comercial, Vacio, Parada, Recarga)
      2. Para cada conductor: crear InS, Desplazamiento(s) y FnS
      3. Ordenar: InS → eventos (por inicio) → FnS
    """
    # IMPORTANTE: aquí debemos ser coherentes con Fase 2/3.
    # Allí los turnos usan como identificador canónico de viaje:
    #   canon = v.get("id") or v.get("_tmp_id")
    # Si aquí solo consideramos v["id"], todos los viajes que solo tienen _tmp_id
    # quedan fuera de ids_com y mapa_viajes, y sus eventos Comerciales nunca
    # reciben conductor aunque el turno sí los tenga asignados.
    ids_com: Set[Any] = set()
    mapa_viajes: Dict[Any, Dict] = {}
    for v in viajes_comerciales:
        for key in (v.get("id"), v.get("_tmp_id")):
            if key is None:
                continue
            ids_com.add(key)
            ids_com.add(str(key))
            mapa_viajes[key] = v
            mapa_viajes[str(key)] = v
    # Agregar metadata también (por si hay ids adicionales usados en turnos)
    for tid, meta in (metadata_tareas or {}).items():
        v = meta.get("viaje") if isinstance(meta, dict) else None
        if v and tid not in mapa_viajes:
            mapa_viajes[tid] = v
            mapa_viajes[str(tid)] = v

    deposito = gestor.deposito_base

    # 1. Asignar conductor a eventos de bus
    eventos_planos = _asignar_conductor_a_eventos(
        eventos_bus, turnos_seleccionados, ids_com, mapa_viajes, gestor
    )

    # 2. Agrupar por conductor
    por_conductor: Dict[int, List[Dict]] = {}
    for ev in eventos_planos:
        c = ev.get("conductor")
        if c is None:
            continue
        por_conductor.setdefault(c, []).append(ev)

    # 3. Crear InS, Desplazamientos y FnS por conductor
    resultado_final: List[Dict[str, Any]] = []

    for c_id, turno in enumerate(turnos_seleccionados, start=1):
        evs_asignados = por_conductor.get(c_id, [])

        # Verificar si tiene al menos un comercial
        tiene_comercial = any(
            str(e.get("evento", "")).upper() == "COMERCIAL"
            for e in evs_asignados
        )
        if not tiene_comercial:
            continue

        # Crear eventos marco (InS, Desplazamientos, FnS)
        evs_marco = _crear_eventos_marco(c_id, turno, evs_asignados, gestor, solo_eventos_fase_1)

        # Ordenar: InS → otros (por inicio) → FnS
        ins = [e for e in evs_marco if str(e.get("evento", "")).upper() == "INS"]
        fns = [e for e in evs_marco if str(e.get("evento", "")).upper() == "FNS"]
        desps = [e for e in evs_marco if str(e.get("evento", "")).upper() == "DESPLAZAMIENTO"]

        otros = sorted(evs_asignados + desps, key=lambda x: (x.get("inicio", 0), x.get("bus", 0)))

        resultado_final.extend(ins)
        resultado_final.extend(otros)
        resultado_final.extend(fns)

    # REGLA DURA: Sin solapamiento Comercial-Comercial por conductor+bus (no negociable)
    validar_eventos_sin_solapamiento_conductor_bus(resultado_final)

    return resultado_final


def preparar_eventos_para_excel(
    eventos_bus: List[List[Dict[str, Any]]],
    turnos_seleccionados: List[Dict[str, Any]],
    viajes_comerciales: List[Dict[str, Any]],
    metadata_tareas: Dict[Any, Dict[str, Any]],
    gestor: Any,
    verbose: bool = False,
    solo_eventos_fase_1: bool = False,
) -> Tuple[List[List[Dict[str, Any]]], List[Dict[str, Any]], Dict[int, Optional[str]]]:
    """Wrapper para el exportador Excel."""
    eventos_cond = ensamblar_eventos_conductores(
        turnos_seleccionados, eventos_bus, viajes_comerciales,
        metadata_tareas, gestor, verbose, solo_eventos_fase_1
    )
    bus_tipo_map: Dict[int, Optional[str]] = {}
    return eventos_bus, eventos_cond, bus_tipo_map
