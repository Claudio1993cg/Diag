from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from diagramador_optimizado.core.domain.logistica import GestorDeLogistica
from diagramador_optimizado.utils.time_utils import formatear_hora


def resolver_union_conductores(
    config: Dict[str, Any],
    turnos_conductores: List[Dict[str, Any]],
    metadata_tareas: Dict[Any, Dict[str, Any]],
    viajes_comerciales: List[Dict[str, Any]],
    gestor: GestorDeLogistica,
    verbose: bool = False,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    FASE 3: Unión de Conductores usando Grafos
    
    Esta fase toma los turnos de conductores generados en la Fase 2 y los une
    cuando es posible, reduciendo el número total de conductores necesarios.
    
    Lógica:
    1. Construye un grafo donde cada nodo es un turno de conductor
    2. Crea arcos entre turnos que pueden unirse (respetando límites de jornada,
       tiempos de desplazamiento, y otras restricciones)
    3. Resuelve un problema de agrupación para minimizar el número de conductores
    4. Retorna los turnos unidos y el estado del proceso
    
    Args:
        config: Configuración del sistema
        turnos_conductores: Lista de turnos de conductores de la Fase 2
        metadata_tareas: Metadata de las tareas (viajes)
        viajes_comerciales: Lista de viajes comerciales
        gestor: Instancia de GestorDeLogistica
        verbose: Si es True, muestra información detallada
        
    Returns:
        Tupla (turnos_unidos, estado) donde:
        - turnos_unidos: Lista de turnos unidos (cada turno puede contener
          múltiples turnos originales)
        - estado: String describiendo el estado del proceso
    """
    
    # Fase 3 siempre se ejecuta (ya no es opcional)
    fase_3_config = config.get("fase_3_union_conductores", {})
    
    print("\n" + "=" * 80)
    print("--- FASE 3: Unión de Conductores (Optimización con Grafos) ---")
    print("=" * 80)
    
    limite_jornada = gestor.limite_jornada
    tiempo_descanso_minimo = gestor.tiempo_descanso_minimo()
    max_turnos_por_conductor = fase_3_config.get("max_turnos_por_conductor", 3)  # Cambiado a 3 por defecto, puede ser hasta 4
    permitir_cambio_linea = fase_3_config.get("permitir_cambio_linea", True)
    
    print(f"Límite de jornada: {limite_jornada} min")
    print(f"Tiempo de descanso mínimo: {tiempo_descanso_minimo} min")
    print(f"Máximo turnos por conductor: {max_turnos_por_conductor}")
    print(f"Permitir cambio de línea: {permitir_cambio_linea}")
    print(f"Total turnos a procesar: {len(turnos_conductores)}")
    
    if len(turnos_conductores) <= 1:
        print("No hay suficientes turnos para unir. Saltando Fase 3.")
        return turnos_conductores, "No hay suficientes turnos para unir"
    
    # Construir mapa de viajes para acceso rápido
    mapa_viajes = {viaje["id"]: viaje for viaje in viajes_comerciales}
    
    # Construir grafo de compatibilidad entre turnos
    grafo_compatibilidad = _construir_grafo_compatibilidad(
        turnos_conductores,
        metadata_tareas,
        mapa_viajes,
        gestor,
        limite_jornada,
        tiempo_descanso_minimo,
        permitir_cambio_linea,
        verbose,
    )
    
    if verbose:
        print(f"\nGrafo construido: {len(grafo_compatibilidad)} arcos de compatibilidad")
    
    # Resolver agrupación de turnos usando algoritmo greedy
    turnos_unidos = _agrupar_turnos_greedy(
        turnos_conductores,
        grafo_compatibilidad,
        max_turnos_por_conductor,
        limite_jornada,
        gestor,
        metadata_tareas,
        mapa_viajes,
        verbose,
    )

    # Segunda pasada: intentar consolidar turnos resultantes respetando las mismas reglas
    turnos_unidos = _consolidar_turnos_greedy(
        turnos_unidos,
        metadata_tareas,
        mapa_viajes,
        gestor,
        limite_jornada,
        tiempo_descanso_minimo,
        permitir_cambio_linea,
        max_turnos_por_conductor,
        verbose,
    )
    
    reduccion = len(turnos_conductores) - len(turnos_unidos)
    porcentaje_reduccion = (reduccion / len(turnos_conductores) * 100) if turnos_conductores else 0
    
    print(f"\n--- RESULTADOS FASE 3 ---")
    print(f"Turnos originales: {len(turnos_conductores)}")
    print(f"Turnos después de unión: {len(turnos_unidos)}")
    print(f"Reducción: {reduccion} conductores ({porcentaje_reduccion:.1f}%)")
    print("=" * 80)
    
    estado = f"Fase 3 completada: {reduccion} conductores reducidos ({porcentaje_reduccion:.1f}%)"
    return turnos_unidos, estado


def _consolidar_turnos_greedy(
    turnos: List[Dict[str, Any]],
    metadata_tareas: Dict[Any, Dict[str, Any]],
    mapa_viajes: Dict[Any, Dict[str, Any]],
    gestor: GestorDeLogistica,
    limite_jornada: int,
    tiempo_descanso_minimo: int,
    permitir_cambio_linea: bool,
    max_turnos_por_conductor: int,
    verbose: bool,
) -> List[Dict[str, Any]]:
    def _primer_meta(turno: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tareas = turno.get("tareas_con_bus", [])
        if not tareas:
            return None
        return metadata_tareas.get(tareas[0][0])

    def _ultima_meta(turno: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tareas = turno.get("tareas_con_bus", [])
        if not tareas:
            return None
        return metadata_tareas.get(tareas[-1][0])

    cambios = True
    turnos_actuales = turnos[:]
    while cambios:
        cambios = False
        turnos_actuales.sort(key=lambda t: t.get("inicio", 0))
        for i in range(len(turnos_actuales)):
            turno_i = turnos_actuales[i]
            meta_ultima_i = _ultima_meta(turno_i)
            if not meta_ultima_i:
                continue
            for j in range(i + 1, len(turnos_actuales)):
                turno_j = turnos_actuales[j]
                meta_primera_j = _primer_meta(turno_j)
                if not meta_primera_j:
                    continue
                if turno_j["inicio"] < turno_i["fin"]:
                    continue

                total_turnos = len(turno_i.get("turnos_base", [])) + len(turno_j.get("turnos_base", []))
                if total_turnos == 0:
                    total_turnos = 2
                if total_turnos > max_turnos_por_conductor:
                    continue

                compatibilidad = _evaluar_compatibilidad_turnos(
                    turno_i,
                    turno_j,
                    meta_ultima_i,
                    meta_primera_j,
                    mapa_viajes,
                    gestor,
                    limite_jornada,
                    tiempo_descanso_minimo,
                    permitir_cambio_linea,
                    turno_i["fin"],
                    turno_j["inicio"],
                )
                if not compatibilidad["puede_unirse"]:
                    continue

                # REGLA DURA: la jornada total combinada no puede superar el límite
                inicio_grupo = min(turno_i["inicio"], turno_j["inicio"])
                fin_grupo = max(turno_i["fin"], turno_j["fin"])
                duracion_span = fin_grupo - inicio_grupo
                if duracion_span < 0:
                    duracion_span += 1440
                if duracion_span > limite_jornada:
                    if verbose:
                        print(
                            f"  [Fase 3] No se consolidan turnos {turno_i.get('id')} + {turno_j.get('id')}: "
                            f"duración combinada {duracion_span} > límite {limite_jornada}"
                        )
                    continue

                buses_i = {bus_idx for _, bus_idx in turno_i.get("tareas_con_bus", [])}
                buses_j = {bus_idx for _, bus_idx in turno_j.get("tareas_con_bus", [])}
                cambios_bus_merge = max(turno_i.get("cambios_bus", 0), turno_j.get("cambios_bus", 0))
                if buses_i != buses_j:
                    cambios_bus_merge += 1
                turno_nuevo = {
                    "id": min(turno_i.get("id", 0), turno_j.get("id", 0)),
                    "tareas_con_bus": turno_i.get("tareas_con_bus", []) + turno_j.get("tareas_con_bus", []),
                    "inicio": min(turno_i["inicio"], turno_j["inicio"]),
                    "fin": max(turno_i["fin"], turno_j["fin"]),
                    "duracion": max(1, (max(turno_i["fin"], turno_j["fin"]) - min(turno_i["inicio"], turno_j["inicio"]))),
                    "cambios_bus": cambios_bus_merge,
                    "turnos_base": (turno_i.get("turnos_base", [turno_i.get("id")]) + turno_j.get("turnos_base", [turno_j.get("id")])),
                    "deposito_inicio": turno_i.get("deposito_inicio") or turno_j.get("deposito_inicio"),
                }
                if verbose:
                    print(
                        f"  [Fase 3] Consolidando turnos {turno_i.get('id')} + {turno_j.get('id')} -> {turno_nuevo.get('id')}"
                    )
                turnos_actuales.pop(j)
                turnos_actuales.pop(i)
                turnos_actuales.append(turno_nuevo)
                cambios = True
                break
            if cambios:
                break

    return turnos_actuales


def _construir_grafo_compatibilidad(
    turnos: List[Dict[str, Any]],
    metadata_tareas: Dict[Any, Dict[str, Any]],
    mapa_viajes: Dict[Any, Dict[str, Any]],
    gestor: GestorDeLogistica,
    limite_jornada: int,
    tiempo_descanso_minimo: int,
    permitir_cambio_linea: bool,
    verbose: bool,
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    """
    Construye un grafo de compatibilidad entre turnos.
    
    Un arco (i, j) existe si el turno i puede unirse con el turno j
    (es decir, un conductor puede realizar ambos turnos en secuencia).
    
    Returns:
        Diccionario donde las claves son tuplas (i, j) y los valores son
        diccionarios con información sobre la compatibilidad (tiempo_transicion, etc.)
    """
    grafo: Dict[Tuple[int, int], Dict[str, Any]] = {}
    n = len(turnos)

    # Ordenar índices de turnos por hora de inicio para evaluar compatibilidad en orden temporal,
    # independientemente del orden en el que fueron generados en Fase 2.
    indices_ordenados = sorted(range(n), key=lambda idx: turnos[idx]["inicio"])
    
    for pos_i in range(len(indices_ordenados)):
        i = indices_ordenados[pos_i]
        turno_i = turnos[i]
        tareas_i = turno_i.get("tareas_con_bus", [])
        if not tareas_i:
            continue
        
        # Obtener última tarea del turno i
        ultima_tarea_id_i, _ = tareas_i[-1]
        meta_ultima_i = metadata_tareas.get(ultima_tarea_id_i)
        if not meta_ultima_i:
            continue
        
        fin_turno_i = turno_i["fin"]
        
        for pos_j in range(pos_i + 1, len(indices_ordenados)):
            j = indices_ordenados[pos_j]
            turno_j = turnos[j]
            tareas_j = turno_j.get("tareas_con_bus", [])
            if not tareas_j:
                continue
            
            # Obtener primera tarea del turno j
            primera_tarea_id_j, _ = tareas_j[0]
            meta_primera_j = metadata_tareas.get(primera_tarea_id_j)
            if not meta_primera_j:
                continue
            
            inicio_turno_j = turno_j["inicio"]
            
            # Verificar si los turnos pueden unirse
            compatibilidad = _evaluar_compatibilidad_turnos(
                turno_i,
                turno_j,
                meta_ultima_i,
                meta_primera_j,
                mapa_viajes,
                gestor,
                limite_jornada,
                tiempo_descanso_minimo,
                permitir_cambio_linea,
                fin_turno_i,
                inicio_turno_j,
            )
            
            if compatibilidad["puede_unirse"]:
                grafo[(i, j)] = compatibilidad
                
                # También crear arco inverso si es bidireccional
                # (aunque normalmente solo unimos turnos en orden temporal)
                if compatibilidad.get("bidireccional", False):
                    compatibilidad_inversa = compatibilidad.copy()
                    compatibilidad_inversa["tiempo_transicion"] = compatibilidad.get(
                        "tiempo_transicion_inverso", compatibilidad["tiempo_transicion"]
                    )
                    grafo[(j, i)] = compatibilidad_inversa
    
    return grafo


def _evaluar_compatibilidad_turnos(
    turno_i: Dict[str, Any],
    turno_j: Dict[str, Any],
    meta_ultima_i: Dict[str, Any],
    meta_primera_j: Dict[str, Any],
    mapa_viajes: Dict[Any, Dict[str, Any]],
    gestor: GestorDeLogistica,
    limite_jornada: int,
    tiempo_descanso_minimo: int,
    permitir_cambio_linea: bool,
    fin_turno_i: int,
    inicio_turno_j: int,
) -> Dict[str, Any]:
    """
    Evalúa si dos turnos pueden unirse.
    
    Returns:
        Diccionario con información sobre la compatibilidad:
        - puede_unirse: bool
        - tiempo_transicion: int (minutos)
        - duracion_total: int (minutos si se unen)
        - razon: str (razón si no pueden unirse)
    """
    resultado = {
        "puede_unirse": False,
        "tiempo_transicion": 0,
        "duracion_total": 0,
        "duracion_i": 0,
        "duracion_j": 0,
        "razon": "",
    }
    
    buses_i = {bus_idx for _, bus_idx in turno_i.get("tareas_con_bus", [])}
    buses_j = {bus_idx for _, bus_idx in turno_j.get("tareas_con_bus", [])}
    mismo_bus = buses_i == buses_j and len(buses_i) == 1
    cambio_de_bus = not mismo_bus

    if cambio_de_bus:
        # Cambio de bus: solo permitido si están en el mismo grupo de línea
        lineas_i = _obtener_lineas_turno(turno_i, mapa_viajes)
        lineas_j = _obtener_lineas_turno(turno_j, mapa_viajes)
        mismas_lineas_o_mismo_grupo = False
        if lineas_i and lineas_j:
            for li in lineas_i:
                for lj in lineas_j:
                    if gestor.pueden_interlinear(li, lj):
                        mismas_lineas_o_mismo_grupo = True
                        break
                if mismas_lineas_o_mismo_grupo:
                    break
        if not mismas_lineas_o_mismo_grupo:
            resultado["razon"] = f"Cambio de bus solo permitido dentro del mismo grupo de línea ({lineas_i} vs {lineas_j})"
            return resultado

    # REGLA 1: Verificar si hay tiempo suficiente entre turnos
    tiempo_entre_turnos = inicio_turno_j - fin_turno_i
    if tiempo_entre_turnos < tiempo_descanso_minimo:
        resultado["razon"] = f"Tiempo insuficiente entre turnos ({tiempo_entre_turnos} < {tiempo_descanso_minimo})"
        return resultado
    
    # REGLA 2: Verificar si la duración total no excede el límite de jornada
    duracion_i = turno_i["duracion"]
    duracion_j = turno_j["duracion"]
    
    # Calcular tiempo de transición (desplazamiento del conductor) entre el
    # último viaje del turno i y el primer viaje del turno j.
    origen_fin_i = meta_ultima_i["viaje"]["destino"]
    origen_inicio_j = meta_primera_j["viaje"]["origen"]
    tiempo_transicion: Optional[int] = None

    if cambio_de_bus:
        # CAMBIO DE BUS: solo conexión vía depósito (conductor termina en depósito, descansa, inicia desde depósito)
        deposito = gestor.deposito_base
        ok_fin, fin_pago_i, det_fin = gestor.get_fin_turno_conductor(meta_ultima_i, devolver_detalle=True)
        ok_ini, inicio_pago_j, det_ini = gestor.get_inicio_turno_conductor(meta_primera_j, devolver_detalle=True)
        if ok_fin and ok_ini and str(det_fin.get("destino")) == str(deposito) and str(det_ini.get("origen")) == str(deposito):
            if inicio_pago_j >= fin_pago_i and inicio_pago_j - fin_pago_i >= tiempo_descanso_minimo:
                tiempo_transicion = inicio_turno_j - fin_turno_i
                if tiempo_transicion < 0:
                    tiempo_transicion += 1440
        if tiempo_transicion is None:
            resultado["razon"] = "Cambio de bus requiere conexión desde depósito (FnS en depósito, InS desde depósito)"
            return resultado
    else:
        # Mismo bus: buscar cualquier conexión válida
        # 1) Intentar primero con desplazamiento de conductor (desplazamientos habilitados)
        habilitado, tiempo_despl = gestor.buscar_info_desplazamiento(
            origen_fin_i,
            origen_inicio_j,
            fin_turno_i,
        )
        if habilitado and tiempo_despl is not None:
            tiempo_transicion = tiempo_despl

        # 2) Si no hay desplazamiento, intentar con vacío (el conductor viaja en un bus)
        if tiempo_transicion is None:
            tiempo_vacio, _ = gestor.buscar_tiempo_vacio(
                origen_fin_i,
                origen_inicio_j,
                fin_turno_i,
            )
            if tiempo_vacio is not None:
                tiempo_transicion = tiempo_vacio

        # 3) Fallback especial: ambos turnos terminan e inician en el depósito base.
        if tiempo_transicion is None:
            deposito = gestor.deposito_base
            ok_fin, fin_pago_i, det_fin = gestor.get_fin_turno_conductor(
                meta_ultima_i, devolver_detalle=True
            )
            ok_ini, inicio_pago_j, det_ini = gestor.get_inicio_turno_conductor(
                meta_primera_j, devolver_detalle=True
            )
            if ok_fin and ok_ini:
                if (
                    str(det_fin.get("destino")) == str(deposito)
                    and str(det_ini.get("origen")) == str(deposito)
                    and inicio_pago_j >= fin_pago_i
                    and inicio_pago_j - fin_pago_i >= tiempo_descanso_minimo
                ):
                    tiempo_transicion = inicio_turno_j - fin_turno_i
                    if tiempo_transicion < 0:
                        tiempo_transicion += 1440

    if tiempo_transicion is None:
        resultado["razon"] = f"No hay conexión entre {origen_fin_i} y {origen_inicio_j}"
        return resultado
    
    # REGLA CRÍTICA: Verificar reglas de parada si el último viaje del turno i y el primer viaje del turno j
    # están en el mismo nodo (el conductor puede estar esperando en ese nodo)
    viaje_ultimo_i = meta_ultima_i["viaje"]
    viaje_primero_j = meta_primera_j["viaje"]
    
    # Si el destino del último viaje del turno i es el mismo que el origen del primer viaje del turno j,
    # verificar reglas de parada
    if viaje_ultimo_i["destino"] == viaje_primero_j["origen"]:
        # Calcular tiempo de espera en el nodo
        tiempo_espera = viaje_primero_j["inicio"] - viaje_ultimo_i["fin"]
        if tiempo_espera < 0:
            tiempo_espera += 1440  # Cruzar medianoche
        
        # Obtener reglas de parada para ese nodo
        paradas_config = gestor.config.get("paradas", {})
        regla_parada = paradas_config.get(str(viaje_ultimo_i["destino"]).upper(), {})
        
        if regla_parada:
            parada_min = regla_parada.get("min", 0)
            parada_max = regla_parada.get("max", 1440)
            
            # REGLA ESTRICTA: Verificar que el tiempo de espera respete el máximo de parada
            if tiempo_espera < parada_min:
                resultado["razon"] = f"Tiempo de espera insuficiente para parada mínima ({tiempo_espera} < {parada_min})"
                return resultado
            elif tiempo_espera > parada_max:
                resultado["razon"] = f"Tiempo de espera excede máximo de parada ({tiempo_espera} > {parada_max})"
                return resultado
    
    # Calcular duración total incluyendo transición
    duracion_total = duracion_i + tiempo_transicion + duracion_j
    
    if duracion_total > limite_jornada:
        resultado["razon"] = f"Duración total excede límite ({duracion_total} > {limite_jornada})"
        return resultado
    
    # REGLA 3: Verificar cambio de línea (si no está permitido)
    if not permitir_cambio_linea:
        lineas_i = _obtener_lineas_turno(turno_i, mapa_viajes)
        lineas_j = _obtener_lineas_turno(turno_j, mapa_viajes)
        if lineas_i and lineas_j and not lineas_i.intersection(lineas_j):
            resultado["razon"] = f"Cambio de línea no permitido ({lineas_i} -> {lineas_j})"
            return resultado
    
    # Si pasa todas las reglas, pueden unirse
    resultado["puede_unirse"] = True
    resultado["tiempo_transicion"] = tiempo_transicion
    resultado["duracion_total"] = duracion_total
    # Guardar duraciones individuales para priorizar la unión de turnos cortos
    resultado["duracion_i"] = duracion_i
    resultado["duracion_j"] = duracion_j
    
    # REGLA DE IMPRODUCTIVIDAD: Idealmente, el tiempo de transición debe ser < 45 minutos
    # Esto representa el tiempo entre que el conductor deja un bus y toma otro
    tiempo_improductividad_maximo = 45  # minutos
    resultado["improductividad_ideal"] = tiempo_transicion < tiempo_improductividad_maximo
    resultado["tiempo_improductividad"] = tiempo_transicion
    
    return resultado


def _obtener_lineas_turno(
    turno: Dict[str, Any],
    mapa_viajes: Dict[Any, Dict[str, Any]],
) -> Set[str]:
    """Obtiene el conjunto de líneas únicas de un turno."""
    lineas = set()
    for tarea_id, _ in turno.get("tareas_con_bus", []):
        if tarea_id in mapa_viajes:
            linea = mapa_viajes[tarea_id].get("linea", "")
            if linea:
                lineas.add(linea)
    return lineas


def _agrupar_turnos_greedy(
    turnos: List[Dict[str, Any]],
    grafo: Dict[Tuple[int, int], Dict[str, Any]],
    max_turnos_por_conductor: int,
    limite_jornada: int,
    gestor: GestorDeLogistica,
    metadata_tareas: Dict[Any, Dict[str, Any]],
    mapa_viajes: Dict[Any, Dict[str, Any]],
    verbose: bool,
) -> List[Dict[str, Any]]:
    """
    Agrupa turnos usando un algoritmo greedy.
    
    Estrategia:
    1. Ordenar arcos por "beneficio" (duración total, tiempo de transición, etc.)
    2. Iterativamente unir turnos que maximicen la reducción de conductores
    3. Respetar límites (max_turnos_por_conductor, limite_jornada)
    """
    n = len(turnos)
    turnos_unidos: List[Dict[str, Any]] = []
    turnos_usados: Set[int] = set()
    
    # Crear lista de arcos ordenados por prioridad
    # Prioridad:
    #   1) Improductividad ideal (< 45 min) primero (False antes que True para ordenar correctamente)
    #   2) Turnos más cortos primero (max(duracion_i, duracion_j) mínimo)
    #   3) Menor duración total si se unen
    #   4) Menor tiempo de transición (improductividad)
    arcos_ordenados = sorted(
        grafo.items(),
        key=lambda x: (
            not x[1].get("improductividad_ideal", False),  # False (no ideal) va primero, True (ideal) después
            max(x[1].get("duracion_i", float("inf")), x[1].get("duracion_j", float("inf"))),
            x[1].get("duracion_total", float("inf")),
            x[1].get("tiempo_transicion", float("inf")),
        ),
    )
    
    # Diccionario para rastrear qué turnos están en qué grupo
    grupos: Dict[int, List[int]] = {}
    
    for (i, j), info in arcos_ordenados:
        # Verificar que ninguno de los turnos ya esté usado
        if i in turnos_usados or j in turnos_usados:
            continue
        
        # Verificar límite de turnos por conductor (número de turnos base en el grupo)
        grupo_i = grupos.get(i, [i])
        grupo_j = grupos.get(j, [j])
        nuevo_grupo = grupo_i + grupo_j
        if len(nuevo_grupo) > max_turnos_por_conductor:
            continue
        
        # REGLA CRÍTICA: Validar reglas de parada entre todos los turnos del grupo
        # Ordenar turnos por inicio para validar en secuencia
        turnos_grupo_ordenados = sorted(nuevo_grupo, key=lambda idx: turnos[idx]["inicio"])
        
        # Validar reglas de parada entre turnos consecutivos
        for k in range(len(turnos_grupo_ordenados) - 1):
            idx_turno_k = turnos_grupo_ordenados[k]
            idx_turno_k1 = turnos_grupo_ordenados[k + 1]
            
            turno_k = turnos[idx_turno_k]
            turno_k1 = turnos[idx_turno_k1]
            
            # Obtener última tarea del turno k y primera del turno k+1
            tareas_k = turno_k.get("tareas_con_bus", [])
            tareas_k1 = turno_k1.get("tareas_con_bus", [])
            
            if not tareas_k or not tareas_k1:
                # Si no hay tareas, no podemos validar reglas de parada
                # Continuar con el siguiente par de turnos
                continue
            
            ultima_tarea_id_k, _ = tareas_k[-1]
            primera_tarea_id_k1, _ = tareas_k1[0]
            
            meta_ultima_k = metadata_tareas.get(ultima_tarea_id_k)
            meta_primera_k1 = metadata_tareas.get(primera_tarea_id_k1)
            
            if not meta_ultima_k or not meta_primera_k1:
                # Si no hay metadatos, no podemos validar reglas de parada
                # Continuar con el siguiente par de turnos
                continue
            
            viaje_ultimo_k = meta_ultima_k["viaje"]
            viaje_primero_k1 = meta_primera_k1["viaje"]
            
            # Si el destino del último viaje del turno k es el mismo que el origen del primer viaje del turno k+1,
            # verificar reglas de parada
            if viaje_ultimo_k["destino"] == viaje_primero_k1["origen"]:
                tiempo_espera = viaje_primero_k1["inicio"] - viaje_ultimo_k["fin"]
                if tiempo_espera < 0:
                    tiempo_espera += 1440  # Cruzar medianoche
                
                # Obtener reglas de parada para ese nodo
                paradas_config = gestor.config.get("paradas", {})
                regla_parada = paradas_config.get(str(viaje_ultimo_k["destino"]).upper(), {})
                
                if regla_parada:
                    parada_min = regla_parada.get("min", 0)
                    parada_max = regla_parada.get("max", 1440)
                    
                    # REGLA ESTRICTA: Verificar que el tiempo de espera respete el máximo de parada
                    if tiempo_espera < parada_min:
                        if verbose:
                            print(
                                f"  [Fase 3] No se unen turnos {nuevo_grupo}: "
                                f"tiempo de espera insuficiente entre turnos {idx_turno_k} y {idx_turno_k1} "
                                f"({tiempo_espera} < {parada_min})"
                            )
                        break  # No unir estos turnos
                    elif tiempo_espera > parada_max:
                        if verbose:
                            print(
                                f"  [Fase 3] No se unen turnos {nuevo_grupo}: "
                                f"tiempo de espera excede máximo de parada entre turnos {idx_turno_k} y {idx_turno_k1} "
                                f"({tiempo_espera} > {parada_max})"
                            )
                        break  # No unir estos turnos
        else:
            # Si no se rompió el bucle, todas las validaciones de parada pasaron
            # REGLA DURA: la jornada total (desde el inicio del primer turno hasta el fin del último)
            # NO puede superar el límite de jornada del conductor.
            inicio_grupo = min(turnos[idx]["inicio"] for idx in nuevo_grupo)
            fin_grupo = max(turnos[idx]["fin"] for idx in nuevo_grupo)
            duracion_span = fin_grupo - inicio_grupo
            if duracion_span < 0:
                duracion_span += 1440  # Cruzar medianoche
            if duracion_span > limite_jornada:
                # No unir estos turnos porque superarían la jornada máxima permitida
                if verbose:
                    print(
                        f"  [Fase 3] No se unen turnos {nuevo_grupo}: "
                        f"duración combinada {duracion_span} > límite {limite_jornada}"
                    )
                continue
            
            # Si pasa todas las validaciones, unir los grupos
            for turno_idx in nuevo_grupo:
                grupos[turno_idx] = nuevo_grupo
                turnos_usados.add(turno_idx)
            continue  # Continuar con el siguiente arco
        
        # Si se rompió el bucle por validación de parada, no unir estos turnos
        # Continuar con el siguiente arco del bucle principal
        continue
    
    # Crear turnos unidos a partir de los grupos
    grupos_procesados: Set[Tuple[int, ...]] = set()
    
    for i in range(n):
        if i in turnos_usados:
            grupo = tuple(sorted(grupos[i]))
            if grupo in grupos_procesados:
                continue
            grupos_procesados.add(grupo)
            
            # Crear turno unido
            turno_unido = _crear_turno_unido(
                [turnos[idx] for idx in grupo],
                gestor,
                verbose,
            )
            turnos_unidos.append(turno_unido)
        else:
            # Turno no unido, agregarlo tal cual
            turnos_unidos.append(turnos[i])
    
    return turnos_unidos


def _crear_turno_unido(
    turnos_originales: List[Dict[str, Any]],
    gestor: GestorDeLogistica,
    verbose: bool,
) -> Dict[str, Any]:
    """
    Crea un turno unido a partir de múltiples turnos originales.
    
    El turno unido contiene todas las tareas de los turnos originales,
    en orden temporal, con los desplazamientos necesarios entre ellos.
    """
    if len(turnos_originales) == 1:
        return turnos_originales[0]
    
    # Ordenar turnos por inicio
    turnos_ordenados = sorted(turnos_originales, key=lambda t: t["inicio"])
    
    # Combinar todas las tareas
    todas_las_tareas: List[Tuple[Any, int]] = []
    todas_las_tareas_con_bus: List[Tuple[Any, int]] = []
    
    inicio_turno = turnos_ordenados[0]["inicio"]
    fin_turno = turnos_ordenados[-1]["fin"]
    
    for turno in turnos_ordenados:
        todas_las_tareas.extend(turno.get("tareas", []))
        todas_las_tareas_con_bus.extend(turno.get("tareas_con_bus", []))
    
    # Calcular duración total
    duracion_total = fin_turno - inicio_turno
    
    # Bus inicial = bus de la primera tarea en la lista (orden cronológico del turno unido)
    id_bus_inicial = todas_las_tareas_con_bus[0][1] if todas_las_tareas_con_bus else turnos_ordenados[0].get("id_bus", 0)
    
    # Contar cambios de bus
    buses_unicos = {bus_idx for _, bus_idx in todas_las_tareas_con_bus}
    cambios_bus = max(0, len(buses_unicos) - 1)
    
    # Obtener depósito de inicio del primer turno
    deposito_inicio = turnos_ordenados[0].get("deposito_inicio")
    if not deposito_inicio:
        # Si no tiene depósito, usar el depósito base del gestor
        deposito_inicio = gestor.deposito_base
    
    turno_unido = {
        "id_bus": id_bus_inicial,
        "tareas": todas_las_tareas,
        "tareas_con_bus": todas_las_tareas_con_bus,
        "inicio": inicio_turno,
        "fin": fin_turno,
        "duracion": duracion_total,
        "cambios_bus": cambios_bus,
        "deposito_inicio": deposito_inicio,  # CRÍTICO: Asegurar que tenga depósito de inicio
        "turnos_originales": [i for i in range(len(turnos_originales))],  # IDs originales
        "es_turno_unido": True,
    }
    
    if verbose:
        print(f"  Turno unido creado: {len(turnos_originales)} turnos -> 1 conductor")
        print(f"    Inicio: {formatear_hora(inicio_turno)}, Fin: {formatear_hora(fin_turno)}")
        print(f"    Duración: {duracion_total} min, Cambios de bus: {cambios_bus}")
    
    return turno_unido

