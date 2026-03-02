"""
FASE 2 V2.0 - Asignación de Conductores
========================================
Principio: cada turno es una cadena de nodos perfectamente conectados.

  InS(depot) → [Desp(depot→N)?] → viajes comerciales → [Desp(N→depot)?] → FnS(depot)

REGLAS DURAS:
  1. Cortes SOLO en puntos de relevo válidos (desplazamiento bidireccional habilitado al depósito)
     o en el depósito mismo.
  2. Duración turno ≤ limite_jornada (sin tolerancia).
  3. 100% cobertura de viajes comerciales.
  4. El conductor entrante llega al nodo de relevo vía Desplazamiento(depot→relay).
  5. El conductor saliente sale del nodo de relevo vía Desplazamiento(relay→depot).
  6. Cortar ANTES de parada larga: si entre dos viajes hay un gap > parada_larga_umbral (ej. 60 min),
     el turno termina en el viaje anterior; el siguiente conductor empieza en el viaje posterior.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from diagramador_optimizado.core.domain.logistica import GestorDeLogistica
from diagramador_optimizado.core.validaciones_fase import validar_fase2_sin_solapamiento_turnos


# ---------------------------------------------------------------------------
# Funciones auxiliares puras
# ---------------------------------------------------------------------------

def _es_deposito(nodo: str, deposito: str) -> bool:
    """
    True si nodo es el depósito (comparación insensible a mayúsculas),
    sin confundir terminales con el depósito.

    Ejemplo: 'PIE ANDINO' (terminal) ≠ 'DEPOSITO PIE ANDINO' (depósito).
    Solo se consideran equivalentes cuando AMBOS nombres contienen
    la palabra 'DEPOSITO' y uno es subcadena del otro, o son iguales.
    """
    a = (nodo or "").strip().upper()
    b = (deposito or "").strip().upper()
    if not a or not b:
        return False
    if a == b:
        return True
    if "DEPOSITO" in a and "DEPOSITO" in b and (a in b or b in a):
        return True
    return False


def _es_relevo_valido(nodo: str, deposito: str, gestor: GestorDeLogistica) -> Tuple[bool, int, int]:
    """
    Un nodo es punto de relevo válido si:
      - Es el depósito (tiempo_ida=0, tiempo_vuelta=0), O
      - desplazamiento(depot→nodo) habilitado=True Y desplazamiento(nodo→depot) habilitado=True

    Returns: (valido, t_depot_to_node, t_node_to_depot)
    """
    if _es_deposito(nodo, deposito):
        return True, 0, 0

    # Verificar ambas direcciones
    hab_ida, t_ida = gestor.buscar_info_desplazamiento(deposito, nodo, 0)
    hab_vuelta, t_vuelta = gestor.buscar_info_desplazamiento(nodo, deposito, 0)

    if hab_ida and hab_vuelta and t_ida is not None and t_vuelta is not None:
        return True, int(t_ida), int(t_vuelta)

    return False, 0, 0


def _calcular_inicio_turno(
    primer_viaje: Dict[str, Any],
    relay_node_anterior: Optional[str],
    deposito: str,
    gestor: GestorDeLogistica,
    tiempo_toma: int,
    es_primer_turno: bool,
) -> int:
    """
    Calcula el inicio del turno para un conductor.

    Primer turno: InS(depot) → Vacio(depot→origen_primer_viaje) → primer viaje
    Turno entrante (relevo): InS(depot) → Desp(depot→relay_node) → [Vacio(relay→origen)?] → primer viaje

    Retorna el tiempo de inicio (InS empieza aquí).
    """
    origen = (primer_viaje.get("origen") or "").strip()
    inicio_viaje = primer_viaje["inicio"]

    if es_primer_turno or relay_node_anterior is None:
        # Conductor sale directo del depot al primer nodo (vacío del bus)
        t_vacio, _ = gestor.buscar_tiempo_vacio(deposito, origen, inicio_viaje)
        t_vacio = t_vacio or 0
        return inicio_viaje - t_vacio - tiempo_toma

    # Conductor entrante en relevo: viene de depot al relay_node
    relay = relay_node_anterior
    _, t_dep_to_relay, _ = _es_relevo_valido(relay, deposito, gestor)

    if _es_deposito(relay, deposito):
        # Relay es el depósito: conductor sale directo al origen del primer viaje
        t_vacio, _ = gestor.buscar_tiempo_vacio(deposito, origen, inicio_viaje)
        t_vacio = t_vacio or 0
        return inicio_viaje - t_vacio - tiempo_toma

    # Relay no es depósito: conductor va depot→relay_node, luego relay_node→origen del viaje
    if _es_deposito(origen, relay) or origen.upper() == relay.upper():
        # El viaje empieza exactamente en el relay node
        return inicio_viaje - t_dep_to_relay - tiempo_toma
    else:
        # Hay un vacio relay→origen del viaje (el bus lleva al conductor desde relay a origen)
        t_vacio_relay_orig, _ = gestor.buscar_tiempo_vacio(relay, origen, inicio_viaje)
        t_vacio_relay_orig = t_vacio_relay_orig or 0
        return inicio_viaje - t_vacio_relay_orig - t_dep_to_relay - tiempo_toma


def _calcular_fin_turno(
    ultimo_viaje: Dict[str, Any],
    deposito: str,
    gestor: GestorDeLogistica,
) -> Tuple[int, bool]:
    """
    Calcula el fin del turno para un conductor.

    Si termina en depósito → fin = último viaje fin (bus ya está ahí)
    Si termina en relay N → fin = último viaje fin + t_desplaz(N→depot)

    Retorna (fin_turno, es_relevo_o_depot).
    """
    destino = (ultimo_viaje.get("destino") or "").strip()
    fin_viaje = ultimo_viaje["fin"]

    if _es_deposito(destino, deposito):
        return fin_viaje, True

    valido, _, t_node_to_dep = _es_relevo_valido(destino, deposito, gestor)
    if valido:
        return fin_viaje + t_node_to_dep, True

    return fin_viaje, False  # No es punto de relevo válido


def _puede_terminar_aqui(ultimo_viaje: Dict[str, Any], deposito: str, gestor: GestorDeLogistica) -> bool:
    """True si el turno puede terminar legalmente en este viaje (destino es depot o relay)."""
    destino = (ultimo_viaje.get("destino") or "").strip()
    if not destino:
        return False
    if _es_deposito(destino, deposito):
        return True
    valido, _, _ = _es_relevo_valido(destino, deposito, gestor)
    return valido


def _id_viaje(viaje: Dict[str, Any], fallback: str) -> Any:
    return viaje.get("id") or viaje.get("_tmp_id") or fallback


def _canonical_viaje_id(viaje: Dict[str, Any], mapa_viaje: Dict[Any, Dict[str, Any]], fallback: str) -> Any:
    """Identificador canónico para exportación: id si existe, si no _tmp_id. Coherente con viajes_comerciales."""
    tid = _id_viaje(viaje, fallback)
    v = mapa_viaje.get(tid) or mapa_viaje.get(str(tid))
    if v is not None:
        return v.get("id") or v.get("_tmp_id") or tid
    return tid


# ---------------------------------------------------------------------------
# División de bloque en turnos
# ---------------------------------------------------------------------------

def _parada_larga_umbral(gestor: GestorDeLogistica) -> int:
    """Minutos a partir de los cuales un gap entre dos viajes se considera parada larga (corte obligatorio)."""
    return gestor.parada_larga_umbral


def _dividir_bloque(
    bloque: List[Dict[str, Any]],
    id_bus: int,
    deposito: str,
    limite_jornada: int,
    tiempo_toma: int,
    gestor: GestorDeLogistica,
    mapa_viaje: Optional[Dict[Any, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Divide un bloque de viajes comerciales en turnos de conductores.

    Algoritmo:
      1. Desde idx_inicio, buscar el corte más lejano que:
         a) Termina en depot o relay válido
         b) Duración del turno ≤ limite_jornada
         c) No incluye parada larga: ningún gap entre viajes del turno > parada_larga_umbral
      2. Crear turno y avanzar al siguiente segmento.
      3. Repetir hasta cubrir todo el bloque.

    REGLA: cortes SOLO en depot o nodo con desplazamiento bidireccional al depósito.
    REGLA: cortar antes de parada larga (gap entre comerciales > umbral) para que el conductor no la absorba.
    """
    n = len(bloque)
    if n == 0:
        return []

    umbral_parada_larga = _parada_larga_umbral(gestor)
    # Gap entre viaje i y viaje i+1 (en minutos)
    gaps: List[int] = []
    for i in range(n - 1):
        gap = bloque[i + 1]["inicio"] - bloque[i]["fin"]
        if gap < 0:
            gap += 1440
        gaps.append(gap)

    turnos: List[Dict[str, Any]] = []
    idx_inicio = 0
    relay_anterior: Optional[str] = None  # Relay node del turno anterior (para calcular inicio del siguiente)
    es_primer_turno = True

    while idx_inicio < n:
        primer_viaje = bloque[idx_inicio]

        # Calcular inicio de este turno
        inicio_turno = _calcular_inicio_turno(
            primer_viaje, relay_anterior, deposito, gestor, tiempo_toma, es_primer_turno
        )

        # Buscar el corte MÁS LEJANO válido (de adelante hacia atrás)
        # Condición extra: el segmento [idx_inicio, idx_fin] no debe contener ningún gap > umbral (parada larga)
        mejor_fin = -1
        for idx_fin in range(n - 1, idx_inicio - 1, -1):
            ultimo_viaje = bloque[idx_fin]

            if not _puede_terminar_aqui(ultimo_viaje, deposito, gestor):
                continue

            # No incluir parada larga dentro del turno: todos los gaps en [idx_inicio, idx_fin-1] <= umbral
            tiene_parada_larga = False
            for j in range(idx_inicio, idx_fin):
                if j < len(gaps) and gaps[j] > umbral_parada_larga:
                    tiene_parada_larga = True
                    break
            if tiene_parada_larga:
                continue

            fin_turno, _ = _calcular_fin_turno(ultimo_viaje, deposito, gestor)
            duracion = fin_turno - inicio_turno
            if duracion < 0:
                duracion += 1440  # cruzar medianoche

            if duracion <= limite_jornada:
                mejor_fin = idx_fin
                break  # Encontramos el más lejano válido (iteramos al revés)

        if mejor_fin < idx_inicio:
            # No hay corte válido que empiece en idx_inicio dentro del límite de jornada.
            # Crear turno de emergencia para el viaje en idx_inicio (solo ese viaje + retorno al depósito).
            viaje_solo = bloque[idx_inicio]
            dest_solo = (viaje_solo.get("destino") or "").strip()
            fin_solo = viaje_solo["fin"]
            if not _es_deposito(dest_solo, deposito):
                t_ret = gestor.buscar_tiempo_vacio(dest_solo, deposito, fin_solo)[0] or 0
                if not t_ret:
                    _, _, t_desp = _es_relevo_valido(dest_solo, deposito, gestor)
                    t_ret = t_desp or 0
                fin_solo = fin_solo + t_ret
            dur = fin_solo - inicio_turno
            if dur < 0:
                dur += 1440
            tid_canon = (_canonical_viaje_id(viaje_solo, mapa_viaje, f"_ev_{id_bus}_{idx_inicio}")
                         if mapa_viaje else _id_viaje(viaje_solo, f"_ev_{id_bus}_{idx_inicio}"))
            turnos.append({
                "id_bus": id_bus,
                "tareas_con_bus": [(tid_canon, id_bus)],
                "inicio": inicio_turno,
                "fin": fin_solo,
                "duracion": dur,
                "overtime": dur > limite_jornada,
                "deposito_inicio": deposito,
                "punto_fin_turno": deposito,
            })
            idx_inicio += 1
            relay_anterior = None
            es_primer_turno = False
            continue

        # Crear el turno
        subbloque = bloque[idx_inicio: mejor_fin + 1]
        ultimo_v = subbloque[-1]
        fin_turno, _ = _calcular_fin_turno(ultimo_v, deposito, gestor)
        duracion = fin_turno - inicio_turno
        if duracion < 0:
            duracion += 1440

        relay_node = (ultimo_v.get("destino") or "").strip()

        def _tid(v: Dict, j: int) -> Any:
            if mapa_viaje:
                return _canonical_viaje_id(v, mapa_viaje, f"_ev_{id_bus}_{idx_inicio + j}")
            return _id_viaje(v, f"_ev_{id_bus}_{idx_inicio + j}")
        turno = {
            "id_bus": id_bus,
            "tareas_con_bus": [
                (_tid(v, j), id_bus)
                for j, v in enumerate(subbloque)
            ],
            "inicio": inicio_turno,
            "fin": fin_turno,
            "duracion": duracion,
            "overtime": False,
            "deposito_inicio": deposito,
            "punto_fin_turno": deposito if _es_deposito(relay_node, deposito) else relay_node,
        }
        turnos.append(turno)

        # Preparar siguiente turno
        relay_anterior = relay_node if not _es_deposito(relay_node, deposito) else None
        idx_inicio = mejor_fin + 1
        es_primer_turno = False

    return turnos


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def resolver_diagramacion_conductores(
    config: Dict[str, Any],
    viajes_comerciales: List[Dict[str, Any]],
    bloques_bus: List[List[Dict[str, Any]]],
    gestor: GestorDeLogistica,
    verbose: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[Any, Dict[str, Any]], str]:
    """
    FASE 2: Asigna conductores a los bloques de buses.

    Por cada bloque de bus:
      - Si cabe en un turno (duración ≤ limite_jornada) y termina en depot/relay → 1 conductor
      - Si no → dividir en múltiples turnos con cortes en relays válidos

    Garantías:
      - 100% viajes comerciales cubiertos
      - Sin turnos que excedan limite_jornada
      - Todo turno inicia y termina en depósito o punto de relevo autorizado
    """
    print("\n" + "=" * 70)
    print("FASE 2: Asignación de Conductores V2.0")
    print("=" * 70)

    limite_jornada: int = gestor.limite_jornada
    tiempo_toma: int = gestor.tiempo_toma
    deposito: str = gestor.deposito_base

    parada_larga_umbral = gestor.parada_larga_umbral
    print(f"  Límite jornada : {limite_jornada} min")
    print(f"  Tiempo toma    : {tiempo_toma} min")
    print(f"  Parada larga   : corte si gap > {parada_larga_umbral} min entre comerciales")
    print(f"  Depósito base  : {deposito}")

    # --- Construir metadata de tareas ---
    # REGLA DURA: Todo viaje comercial debe quedar asignado a exactamente un conductor.
    # Incluir id y _tmp_id para no perder ningún viaje.
    ids_viajes: set = set()
    mapa_viaje: Dict[Any, Dict[str, Any]] = {}
    for v in viajes_comerciales:
        for key in (v.get("id"), v.get("_tmp_id")):
            if key is not None:
                ids_viajes.add(key)
                ids_viajes.add(str(key))
                mapa_viaje[key] = v
                mapa_viaje[str(key)] = v

    metadata_tareas: Dict[Any, Dict[str, Any]] = {}
    for id_bus, bloque in enumerate(bloques_bus):
        for i, viaje in enumerate(bloque):
            tid = _id_viaje(viaje, f"_ev_{id_bus}_{i}")
            id_sig = None
            if i < len(bloque) - 1:
                id_sig = _id_viaje(bloque[i + 1], f"_ev_{id_bus}_{i+1}")
            meta = {
                "viaje": viaje,
                "id_bus": id_bus,
                "es_primero": i == 0,
                "es_ultimo": i == len(bloque) - 1,
                "id_siguiente": id_sig,
            }
            metadata_tareas[tid] = meta
            metadata_tareas[str(tid)] = meta
            canon = _canonical_viaje_id(viaje, mapa_viaje, tid)
            if canon != tid:
                metadata_tareas[canon] = meta
                metadata_tareas[str(canon)] = meta

    # --- Asignar conductores por bloque ---
    turnos: List[Dict[str, Any]] = []

    for id_bus, bloque in enumerate(bloques_bus):
        if not bloque:
            continue

        primer_v = bloque[0]
        ultimo_v = bloque[-1]

        # Calcular duración total del bloque (con vacíos de ida y vuelta)
        t_vacio_ida, _ = gestor.buscar_tiempo_vacio(deposito, primer_v["origen"], primer_v["inicio"])
        t_vacio_ida = t_vacio_ida or 0
        inicio_bloque = primer_v["inicio"] - t_vacio_ida - tiempo_toma

        fin_bloque, termina_bien = _calcular_fin_turno(ultimo_v, deposito, gestor)

        duracion_bloque = fin_bloque - inicio_bloque
        if duracion_bloque < 0:
            duracion_bloque += 1440

        # CASO A: Bloque completo cabe en un turno y termina en relay/depot válido
        if duracion_bloque <= limite_jornada and termina_bien:
            relay_fin = (ultimo_v.get("destino") or "").strip()
            turno = {
                "id_bus": id_bus,
                "tareas_con_bus": [
                    (_canonical_viaje_id(v, mapa_viaje, f"_ev_{id_bus}_{j}"), id_bus)
                    for j, v in enumerate(bloque)
                ],
                "inicio": inicio_bloque,
                "fin": fin_bloque,
                "duracion": duracion_bloque,
                "overtime": False,
                "deposito_inicio": deposito,
                "punto_fin_turno": deposito if _es_deposito(relay_fin, deposito) else relay_fin,
            }
            turnos.append(turno)
            if verbose:
                print(f"  Bus {id_bus+1}: 1 conductor ({duracion_bloque} min)")
            continue

        # CASO B: Necesita división en múltiples turnos
        sub_turnos = _dividir_bloque(bloque, id_bus, deposito, limite_jornada, tiempo_toma, gestor, mapa_viaje)

        if sub_turnos:
            turnos.extend(sub_turnos)
            if verbose:
                print(f"  Bus {id_bus+1}: {len(sub_turnos)} conductores (dividido)")
        else:
            # Fallback: crear 1 turno unitario por bloque aunque exceda el límite
            turno_fb = {
                "id_bus": id_bus,
                "tareas_con_bus": [
                    (_canonical_viaje_id(v, mapa_viaje, f"_ev_{id_bus}_{j}"), id_bus)
                    for j, v in enumerate(bloque)
                ],
                "inicio": inicio_bloque,
                "fin": fin_bloque,
                "duracion": duracion_bloque,
                "overtime": duracion_bloque > limite_jornada,
                "deposito_inicio": deposito,
                "punto_fin_turno": deposito,
            }
            turnos.append(turno_fb)
            if verbose:
                print(f"  Bus {id_bus+1}: 1 conductor FALLBACK (sin corte válido encontrado)")

    # --- Garantizar 100% cobertura ---
    turnos = _garantizar_cobertura(turnos, ids_viajes, mapa_viaje, bloques_bus, metadata_tareas,
                                    deposito, limite_jornada, tiempo_toma, gestor, viajes_comerciales)

    # --- Normalizar punto_fin_turno ---
    for t in turnos:
        pf = (t.get("punto_fin_turno") or "").strip()
        if not pf or _es_deposito(pf, deposito):
            t["punto_fin_turno"] = deposito
            continue
        valido, _, _ = _es_relevo_valido(pf, deposito, gestor)
        if not valido:
            t["punto_fin_turno"] = deposito

    # Asegurar deposito_inicio siempre es el depósito
    for t in turnos:
        t["deposito_inicio"] = deposito

    # Filtrar turnos sin viajes comerciales (considerando también ids sintéticos
    # presentes en metadata_tareas, para no perder bloques añadidos en rescates).
    ids_com_str = ids_viajes | {str(v) for v in ids_viajes}
    ids_meta = {tid for tid in metadata_tareas} | {str(tid) for tid in metadata_tareas}
    ids_totales = ids_com_str | ids_meta
    turnos = [
        t for t in turnos
        if any(str(tid) in ids_totales or tid in ids_totales for tid, _ in t.get("tareas_con_bus", []))
    ]

    # REGLA DURA: Sin solapamiento conductor+bus en turnos (no negociable)
    mapa_para_validar = dict(mapa_viaje)
    for tid, meta in metadata_tareas.items():
        if "viaje" in meta and tid not in mapa_para_validar:
            mapa_para_validar[tid] = meta["viaje"]
    validar_fase2_sin_solapamiento_turnos(turnos, mapa_para_validar)

    # Verificar cobertura final: un viaje está cubierto si su id canónico está en algún turno
    canonical_ids = {v.get("id") or v.get("_tmp_id") for v in viajes_comerciales if (v.get("id") or v.get("_tmp_id")) is not None}
    cubiertos_canon = set()
    for t in turnos:
        for tid, _ in t.get("tareas_con_bus", []):
            if tid is None:
                continue
            if tid in canonical_ids or str(tid) in {str(c) for c in canonical_ids}:
                cubiertos_canon.add(tid if tid in canonical_ids else next((c for c in canonical_ids if str(c) == str(tid)), tid))

    faltantes = canonical_ids - cubiertos_canon
    overtime_count = sum(1 for t in turnos if t.get("overtime", False))

    print(f"\n  RESULTADO FASE 2:")
    print(f"    Conductores    : {len(turnos)}")
    print(f"    Viajes cubiertos: {len(cubiertos_canon)}/{len(canonical_ids)}")
    print(f"    Con overtime   : {overtime_count}")
    if faltantes:
        print(f"    FALTANTES      : {len(faltantes)} viajes sin conductor")
    print("=" * 70)

    return turnos, metadata_tareas, "OPTIMAL"


def _garantizar_cobertura(
    turnos: List[Dict[str, Any]],
    ids_viajes: set,
    mapa_viaje: Dict[Any, Dict[str, Any]],
    bloques_bus: List[List[Dict[str, Any]]],
    metadata_tareas: Dict[Any, Dict[str, Any]],
    deposito: str,
    limite_jornada: int,
    tiempo_toma: int,
    gestor: GestorDeLogistica,
    viajes_comerciales: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Revisa que todos los viajes comerciales estén cubiertos.
    Para los faltantes, crea turnos unitarios de rescate.
    """
    def _ya_cubierto(vid: Any) -> bool:
        s = str(vid)
        for t in turnos:
            for tid, _ in t.get("tareas_con_bus", []):
                if tid == vid or str(tid) == s:
                    return True
        return False

    # Mapa viaje_id → id_bus (por id y _tmp_id para no perder ninguno)
    viaje_a_bus: Dict[Any, int] = {}
    for id_bus, bloque in enumerate(bloques_bus):
        for v in bloque:
            for key in (v.get("id"), v.get("_tmp_id")):
                if key is not None:
                    viaje_a_bus[key] = id_bus
                    viaje_a_bus[str(key)] = id_bus

    # Un rescate por viaje (usar id canónico para no duplicar por id y _tmp_id)
    vistos_canon: set = set()
    nuevos = []
    for v in viajes_comerciales:
        canon = v.get("id") or v.get("_tmp_id")
        if canon is None:
            continue
        if canon in vistos_canon or str(canon) in vistos_canon:
            continue
        if _ya_cubierto(canon):
            vistos_canon.add(canon)
            vistos_canon.add(str(canon))
            continue
        vistos_canon.add(canon)
        vistos_canon.add(str(canon))
        viaje = v
        id_bus = viaje_a_bus.get(canon, viaje_a_bus.get(str(canon), 0))
        t_vacio, _ = gestor.buscar_tiempo_vacio(deposito, viaje["origen"], viaje["inicio"])
        t_vacio = t_vacio or 0
        inicio = viaje["inicio"] - t_vacio - tiempo_toma
        fin, _ = _calcular_fin_turno(viaje, deposito, gestor)
        dur = max(1, fin - inicio)
        nuevos.append({
            "id_bus": id_bus,
            "tareas_con_bus": [(canon, id_bus)],
            "inicio": inicio,
            "fin": fin,
            "duracion": dur,
            "overtime": dur > limite_jornada,
            "deposito_inicio": deposito,
            "punto_fin_turno": deposito,
        })

    if nuevos:
        print(f"  [COBERTURA] Creados {len(nuevos)} turnos de rescate para viajes no cubiertos")
        turnos.extend(nuevos)

    # Garantizar al menos un turno por cada bus que tenga viajes en bloques_bus.
    # Esto cubre casos donde un bloque completo queda sin conductor (p.ej. buses
    # añadidos en reglas de cobertura de Fase 1), aunque sus viajes ya estén
    # cubiertos en otros buses.
    buses_con_viajes: Set[int] = {i for i, bloque in enumerate(bloques_bus) if bloque}
    buses_con_turnos: Set[int] = {t.get("id_bus") for t in turnos if t.get("tareas_con_bus")}
    buses_sin_turno = sorted(buses_con_viajes - buses_con_turnos)

    if buses_sin_turno:
        print(f"  [COBERTURA] Buses sin conductor detectados: {', '.join(str(b + 1) for b in buses_sin_turno)}")
        fallback_buses: List[Dict[str, Any]] = []
        for id_bus in buses_sin_turno:
            bloque = bloques_bus[id_bus]
            if not bloque:
                continue

            inicio_bloque = bloque[0].get("inicio", 0)
            ultimo_viaje = bloque[-1]
            fin_bloque, _ = _calcular_fin_turno(ultimo_viaje, deposito, gestor)
            duracion_bloque = fin_bloque - inicio_bloque
            if duracion_bloque < 0:
                duracion_bloque += 1440

            tareas_bus: List[Tuple[Any, int]] = []
            for j, v in enumerate(bloque):
                # Usar siempre un id sintético para no colisionar con ids canónicos
                # y poder distinguir este bloque concreto en el writer.
                tid = f"_ev_fb_{id_bus}_{j}"
                tareas_bus.append((tid, id_bus))

                # Asegurar que metadata_tareas tenga entrada para este tid, para validaciones
                if tid not in metadata_tareas and str(tid) not in metadata_tareas:
                    id_sig = None
                    if j < len(bloque) - 1:
                        id_sig = f"_ev_fb_{id_bus}_{j + 1}"
                    meta = {
                        "viaje": v,
                        "id_bus": id_bus,
                        "es_primero": j == 0,
                        "es_ultimo": j == len(bloque) - 1,
                        "id_siguiente": id_sig,
                    }
                    metadata_tareas[tid] = meta
                    metadata_tareas[str(tid)] = meta

            turno_bus = {
                "id_bus": id_bus,
                "tareas_con_bus": tareas_bus,
                "inicio": inicio_bloque,
                "fin": fin_bloque,
                "duracion": duracion_bloque,
                "overtime": duracion_bloque > limite_jornada,
                "deposito_inicio": deposito,
                "punto_fin_turno": deposito,
            }
            fallback_buses.append(turno_bus)

        if fallback_buses:
            print(f"  [COBERTURA] Creados {len(fallback_buses)} turnos de rescate por bus sin conductor")
            turnos.extend(fallback_buses)

    return turnos
