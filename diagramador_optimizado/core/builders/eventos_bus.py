"""
Construccion de eventos de bus desde bloques (Fase 1).
Genera InS, Vacio, Comercial, Parada, Recarga, FnS.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from diagramador_optimizado.core.domain.logistica import GestorDeLogistica
from diagramador_optimizado.core.builders.preparacion import destino_es_deposito
from diagramador_optimizado.core.domain.tipos_vehiculo import ParametrosElectricos


def _log_verbose(mensaje: str, verbose: bool) -> None:
    if verbose:
        print(f"[CONSTRUCCION] {mensaje}")

def _obtener_parametros_electricos(
    tipo_bus: Optional[str],
    gestor: Optional[GestorDeLogistica],
) -> Optional[ParametrosElectricos]:
    if not tipo_bus or gestor is None or not hasattr(gestor, "obtener_tipo_bus"):
        return None
    config_tipo = gestor.obtener_tipo_bus(tipo_bus)
    if config_tipo and config_tipo.es_electrico:
        return config_tipo.parametros_electricos
    return None


def _consumo_estimado_evento(
    evento: Dict[str, Any],
    parametros: Optional[ParametrosElectricos],
) -> float:
    if parametros is None:
        return 0.0
    kilometros = evento.get("kilometros", 0) or 0
    if kilometros <= 0:
        return 0.0
    linea = evento.get("linea")
    consumo_linea = parametros.obtener_consumo_linea(linea) if linea else None
    clave_arco = None
    if evento.get("origen") and evento.get("destino"):
        clave_arco = f"{evento['origen']}_{evento['destino']}"
    consumo_arco = parametros.obtener_consumo_arco(clave_arco) if clave_arco else None
    factor = consumo_linea or consumo_arco or parametros.consumo_pct_por_km
    return kilometros * factor


def _aplicar_consumo_evento(
    evento: Dict[str, Any],
    parametros: Optional[ParametrosElectricos],
    bateria_actual: Optional[float],
    verbose: bool,
    contexto: str,
    autonomia_km: Optional[float] = None,
) -> Optional[float]:
    if parametros is None or bateria_actual is None:
        return bateria_actual
    consumo_total = _consumo_estimado_evento(evento, parametros)
    if consumo_total <= 0:
        evento["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
        return bateria_actual
    bateria_actual = max(0.0, bateria_actual - consumo_total)
    evento["consumo"] = round(consumo_total, 2)
    evento["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
    if autonomia_km:
        evento["autonomia"] = round(autonomia_km * (bateria_actual / 100.0), 1)
    
    # REGLA CRÍTICA: Nunca debe circular con menos del mínimo para circular
    minimo_circular = parametros.minimo_para_circular_pct
    if bateria_actual < minimo_circular:
        if verbose:
            _log_verbose(
                f"ERROR: Batería por debajo del mínimo ({contexto}): {evento.get('desc', evento.get('evento'))} -> {bateria_actual:.1f}% < {minimo_circular}%",
                True,
            )
        # No permitir que circule con menos del mínimo
        # En un caso real, esto debería forzar una recarga inmediata o detener el bus
        # Por ahora, solo registramos el error
    elif bateria_actual <= minimo_circular + 5.0 and verbose:
        _log_verbose(
            f"ADVERTENCIA: Batería cerca del mínimo ({contexto}): {evento.get('desc', evento.get('evento'))} -> {bateria_actual:.1f}%",
            True,
        )
    return bateria_actual


def _consumo_proyectado_restante(
    bloque: List[Dict[str, Any]],
    indice_inicio: int,
    parametros: Optional[ParametrosElectricos],
    max_eventos: int = 2,
) -> float:
    """
    Proyecta el consumo de los siguientes viajes comerciales en el bloque.
    
    Args:
        bloque: Lista de viajes comerciales
        indice_inicio: Índice del viaje desde el cual proyectar (0-based)
        parametros: Parámetros eléctricos del bus
        max_eventos: Número máximo de viajes a proyectar (default: 2)
    
    Returns:
        Consumo total proyectado en porcentaje de batería
    """
    if parametros is None or indice_inicio >= len(bloque):
        return 0.0
    consumo = 0.0
    tomados = 0
    for idx in range(indice_inicio, len(bloque)):
        viaje = bloque[idx]
        consumo += _consumo_estimado_evento(
            {
                "kilometros": viaje.get("kilometros", 0),
                "linea": viaje.get("linea"),
                "origen": viaje.get("origen"),
                "destino": viaje.get("destino"),
            },
            parametros,
        )
        tomados += 1
        if tomados >= max_eventos:
            break
    return consumo


def _construir_cache_vacio(gestor: GestorDeLogistica):
    """
    Caché liviano para tiempos de vacíos dentro de la construcción de eventos.
    """
    @lru_cache(maxsize=30000)
    def _cached(origen: str, destino: str, referencia: int):
        try:
            referencia_int = int(referencia)
        except Exception:
            referencia_int = 0
        return gestor.buscar_tiempo_vacio(origen, destino, referencia_int)

    return _cached


def _calcular_recarga_disponible(
    parametros: Optional[ParametrosElectricos],
    bateria_actual: Optional[float],
    inicio_minimo: int,
    fin_maximo: int,
    bateria_objetivo: Optional[float] = None,
) -> Optional[Tuple[int, int, float]]:
    """
    Calcula la ventana de recarga disponible respetando:
    - El tiempo mínimo de recarga configurado (SIEMPRE)
    - La ventana horaria de recarga permitida (SIEMPRE)
    - El objetivo de batería deseado
    
    REGLA CRÍTICA: La ventana de recarga debe cumplirse SIEMPRE.
    Si no hay tiempo suficiente dentro de la ventana, no se puede recargar.
    """
    if parametros is None or bateria_actual is None:
        return None
    if fin_maximo <= inicio_minimo:
        return None
    if bateria_objetivo is None:
        bateria_objetivo = 100.0
    
    # REGLA: La ventana horaria de recarga debe cumplirse SIEMPRE
    # Solo se puede recargar dentro de la ventana configurada
    ventana_inicio = parametros.ventana_recarga.inicio
    ventana_fin = parametros.ventana_recarga.fin
    
    # Verificar que haya intersección entre la ventana disponible y la ventana de recarga
    inicio_disponible = max(inicio_minimo, ventana_inicio)
    fin_disponible = min(fin_maximo, ventana_fin)
    
    # Si no hay intersección, no se puede recargar
    if fin_disponible <= inicio_disponible:
        return None
    
    # Asegurar que haya al menos el tiempo mínimo de recarga disponible
    tiempo_minimo = parametros.tiempo_minimo_recarga
    if fin_disponible - inicio_disponible < tiempo_minimo:
        return None
    
    # Calcular la duración de recarga necesaria para alcanzar el objetivo
    delta_bateria_necesario = bateria_objetivo - bateria_actual
    if delta_bateria_necesario <= 0:
        return None
    
    # Calcular tiempo necesario para recargar
    tiempo_necesario = math.ceil(delta_bateria_necesario / parametros.tasa_recarga_pct_por_min)
    
    # Usar el máximo entre tiempo mínimo y tiempo necesario
    tiempo_recarga = max(tiempo_minimo, tiempo_necesario)
    
    # Verificar que haya suficiente tiempo disponible dentro de la ventana
    if fin_disponible - inicio_disponible < tiempo_recarga:
        # Ajustar el tiempo de recarga al disponible, pero nunca menos del mínimo
        tiempo_recarga = fin_disponible - inicio_disponible
        if tiempo_recarga < tiempo_minimo:
            return None
    
    # Calcular batería final con el tiempo de recarga disponible
    delta = tiempo_recarga * parametros.tasa_recarga_pct_por_min
    bateria_final = min(100.0, bateria_actual + delta)
    
    if bateria_final <= bateria_actual:
        return None
    
    # Ajustar fin para que coincida con el tiempo de recarga calculado
    fin_recarga = inicio_disponible + tiempo_recarga
    
    return inicio_disponible, fin_recarga, bateria_final


def _agregar_evento_recarga(
    eventos: List[Dict[str, Any]],
    deposito: str,
    inicio: int,
    fin: int,
    gestor: GestorDeLogistica,
    bateria_inicial: Optional[float] = None,
    bateria_final: Optional[float] = None,
    tipo_bus: Optional[str] = None,
) -> None:
    """
    Registra una ventana de recarga para buses eléctricos aprovechando la data del gestor.
    """
    if fin <= inicio:
        return

    # Asegurar que deposito sea un string
    if not isinstance(deposito, str):
        deposito = str(deposito) if deposito else gestor.deposito_base

    porcentaje_texto: Optional[str] = None
    if bateria_inicial is not None or bateria_final is not None:
        porcentaje_texto = f"{bateria_inicial or 0:.0f}% -> {bateria_final or bateria_inicial or 0:.0f}%"

    posicion_recarga = None
    if hasattr(gestor, "posiciones_recarga_en_deposito"):
        posicion_recarga = gestor.posiciones_recarga_en_deposito(deposito)

    eventos.append(
        {
            "evento": "Recarga",
            "origen": deposito,
            "destino": deposito,
            "inicio": inicio,
            "fin": fin,
            "kilometros": 0,
            "desc": f"Recarga en {deposito}",
            "porcentaje_bateria": porcentaje_texto,
            "posicion_recarga": posicion_recarga,
            "tipo_bus": tipo_bus,
        }
    )


def _requiere_recarga(
    parametros: Optional[Any],
    bateria_actual: Optional[float],
    consumo_proyectado: float,
) -> bool:
    """Indica si se requiere recarga antes del próximo consumo."""
    if parametros is None or bateria_actual is None:
        return False
    minimo = getattr(parametros, "minimo_para_circular_pct", 30.0)
    bateria_despues = bateria_actual - (consumo_proyectado or 0)
    return bateria_actual < minimo or bateria_despues < minimo


def _planificar_recarga_si_requiere(
    eventos: List[Dict[str, Any]],
    gestor: GestorDeLogistica,
    parametros_electricos: Any,
    bateria_actual: Optional[float],
    bus_id: Optional[int] = None,
    tipo_bus: Optional[str] = None,
    destino_actual: Optional[str] = None,
    inicio_disponible: int = 0,
    fin_disponible: int = 0,
    contexto: str = "",
    verbose: bool = False,
    consumo_proyectado: float = 0.0,
    autonomia_km: Optional[float] = None,
) -> Optional[float]:
    """
    Intenta planificar una recarga si la batería lo requiere.
    Retorna la batería actualizada o la original si no se planificó recarga.
    """
    from diagramador_optimizado.core.builders.recarga import _buscar_oportunidad_recarga

    if not _requiere_recarga(parametros_electricos, bateria_actual, consumo_proyectado):
        return bateria_actual
    if not destino_actual or fin_disponible <= inicio_disponible:
        return bateria_actual

    tiempo_disponible = fin_disponible - inicio_disponible
    ev_recarga = _buscar_oportunidad_recarga(
        destino_actual,
        destino_actual,
        fin_disponible,
        bateria_actual or 0.0,
        parametros_electricos,
        gestor,
        gestor.buscar_tiempo_vacio,
        tiempo_disponible,
        verbose,
    )
    if ev_recarga:
        bateria_final = ev_recarga.get("bateria_final", bateria_actual)
        vacio_ida = ev_recarga.get("vacio_ida")
        vacio_vuelta = ev_recarga.get("vacio_vuelta")
        if vacio_ida:
            v = dict(vacio_ida)
            v["evento"] = "Vacio"
            v["tipo_bus"] = tipo_bus
            eventos.append(v)
        _agregar_evento_recarga(
            eventos,
            ev_recarga.get("destino", gestor.deposito_base),
            ev_recarga.get("inicio", inicio_disponible),
            ev_recarga.get("fin", fin_disponible),
            gestor,
            bateria_inicial=bateria_actual,
            bateria_final=bateria_final,
            tipo_bus=tipo_bus,
        )
        if vacio_vuelta:
            v = dict(vacio_vuelta)
            v["evento"] = "Vacio"
            v["tipo_bus"] = tipo_bus
            eventos.append(v)
        return bateria_final
    return bateria_actual


def _normalizar_eventos_bus(eventos: List[Dict[str, Any]], verbose: bool = False, gestor: Optional[Any] = None) -> List[Dict[str, Any]]:
    """
    Normaliza la secuencia de eventos (de bus o conductor):
    - Elimina duplicados exactos.
    - Resuelve solapamientos priorizando Comerciales > Recargas > Vacíos > Paradas.
    """
    if not eventos:
        return []

    # Ordenar eventos por inicio y prioridad de tipo
    def _prioridad_tipo(ev):
        tipo = ev.get("evento", "")
        if tipo == "Comercial": return 0
        if tipo == "Recarga": return 1
        # CRÍTICO: Vacíos que conectan con FnS deben tener alta prioridad para no ser recortados
        # Verificar si hay un FnS después de este vacío
        tipo_str = str(tipo).strip().upper()
        if tipo_str == "VACIO":
            # Los vacíos que conectan con FnS deben tener prioridad similar a InS/FnS
            # Esto se manejará en la lógica de solapamiento
            return 2
        if tipo == "Desplazamiento": return 3
        if tipo == "InS" or tipo == "FnS": return 4
        return 5  # Parada y otros

    eventos_ordenados = sorted(
        eventos,
        key=lambda x: (x.get("inicio", 0), _prioridad_tipo(x), x.get("fin", 0))
    )

    eventos_salida: List[Dict[str, Any]] = []
    vacios_conductor_1_en_normalizacion = [ev for ev in eventos_ordenados if ev.get("conductor") == 1 and str(ev.get("evento", "")).strip().upper() == "VACIO"]
    if vacios_conductor_1_en_normalizacion:
        print(f"[DEBUG Conductor 1] Vacíos en normalización: {len(vacios_conductor_1_en_normalizacion)}")
        for ev_vacio in vacios_conductor_1_en_normalizacion:
            print(f"  Vacio: {ev_vacio.get('origen')} -> {ev_vacio.get('destino')} ({ev_vacio.get('inicio')} - {ev_vacio.get('fin')})")
    for actual in eventos_ordenados:
        if not eventos_salida:
            eventos_salida.append(actual)
            continue

        anterior = eventos_salida[-1]
        
        # Deduplicar exactos
        if (actual.get("evento") == anterior.get("evento") and
            actual.get("inicio") == anterior.get("inicio") and
            actual.get("fin") == anterior.get("fin") and
            actual.get("origen") == anterior.get("origen") and
            actual.get("destino") == anterior.get("destino")):
            continue

        inicio_act = actual.get("inicio", 0)
        fin_act = actual.get("fin", inicio_act)
        inicio_ant = anterior.get("inicio", 0)
        fin_ant = anterior.get("fin", inicio_ant)

        if inicio_act < fin_ant:
            # Hay solapamiento
            tipo_act = str(actual.get("evento", "")).strip().upper()
            tipo_ant = str(anterior.get("evento", "")).strip().upper()
            if actual.get("conductor") == 1 or anterior.get("conductor") == 1:
                print(f"[DEBUG Conductor 1] Solapamiento detectado: actual={tipo_act} ({inicio_act}-{fin_act}), anterior={tipo_ant} ({inicio_ant}-{fin_ant})")
            
            # CRÍTICO: InS/FnS nunca deben eliminarse ni recortarse - siempre agregarlos
            if tipo_act in ["INS", "FNS"] or tipo_ant in ["INS", "FNS"]:
                # Si alguno de los eventos es InS/FnS, agregarlo sin modificar
                eventos_salida.append(actual)
                continue
            
            # CRÍTICO: Vacíos que conectan con FnS no deben recortarse
            # Si el evento actual es un Vacio, verificar si hay un FnS después del mismo conductor
            if tipo_act == "VACIO":
                # Verificar si hay un FnS después de este vacío (en los eventos restantes)
                idx_actual = eventos_ordenados.index(actual)
                eventos_restantes = eventos_ordenados[idx_actual + 1:]
                conductor_vacio = actual.get("conductor")
                destino_vacio = actual.get("destino", "")
                hay_fns_despues = any(str(ev.get("evento", "")).strip().upper() == "FNS" and 
                                     ev.get("conductor") == conductor_vacio for ev in eventos_restantes)
                # CRÍTICO: Preservar vacíos que terminan en un depósito (probablemente conectan con FnS)
                # Obtener lista de depósitos del gestor si está disponible, o usar lista vacía
                nombres_depositos_vacio = []
                if gestor and hasattr(gestor, "_nombres_depositos"):
                    nombres_depositos_vacio = gestor._nombres_depositos()
                elif gestor:
                    nombres_depositos_vacio = [gestor.deposito_base] if hasattr(gestor, "deposito_base") else []
                es_vacio_a_deposito = destino_vacio and destino_vacio in nombres_depositos_vacio
                if conductor_vacio == 1:
                    print(f"[DEBUG Conductor 1] Verificando vacío: destino={destino_vacio}, depósitos={nombres_depositos_vacio}, es_vacio_a_deposito={es_vacio_a_deposito}, hay_fns_despues={hay_fns_despues}, gestor_disponible={gestor is not None}")
                if hay_fns_despues or es_vacio_a_deposito:
                    # Hay un FnS después del mismo conductor o el vacío termina en depósito - no recortar el vacío
                    if conductor_vacio == 1:
                        print(f"[DEBUG Conductor 1] Preservando vacío: hay_fns_despues={hay_fns_despues}, es_vacio_a_deposito={es_vacio_a_deposito}")
                    eventos_salida.append(actual)
                    continue
            # CRÍTICO: Si el evento anterior es un Vacio que conecta con FnS, no recortarlo
            if tipo_ant == "VACIO":
                idx_anterior = eventos_ordenados.index(anterior)
                eventos_restantes_ant = eventos_ordenados[idx_anterior + 1:]
                conductor_vacio_ant = anterior.get("conductor")
                destino_vacio_ant = anterior.get("destino", "")
                hay_fns_despues_ant = any(str(ev.get("evento", "")).strip().upper() == "FNS" and 
                                          ev.get("conductor") == conductor_vacio_ant for ev in eventos_restantes_ant)
                # También verificar si el vacío anterior termina en un depósito
                nombres_depositos_vacio_ant = []
                if gestor and hasattr(gestor, "_nombres_depositos"):
                    nombres_depositos_vacio_ant = gestor._nombres_depositos()
                elif gestor:
                    nombres_depositos_vacio_ant = [gestor.deposito_base] if hasattr(gestor, "deposito_base") else []
                es_vacio_ant_a_deposito = destino_vacio_ant and destino_vacio_ant in nombres_depositos_vacio_ant
                if hay_fns_despues_ant or es_vacio_ant_a_deposito:
                    # El anterior es un vacío que conecta con FnS - no recortarlo, agregar el actual sin modificar
                    if conductor_vacio_ant == 1:
                        print(f"[DEBUG Conductor 1] Preservando vacío anterior: hay_fns_despues={hay_fns_despues_ant}, es_vacio_a_deposito={es_vacio_ant_a_deposito}, destino={destino_vacio_ant}")
                    eventos_salida.append(actual)
                    continue

            # CRÍTICO: Antes de recortar, verificar si alguno de los eventos es un vacío que termina en depósito
            # Estos vacíos no deben recortarse porque conectan con FnS
            nombres_depositos_check = []
            if gestor and hasattr(gestor, "_nombres_depositos"):
                nombres_depositos_check = gestor._nombres_depositos()
            elif gestor:
                nombres_depositos_check = [gestor.deposito_base] if hasattr(gestor, "deposito_base") else []
            
            es_vacio_act_a_deposito = tipo_act == "VACIO" and actual.get("destino", "") in nombres_depositos_check
            es_vacio_ant_a_deposito = tipo_ant == "VACIO" and anterior.get("destino", "") in nombres_depositos_check
            
            if (actual.get("conductor") == 1 or anterior.get("conductor") == 1) and (tipo_act == "VACIO" or tipo_ant == "VACIO"):
                print(f"[DEBUG Conductor 1] Verificando preservación: tipo_act={tipo_act}, tipo_ant={tipo_ant}, "
                      f"destino_act={actual.get('destino')}, destino_ant={anterior.get('destino')}, "
                      f"depósitos={nombres_depositos_check}, es_vacio_act={es_vacio_act_a_deposito}, es_vacio_ant={es_vacio_ant_a_deposito}")
            
            if es_vacio_act_a_deposito:
                # El actual es un vacío que termina en depósito - preservarlo sin recortar
                if actual.get("conductor") == 1:
                    print(f"[DEBUG Conductor 1] Preservando vacío actual (termina en depósito)")
                eventos_salida.append(actual)
                continue
            if es_vacio_ant_a_deposito:
                # El anterior es un vacío que termina en depósito - preservarlo SIN RECORTAR
                # Agregar el actual sin modificar (puede solaparse, pero el vacío tiene prioridad)
                if anterior.get("conductor") == 1:
                    print(f"[DEBUG Conductor 1] Preservando vacío anterior (termina en depósito) SIN RECORTAR")
                # NO recortar el anterior - mantenerlo completo
                eventos_salida.append(actual)
                continue
            
            if _prioridad_tipo(actual) < _prioridad_tipo(anterior):
                # El actual tiene más prioridad. Recortar o eliminar el anterior.
                # CRÍTICO: Si el anterior es un vacío que termina en depósito, NO recortarlo
                if not es_vacio_ant_a_deposito:
                    anterior["fin"] = inicio_act
                    if anterior["fin"] <= anterior["inicio"]:
                        eventos_salida.pop()
                        # Re-evaluar contra el nuevo "anterior"
                        if eventos_salida:
                            # Recursión simple para re-chequear contra el anterior del anterior
                            temp_list = eventos_salida + [actual]
                            eventos_salida = _normalizar_eventos_bus(temp_list, verbose, gestor)
                            continue
                eventos_salida.append(actual)
            else:
                # El anterior tiene más prioridad o igual. Recortar el actual.
                if fin_act <= fin_ant:
                    # El actual está contenido en el anterior, omitir.
                    continue
                actual["inicio"] = fin_ant
                if actual["fin"] > actual["inicio"]:
                    eventos_salida.append(actual)
        else:
            eventos_salida.append(actual)

    return eventos_salida


def _ordenar_eventos_para_normalizar(eventos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _prioridad(ev: Dict[str, Any]) -> int:
        return 1 if ev.get("evento") == "Parada" else 0

    return sorted(
        eventos,
        key=lambda ev: (
            ev.get("inicio", 0),
            _prioridad(ev),
            ev.get("fin", ev.get("inicio", 0)),
            ev.get("evento", ""),
        ),
    )


def _normalizar_eventos_por_clave(
    eventos: List[Dict[str, Any]],
    clave_func,
    verbose: bool = False,
    gestor: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    grupos: Dict[Any, List[Dict[str, Any]]] = {}
    for ev in eventos:
        clave = clave_func(ev)
        grupos.setdefault(clave, []).append(ev)

    resultado: List[Dict[str, Any]] = []
    for clave in sorted(grupos.keys(), key=lambda k: str(k)):
        eventos_ordenados = _ordenar_eventos_para_normalizar(grupos[clave])
        resultado.extend(_normalizar_eventos_bus(eventos_ordenados, verbose, gestor))

    return resultado


def _buscar_tiempo_vacio_con_respaldo(
    gestor: GestorDeLogistica,
    origen: str,
    destino: str,
    minutos_actuales: int,
    verbose: bool = False,
    contexto: str = "",
    buscar_vacio_fn=None,
) -> Tuple[Optional[int], int]:
    buscar = buscar_vacio_fn or gestor.buscar_tiempo_vacio
    tiempo, km = buscar(origen, destino, minutos_actuales)
    if tiempo is None or (tiempo <= 1 and (km or 0) > 0 and origen != destino):
        tiempo_rev, km_rev = buscar(destino, origen, minutos_actuales)
        if tiempo_rev is not None and tiempo_rev > 1:
            if verbose:
                _log_verbose(
                    f"Tiempo vacío anómalo {origen}->{destino} ({tiempo} min). "
                    f"Usando respaldo {destino}->{origen} ({tiempo_rev} min). {contexto}",
                    verbose,
                )
            return tiempo_rev, km_rev
    return tiempo, km




def _construir_eventos_bus(
    bloques_bus: List[List[Dict[str, Any]]],
    gestor: GestorDeLogistica,
    verbose: bool = False,
) -> List[List[Dict[str, Any]]]:
    """
    Genera la secuencia detallada de eventos para cada bus reutilizando
    exclusivamente la lógica centralizada del GestorDeLogistica.
    """
    # Obtener todos los depósitos disponibles (para buscar el mejor)
    nombres_depositos = gestor._nombres_depositos() if hasattr(gestor, "_nombres_depositos") else [gestor.deposito_base]
    deposito_base = gestor.deposito_base  # Usar como fallback
    eventos_por_bus: List[List[Dict[str, Any]]] = []
    buscar_vacio = _construir_cache_vacio(gestor)
    parametros_cache: Dict[Optional[str], Optional[ParametrosElectricos]] = {}
    config_tipo_cache: Dict[Optional[str], Any] = {}

    for bloque in bloques_bus:
        eventos: List[Dict[str, Any]] = []
        if not bloque:
            eventos_por_bus.append(eventos)
            continue

        # Obtener el primer viaje comercial (no evento de recarga) para determinar tipo de bus
        primer_viaje_comercial = None
        for item in bloque:
            if item.get("evento") != "recarga" and item.get("evento") != "vacio":
                primer_viaje_comercial = item
                break
        
        if not primer_viaje_comercial:
            # Bloque sin viajes comerciales (solo eventos), usar el primer elemento
            primer_viaje_comercial = bloque[0] if bloque else None
        
        tipo_bloque = primer_viaje_comercial.get("tipo_bus") if primer_viaje_comercial else None
        if tipo_bloque not in parametros_cache:
            parametros_cache[tipo_bloque] = _obtener_parametros_electricos(tipo_bloque, gestor)
        parametros_electricos = parametros_cache[tipo_bloque]
        bateria_actual = parametros_electricos.carga_inicial_pct if parametros_electricos else None
        if tipo_bloque not in config_tipo_cache:
            config_tipo_cache[tipo_bloque] = (
                gestor.obtener_tipo_bus(tipo_bloque)
                if hasattr(gestor, "obtener_tipo_bus") and tipo_bloque
                else None
            )
        config_tipo = config_tipo_cache[tipo_bloque]
        autonomia_tipo = config_tipo.autonomia_km if config_tipo else None
        contexto_bloque = f"bus_{tipo_bloque or 'sin_tipo'}"

        primero = primer_viaje_comercial if primer_viaje_comercial else bloque[0]
        
        # IMPORTANTE: Verificar si el bloque ya tiene un depósito asignado (desde Fase 1)
        deposito_preasignado = primero.get("deposito_asignado")
        nombres_depositos_gestor = gestor._nombres_depositos() if hasattr(gestor, "_nombres_depositos") else [gestor.deposito_base]
        if not nombres_depositos_gestor:
            nombres_depositos_gestor = [deposito_base]
        
        # Si hay un depósito preasignado y es válido, usarlo; si no, buscar el mejor
        if deposito_preasignado and deposito_preasignado in nombres_depositos_gestor:
            mejor_deposito_inicio = deposito_preasignado
            t_vacio_ini, km_vacio_ini = buscar_vacio(mejor_deposito_inicio, primero["origen"], primero["inicio"])
            if verbose and len(nombres_depositos_gestor) > 1:
                print(f"      [DEPOSITO PREASIGNADO] Usando depósito {mejor_deposito_inicio} (asignado en Fase 1)")
        else:
            # Buscar el mejor depósito (más cercano) para el primer viaje
            mejor_deposito_inicio = deposito_base
            mejor_tiempo_vacio = None
            todos_tiempos = {}  # Para logging
            
            for dep in nombres_depositos_gestor:
                t_vacio, km_vacio = buscar_vacio(dep, primero["origen"], primero["inicio"])
                if t_vacio is not None:
                    todos_tiempos[dep] = t_vacio
                    if mejor_tiempo_vacio is None or t_vacio < mejor_tiempo_vacio:
                        mejor_tiempo_vacio = t_vacio
                        mejor_deposito_inicio = dep
                elif verbose:
                    print(f"      [ADV] Depósito {dep}: Sin conexión de vacío hacia {primero['origen']}")
            
            # Logging detallado cuando hay múltiples depósitos
            if len(nombres_depositos_gestor) > 1:
                print(f"      [DEPOSITOS] Depósitos considerados para viaje {primero.get('id', 'N/A')} desde {primero['origen']}: {sorted(nombres_depositos_gestor)}")
                if todos_tiempos:
                    tiempos_str = ", ".join([f"{dep}: {t}min" for dep, t in sorted(todos_tiempos.items(), key=lambda x: x[1])])
                    print(f"      [TIEMPOS] Tiempos de vacío encontrados: {tiempos_str}")
                print(f"      [SELECCIONADO] Depósito: {mejor_deposito_inicio} (tiempo vacío: {mejor_tiempo_vacio} min)")
            
            t_vacio_ini, km_vacio_ini = buscar_vacio(mejor_deposito_inicio, primero["origen"], primero["inicio"])
        
        # Usar el depósito encontrado para todo el bloque
        deposito_usado = mejor_deposito_inicio
        t_vacio_ini = t_vacio_ini or 0
        hora_salida_deposito = primero["inicio"] - t_vacio_ini
        # REGLA: InS (tiempo de toma) tiene duración de 15 minutos
        tiempo_toma = gestor.tiempo_toma
        inicio_toma = hora_salida_deposito - tiempo_toma
        eventos.append(
            {
                "evento": "InS",
                "origen": mejor_deposito_inicio,
                "destino": mejor_deposito_inicio,
                "inicio": inicio_toma,
                "fin": hora_salida_deposito,
                "kilometros": 0,
                "desc": f"Inicio de servicio bus (Toma de {tiempo_toma} min)",
                "tipo_bus": tipo_bloque,
            }
        )
        if bateria_actual is not None:
            eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
        eventos.append(
            {
                "evento": "Vacio",
                "origen": mejor_deposito_inicio,
                "destino": primero["origen"],
                "inicio": hora_salida_deposito,
                "fin": primero["inicio"],
                "kilometros": km_vacio_ini,
                "desc": f"Vacio {mejor_deposito_inicio}->{primero['origen']}",
                "tipo_bus": tipo_bloque,
            }
        )
        bateria_actual = _aplicar_consumo_evento(
            eventos[-1],
            parametros_electricos,
            bateria_actual,
            verbose,
            contexto_bloque,
            autonomia_tipo,
        )

        for idx, viaje in enumerate(bloque):
            # Verificar si el elemento actual es un evento de recarga ya planificado en Fase 1
            if viaje.get("evento") == "recarga":
                # Evento de recarga ya planificado en Fase 1, agregarlo directamente
                eventos.append(viaje)
                bateria_actual = viaje.get("bateria_final", bateria_actual)
                if bateria_actual is not None:
                    eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                continue
            
            # Verificar si el elemento es un vacío (recarga o standby) ya planificado en Fase 1
            if viaje.get("evento") == "vacio" and (
                "recarga" in viaje.get("desc", "").lower() or "standby" in viaje.get("desc", "").lower()
            ):
                # Asegurar parada de duración parada_max antes del vacío standby si hay gap
                ultimo_ev = eventos[-1] if eventos else None
                gap = (viaje["inicio"] - ultimo_ev["fin"]) if (ultimo_ev and viaje.get("inicio") and ultimo_ev.get("fin")) else 0
                if gap > 0:
                    nodo = ultimo_ev.get("destino") or ultimo_ev.get("origen")
                    regla_parada = gestor.paradas_dict.get(str(nodo).upper()) if hasattr(gestor, "paradas_dict") else None
                    parada_max = regla_parada.get("max", 1440) if regla_parada else 1440
                    tiempo_parada = min(gap, parada_max)
                    if tiempo_parada > 0:
                        eventos.append(
                            {
                                "evento": "Parada",
                                "origen": nodo,
                                "destino": nodo,
                                "inicio": ultimo_ev["fin"],
                                "fin": ultimo_ev["fin"] + tiempo_parada,
                                "kilometros": 0,
                                "desc": f"Parada en {nodo} ({tiempo_parada} min, máximo permitido antes de standby)",
                                "tipo_bus": tipo_bloque,
                            }
                        )
                eventos.append(viaje)
                bateria_actual = _aplicar_consumo_evento(
                    eventos[-1],
                    parametros_electricos,
                    bateria_actual,
                    verbose,
                    contexto_bloque,
                    autonomia_tipo,
                )
                continue
            
            # REGLA CRÍTICA: Proyectar consumo ANTES de cada viaje comercial
            # Si detecta que después del viaje quedará por debajo del mínimo, FORZAR recarga
            # NOTA: Esta lógica solo se ejecuta si NO hay eventos de recarga ya planificados en Fase 1
            if parametros_electricos and bateria_actual is not None:
                minimo_circular = parametros_electricos.minimo_para_circular_pct
                
                # Proyectar consumo del viaje actual
                consumo_viaje = _consumo_estimado_evento(
                    {
                        "kilometros": viaje.get("kilometros", 0),
                        "linea": viaje.get("linea"),
                        "origen": viaje.get("origen"),
                        "destino": viaje.get("destino"),
                    },
                    parametros_electricos,
                )
                bateria_despues_viaje = bateria_actual - consumo_viaje
                
                # Proyectar consumo de los siguientes viajes para detectar problemas futuros
                # Proyectar desde idx+1 porque el viaje actual es idx
                consumo_futuro = _consumo_proyectado_restante(
                    bloque,
                    idx + 1,  # Proyectar desde el siguiente viaje
                    parametros_electricos,
                    max_eventos=3,  # Proyectar 3 viajes adelante
                )
                bateria_proyectada_futuro = bateria_despues_viaje - consumo_futuro
                
                # REGLA: Si la batería DESPUÉS del viaje quedará por debajo del mínimo,
                # DEBE forzar recarga ANTES del viaje. NO se puede evitar el viaje, debe buscar solución.
                # Solo forzar recarga si realmente quedará por debajo del mínimo después del viaje actual
                requiere_recarga_forzada = (
                    bateria_actual < minimo_circular 
                    or bateria_despues_viaje < minimo_circular
                )
                
                if requiere_recarga_forzada:
                    _log_verbose(
                        f"FORZANDO RECARGA OBLIGATORIA: Batería actual {bateria_actual:.1f}%, "
                        f"después del viaje {bateria_despues_viaje:.1f}%, "
                        f"proyectada futuro {bateria_proyectada_futuro:.1f}% < mínimo {minimo_circular}%. "
                        f"Insertando recarga ANTES del viaje {viaje.get('id') or viaje.get('_tmp_id') or '?'}",
                        verbose,
                    )
                    
                    # Calcular tiempo disponible antes del viaje y origen actual
                    ultimo_evento = eventos[-1] if eventos else None
                    tiempo_disponible_antes = viaje["inicio"] - (ultimo_evento["fin"] if ultimo_evento else 0)
                    origen_actual = ultimo_evento["destino"] if ultimo_evento else gestor.deposito_base
                    tiempo_actual = ultimo_evento["fin"] if ultimo_evento else 0
                    
                    # Buscar depósito más cercano que permita recarga
                    deposito_recarga = None
                    mejor_tiempo_ida = None
                    mejor_tiempo_vuelta = None
                    mejor_km_ida = None
                    mejor_km_vuelta = None
                    
                    for deposito_obj in gestor.depositos_config:
                        deposito_nombre = deposito_obj.nombre if hasattr(deposito_obj, 'nombre') else str(deposito_obj)
                        if not isinstance(deposito_nombre, str):
                            deposito_nombre = str(deposito_nombre)
                        if not gestor.permite_recarga_en_deposito(deposito_nombre):
                            continue
                        
                        # Calcular tiempo de ida al depósito desde el último evento
                        tiempo_ida, km_ida = buscar_vacio(
                            origen_actual,
                            deposito_nombre,
                            tiempo_actual
                        )
                        if tiempo_ida is None:
                            continue
                        
                        # Calcular tiempo de vuelta del depósito al origen del viaje
                        tiempo_vuelta, km_vuelta = buscar_vacio(
                            deposito_nombre,
                            viaje["origen"],
                            viaje["inicio"] - 30  # Aproximación
                        )
                        if tiempo_vuelta is None:
                            continue
                        
                        # Verificar si hay tiempo suficiente para ida + recarga mínima + vuelta
                        # Ser más flexible: aceptar hasta 30 minutos de diferencia si es necesario
                        tiempo_total_necesario = tiempo_ida + parametros_electricos.tiempo_minimo_recarga + tiempo_vuelta
                        if tiempo_total_necesario <= tiempo_disponible_antes + 30:  # Flexibilidad de 30 min
                            if mejor_tiempo_ida is None or tiempo_total_necesario < (mejor_tiempo_ida + mejor_tiempo_vuelta):
                                deposito_recarga = deposito_nombre
                                mejor_tiempo_ida = tiempo_ida
                                mejor_tiempo_vuelta = tiempo_vuelta
                                mejor_km_ida = km_ida or 0
                                mejor_km_vuelta = km_vuelta or 0
                    
                    if deposito_recarga and mejor_tiempo_ida is not None:
                        # Insertar eventos de recarga forzada
                        inicio_ida = tiempo_actual
                        llegada_deposito = inicio_ida + mejor_tiempo_ida
                        
                        # Calcular tiempo de recarga necesario para llegar al mínimo + margen
                        delta_bateria = max(0, minimo_circular + 20.0 - bateria_actual)
                        tiempo_recarga = max(
                            parametros_electricos.tiempo_minimo_recarga,
                            math.ceil(delta_bateria / parametros_electricos.tasa_recarga_pct_por_min)
                        )
                        
                        # Verificar ventana de recarga
                        recarga_info = _calcular_recarga_disponible(
                            parametros_electricos,
                            bateria_actual,
                            llegada_deposito,
                            viaje["inicio"] - mejor_tiempo_vuelta,
                            100.0,  # Objetivo: recargar completamente
                        )
                        
                        if recarga_info:
                            inicio_recarga, fin_recarga, bateria_final = recarga_info
                        else:
                            # Si no cabe en la ventana, usar el tiempo mínimo
                            inicio_recarga = llegada_deposito
                            fin_recarga = inicio_recarga + tiempo_recarga
                            bateria_final = min(100.0, bateria_actual + (tiempo_recarga * parametros_electricos.tasa_recarga_pct_por_min))
                        
                        salida_deposito = fin_recarga
                        llegada_origen_viaje = salida_deposito + mejor_tiempo_vuelta
                        
                        # Agregar eventos de recarga forzada
                        if inicio_ida < llegada_deposito:
                            eventos.append(
                                {
                                    "evento": "Vacio",
                                    "origen": origen_actual,
                                    "destino": deposito_recarga,
                                    "inicio": inicio_ida,
                                    "fin": llegada_deposito,
                                    "kilometros": mejor_km_ida,
                                    "desc": f"Vacio forzado a {deposito_recarga} (recarga obligatoria)",
                                    "tipo_bus": tipo_bloque,
                                }
                            )
                            bateria_actual = _aplicar_consumo_evento(
                                eventos[-1],
                                parametros_electricos,
                                bateria_actual,
                                verbose,
                                contexto_bloque,
                                autonomia_tipo,
                            )
                        
                        _agregar_evento_recarga(
                            eventos,
                            deposito_recarga,
                            inicio_recarga,
                            fin_recarga,
                            gestor,
                            bateria_inicial=bateria_actual,
                            bateria_final=bateria_final,
                            tipo_bus=tipo_bloque,
                        )
                        bateria_actual = bateria_final
                        
                        eventos.append(
                            {
                                "evento": "Vacio",
                                "origen": deposito_recarga,
                                "destino": viaje["origen"],
                                "inicio": salida_deposito,
                                "fin": llegada_origen_viaje,
                                "kilometros": mejor_km_vuelta,
                                "desc": f"Vacio desde {deposito_recarga} (post-recarga obligatoria)",
                                "tipo_bus": tipo_bloque,
                            }
                        )
                        bateria_actual = _aplicar_consumo_evento(
                            eventos[-1],
                            parametros_electricos,
                            bateria_actual,
                            verbose,
                            contexto_bloque,
                            autonomia_tipo,
                        )
                        
                        # Agregar parada si hay tiempo antes del viaje
                        if llegada_origen_viaje < viaje["inicio"]:
                            eventos.append(
                                {
                                    "evento": "Parada",
                                    "origen": viaje["origen"],
                                    "destino": viaje["origen"],
                                    "inicio": llegada_origen_viaje,
                                    "fin": viaje["inicio"],
                                    "kilometros": 0,
                                    "desc": f"Parada en {viaje['origen']} (post-recarga)",
                                    "tipo_bus": tipo_bloque,
                                }
                            )
                            if bateria_actual is not None:
                                eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                    else:
                        # REGLA CRÍTICA: SIEMPRE debe existir una solución
                        # Si no hay tiempo suficiente, intentar ajustar tiempos o buscar alternativas
                        _log_verbose(
                            f"ADVERTENCIA: Tiempo insuficiente para recarga antes del viaje {viaje.get('id') or viaje.get('_tmp_id') or '?'}. "
                            f"Buscando solución alternativa...",
                            verbose,
                        )
                        
                        # Estrategia alternativa: Intentar recargar en el depósito base si es diferente
                        # o ajustar el tiempo del viaje si es posible
                        deposito_base = gestor.deposito_base
                        if deposito_base != origen_actual and gestor.permite_recarga_en_deposito(deposito_base):
                            tiempo_ida_base, km_ida_base = buscar_vacio(
                                origen_actual,
                                deposito_base,
                                tiempo_actual,
                            )
                            tiempo_vuelta_base, km_vuelta_base = buscar_vacio(
                                deposito_base,
                                viaje["origen"],
                                viaje["inicio"] - 30,
                            )
                            
                            if tiempo_ida_base is not None and tiempo_vuelta_base is not None:
                                tiempo_total_base = tiempo_ida_base + parametros_electricos.tiempo_minimo_recarga + tiempo_vuelta_base
                                # Aceptar si está cerca (hasta 30 min de diferencia para ser más flexible)
                                if tiempo_total_base <= tiempo_disponible_antes + 30:
                                    deposito_recarga = deposito_base
                                    mejor_tiempo_ida = tiempo_ida_base
                                    mejor_tiempo_vuelta = tiempo_vuelta_base
                                    mejor_km_ida = km_ida_base or 0
                                    mejor_km_vuelta = km_vuelta_base or 0
                                    
                                    # Recalcular con el depósito base
                                    inicio_ida = tiempo_actual
                                    llegada_deposito = inicio_ida + mejor_tiempo_ida
                                    
                                    delta_bateria = max(0, minimo_circular + 20.0 - bateria_actual)
                                    tiempo_recarga = max(
                                        parametros_electricos.tiempo_minimo_recarga,
                                        math.ceil(delta_bateria / parametros_electricos.tasa_recarga_pct_por_min)
                                    )
                                    
                                    recarga_info = _calcular_recarga_disponible(
                                        parametros_electricos,
                                        bateria_actual,
                                        llegada_deposito,
                                        viaje["inicio"] - mejor_tiempo_vuelta,
                                        100.0,
                                    )
                                    
                                    if recarga_info:
                                        inicio_recarga, fin_recarga, bateria_final = recarga_info
                                    else:
                                        inicio_recarga = llegada_deposito
                                        fin_recarga = inicio_recarga + tiempo_recarga
                                        bateria_final = min(100.0, bateria_actual + (tiempo_recarga * parametros_electricos.tasa_recarga_pct_por_min))
                                    
                                    salida_deposito = fin_recarga
                                    llegada_origen_viaje = salida_deposito + mejor_tiempo_vuelta
                                    
                                    # Insertar eventos de recarga
                                    if inicio_ida < llegada_deposito:
                                        eventos.append(
                                            {
                                                "evento": "Vacio",
                                                "origen": origen_actual,
                                                "destino": deposito_recarga,
                                                "inicio": inicio_ida,
                                                "fin": llegada_deposito,
                                                "kilometros": mejor_km_ida,
                                                "desc": f"Vacio forzado a {deposito_recarga} (recarga obligatoria)",
                                                "tipo_bus": tipo_bloque,
                                            }
                                        )
                                        bateria_actual = _aplicar_consumo_evento(
                                            eventos[-1],
                                            parametros_electricos,
                                            bateria_actual,
                                            verbose,
                                            contexto_bloque,
                                            autonomia_tipo,
                                        )
                                    
                                    _agregar_evento_recarga(
                                        eventos,
                                        deposito_recarga,
                                        inicio_recarga,
                                        fin_recarga,
                                        gestor,
                                        bateria_inicial=bateria_actual,
                                        bateria_final=bateria_final,
                                        tipo_bus=tipo_bloque,
                                    )
                                    bateria_actual = bateria_final
                                    
                                    eventos.append(
                                        {
                                            "evento": "Vacio",
                                            "origen": deposito_recarga,
                                            "destino": viaje["origen"],
                                            "inicio": salida_deposito,
                                            "fin": llegada_origen_viaje,
                                            "kilometros": mejor_km_vuelta,
                                            "desc": f"Vacio desde {deposito_recarga} (post-recarga obligatoria)",
                                            "tipo_bus": tipo_bloque,
                                        }
                                    )
                                    bateria_actual = _aplicar_consumo_evento(
                                        eventos[-1],
                                        parametros_electricos,
                                        bateria_actual,
                                        verbose,
                                        contexto_bloque,
                                        autonomia_tipo,
                                    )
                                    
                                    if llegada_origen_viaje < viaje["inicio"]:
                                        eventos.append(
                                            {
                                                "evento": "Parada",
                                                "origen": viaje["origen"],
                                                "destino": viaje["origen"],
                                                "inicio": llegada_origen_viaje,
                                                "fin": viaje["inicio"],
                                                "kilometros": 0,
                                                "desc": f"Parada en {viaje['origen']} (post-recarga)",
                                                "tipo_bus": tipo_bloque,
                                            }
                                        )
                                        if bateria_actual is not None:
                                            eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                                    
                                    _log_verbose(
                                        f"SOLUCIÓN ENCONTRADA: Recarga forzada en {deposito_recarga} antes del viaje {viaje.get('id') or viaje.get('_tmp_id') or '?'}",
                                        verbose,
                                    )
                                    continue
                        
                        # Última estrategia: Intentar recargar lo mínimo necesario con el tiempo disponible
                        # Aceptar cualquier depósito que tenga tiempo disponible, aunque sea parcial
                        mejor_deposito_parcial = None
                        mejor_tiempo_disponible = 0
                        mejor_tiempo_ida_parcial = None
                        mejor_tiempo_vuelta_parcial = None
                        mejor_km_ida_parcial = None
                        mejor_km_vuelta_parcial = None
                        
                        for deposito_obj_parcial in gestor.depositos_config:
                            deposito_nombre_parcial = deposito_obj_parcial.nombre if hasattr(deposito_obj_parcial, 'nombre') else str(deposito_obj_parcial)
                            if not isinstance(deposito_nombre_parcial, str):
                                deposito_nombre_parcial = str(deposito_nombre_parcial)
                            if not gestor.permite_recarga_en_deposito(deposito_nombre_parcial):
                                continue
                            
                            tiempo_ida_parcial, km_ida_parcial = buscar_vacio(
                                origen_actual,
                                deposito_nombre_parcial,
                                tiempo_actual
                            )
                            if tiempo_ida_parcial is None:
                                continue
                            
                            tiempo_vuelta_parcial, km_vuelta_parcial = buscar_vacio(
                                deposito_nombre_parcial,
                                viaje["origen"],
                                viaje["inicio"] - 30
                            )
                            if tiempo_vuelta_parcial is None:
                                continue
                            
                            # Calcular tiempo disponible para recarga
                            tiempo_para_recarga = tiempo_disponible_antes - tiempo_ida_parcial - tiempo_vuelta_parcial
                            if tiempo_para_recarga > 0:
                                # Aceptar si hay al menos 10 minutos para recargar (mínimo absoluto)
                                if tiempo_para_recarga >= 10 and tiempo_para_recarga > mejor_tiempo_disponible:
                                    mejor_deposito_parcial = deposito_nombre_parcial
                                    mejor_tiempo_disponible = tiempo_para_recarga
                                    mejor_tiempo_ida_parcial = tiempo_ida_parcial
                                    mejor_tiempo_vuelta_parcial = tiempo_vuelta_parcial
                                    mejor_km_ida_parcial = km_ida_parcial or 0
                                    mejor_km_vuelta_parcial = km_vuelta_parcial or 0
                        
                        if mejor_deposito_parcial and mejor_tiempo_disponible >= 10:
                            # Recargar con el tiempo disponible (aunque sea parcial)
                            inicio_ida_parcial = tiempo_actual
                            llegada_deposito_parcial = inicio_ida_parcial + mejor_tiempo_ida_parcial
                            
                            # Calcular recarga con el tiempo disponible
                            tiempo_recarga_parcial = min(
                                mejor_tiempo_disponible,
                                parametros_electricos.tiempo_minimo_recarga
                            )
                            
                            # Calcular batería final con recarga parcial
                            delta_bateria_parcial = tiempo_recarga_parcial * parametros_electricos.tasa_recarga_pct_por_min
                            bateria_final_parcial = min(100.0, bateria_actual + delta_bateria_parcial)
                            
                            # Verificar que al menos llegue al mínimo
                            if bateria_final_parcial >= minimo_circular:
                                inicio_recarga_parcial = llegada_deposito_parcial
                                fin_recarga_parcial = inicio_recarga_parcial + tiempo_recarga_parcial
                                salida_deposito_parcial = fin_recarga_parcial
                                llegada_origen_viaje_parcial = salida_deposito_parcial + mejor_tiempo_vuelta_parcial
                                
                                # Insertar eventos de recarga parcial
                                if inicio_ida_parcial < llegada_deposito_parcial:
                                    eventos.append(
                                        {
                                            "evento": "Vacio",
                                            "origen": origen_actual,
                                            "destino": mejor_deposito_parcial,
                                            "inicio": inicio_ida_parcial,
                                            "fin": llegada_deposito_parcial,
                                            "kilometros": mejor_km_ida_parcial,
                                            "desc": f"Vacio forzado a {mejor_deposito_parcial} (recarga parcial obligatoria)",
                                            "tipo_bus": tipo_bloque,
                                        }
                                    )
                                    bateria_actual = _aplicar_consumo_evento(
                                        eventos[-1],
                                        parametros_electricos,
                                        bateria_actual,
                                        verbose,
                                        contexto_bloque,
                                        autonomia_tipo,
                                    )
                                
                                _agregar_evento_recarga(
                                    eventos,
                                    mejor_deposito_parcial,
                                    inicio_recarga_parcial,
                                    fin_recarga_parcial,
                                    gestor,
                                    bateria_inicial=bateria_actual,
                                    bateria_final=bateria_final_parcial,
                                    tipo_bus=tipo_bloque,
                                )
                                bateria_actual = bateria_final_parcial
                                
                                eventos.append(
                                    {
                                        "evento": "Vacio",
                                        "origen": mejor_deposito_parcial,
                                        "destino": viaje["origen"],
                                        "inicio": salida_deposito_parcial,
                                        "fin": llegada_origen_viaje_parcial,
                                        "kilometros": mejor_km_vuelta_parcial,
                                        "desc": f"Vacio desde {mejor_deposito_parcial} (post-recarga parcial)",
                                        "tipo_bus": tipo_bloque,
                                    }
                                )
                                bateria_actual = _aplicar_consumo_evento(
                                    eventos[-1],
                                    parametros_electricos,
                                    bateria_actual,
                                    verbose,
                                    contexto_bloque,
                                    autonomia_tipo,
                                )
                                
                                if llegada_origen_viaje_parcial < viaje["inicio"]:
                                    eventos.append(
                                        {
                                            "evento": "Parada",
                                            "origen": viaje["origen"],
                                            "destino": viaje["origen"],
                                            "inicio": llegada_origen_viaje_parcial,
                                            "fin": viaje["inicio"],
                                            "kilometros": 0,
                                            "desc": f"Parada en {viaje['origen']} (post-recarga parcial)",
                                            "tipo_bus": tipo_bloque,
                                        }
                                    )
                                    if bateria_actual is not None:
                                        eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                                
                                _log_verbose(
                                    f"SOLUCIÓN PARCIAL ENCONTRADA: Recarga parcial en {mejor_deposito_parcial} "
                                    f"({tiempo_recarga_parcial} min) antes del viaje {viaje.get('id') or viaje.get('_tmp_id') or '?'}. "
                                    f"Batería: {bateria_actual:.1f}% -> {bateria_final_parcial:.1f}%",
                                    verbose,
                                )
                                continue
                        
                        # Si aún no hay solución, es un error crítico que debe reportarse
                        _log_verbose(
                            f"ERROR CRÍTICO: No se puede forzar recarga antes del viaje {viaje.get('id') or viaje.get('_tmp_id') or '?'}. "
                            f"Batería actual: {bateria_actual:.1f}%, mínimo requerido: {minimo_circular}%. "
                            f"Tiempo disponible: {tiempo_disponible_antes} min. "
                            f"El sistema debe encontrar una solución alternativa.",
                            True,  # Siempre mostrar este error
                        )
                        # Continuar de todas formas, pero el resultado será inválido
            
            # REGLA: Parada y vacío vienen de Fase 1 - NO son comerciales, solo transcribir
            if viaje.get("evento") == "parada":
                eventos.append({
                    **viaje,
                    "evento": "Parada",
                    "tipo_bus": tipo_bloque,
                })
                continue
            if viaje.get("evento") == "vacio":
                eventos.append({
                    **viaje,
                    "evento": "Vacio",
                    "tipo_bus": tipo_bloque,
                })
                bateria_actual = _aplicar_consumo_evento(
                    eventos[-1],
                    parametros_electricos,
                    bateria_actual,
                    verbose,
                    contexto_bloque,
                    autonomia_tipo,
                )
                continue
            
            # Solo viajes comerciales del input (sin evento parada/vacio/recarga)
            config_linea = (
                gestor.obtener_configuracion_linea(viaje.get("linea"))
                if hasattr(gestor, "obtener_configuracion_linea")
                else None
            )
            frecuencia_objetivo = config_linea.frecuencia_objetivo_min if config_linea else None
            duracion_optima = config_linea.duracion_optima_min if config_linea else None
            eventos.append(
                {
                    "evento": "Comercial",
                    "origen": viaje["origen"],
                    "destino": viaje["destino"],
                    "inicio": viaje["inicio"],
                    "fin": viaje["fin"],
                    "kilometros": viaje.get("kilometros", 0),
                    "desc": viaje.get("desc", f"{viaje.get('origen', '')} -> {viaje.get('destino', '')}"),
                    "linea": viaje.get("linea", ""),
                    "tipo_bus": tipo_bloque,
                    "viaje_id": viaje.get("id") or viaje.get("_tmp_id"),
                    "viaje_inferido": viaje.get("id") or viaje.get("_tmp_id"),
                    "sentido": viaje.get("sentido"),
                    "frecuencia_objetivo": frecuencia_objetivo,
                    "duracion_optima": duracion_optima,
                    "desviacion_frecuencia": None,
                }
            )
            duracion_real = viaje["fin"] - viaje["inicio"]
            if duracion_optima is not None:
                eventos[-1]["desviacion_duracion"] = duracion_real - duracion_optima
            else:
                eventos[-1]["desviacion_duracion"] = None
            bateria_actual = _aplicar_consumo_evento(
                eventos[-1],
                parametros_electricos,
                bateria_actual,
                verbose,
                contexto_bloque,
                autonomia_tipo,
            )

            if idx == len(bloque) - 1:
                continue
            siguiente = bloque[idx + 1]
            es_conexion, detalle = gestor.evaluar_conexion_bus(viaje, siguiente, devolver_detalle=True)
            if not es_conexion:
                # REGLA CRÍTICA: NO se puede dejar un hueco entre eventos comerciales
                # SIEMPRE debe haber un evento intermedio (parada o vacío)
                motivo = detalle.get("motivo", "desconocido")
                mismo_lugar = viaje["destino"] == siguiente["origen"]
                
                # Si el siguiente viaje es desde el mismo lugar, intentar crear parada ajustada
                if mismo_lugar:
                    tiempo_disponible = siguiente["inicio"] - viaje["fin"]
                    nodo = viaje["destino"]
                    regla_parada = gestor.paradas_dict.get(nodo.upper()) if hasattr(gestor, "paradas_dict") else None
                    
                    if regla_parada:
                        parada_min = regla_parada.get("min", 0)
                        parada_max = regla_parada.get("max", 1440)
                        
                        # Si el tiempo es menor al mínimo, no se puede crear parada válida
                        # Forzar al depósito en este caso
                        if tiempo_disponible < parada_min:
                            _log_verbose(
                                f"ERROR: No se puede crear parada entre viajes {viaje.get('id') or viaje.get('_tmp_id') or '?'} y {siguiente.get('id') or siguiente.get('_tmp_id') or '?'}. "
                                f"Tiempo insuficiente: {tiempo_disponible} min < mínimo {parada_min} min. "
                                f"El bus debe ir al depósito.",
                                verbose,
                            )
                            # Continuar al código que fuerza el depósito (más abajo)
                            # No hacer continue aquí, dejar que el código continúe
                        elif tiempo_disponible > parada_max:
                            # Si excede el máximo, crear parada ajustada al máximo (el bus se queda en el lugar)
                            _log_verbose(
                                f"Parada ajustada al máximo en {nodo}: "
                                f"{tiempo_disponible} min > máximo {parada_max} min. "
                                f"Creando parada de {parada_max} min.",
                                verbose,
                            )
                            tiempo_parada = parada_max
                            
                            # Crear parada ajustada al máximo
                            eventos.append(
                                {
                                    "evento": "Parada",
                                    "origen": nodo,
                                    "destino": nodo,
                                    "inicio": viaje["fin"],
                                    "fin": viaje["fin"] + tiempo_parada,
                                    "kilometros": 0,
                                    "desc": f"Parada en {nodo} ({tiempo_parada} min, ajustada al máximo permitido)",
                                    "tipo_bus": tipo_bloque,
                                }
                            )
                            if bateria_actual is not None:
                                eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                            
                            # CRÍTICO: Verificar si hay tiempo excedente después de la parada ajustada
                            tiempo_excedente = siguiente["inicio"] - (viaje["fin"] + tiempo_parada)
                            if tiempo_excedente > 0:
                                # Hay tiempo excedente - debemos llenarlo con un evento apropiado
                                fin_parada_ajustada = viaje["fin"] + tiempo_parada
                                
                                # Si el siguiente viaje comienza en el mismo lugar, evaluar si crear parada o enviar al depósito
                                if siguiente["origen"] == nodo:
                                    # REGLA CRÍTICA: Respetar el mínimo y máximo de parada
                                    # Si el tiempo excedente excede el máximo, enviar al depósito en lugar de violar las reglas
                                    if regla_parada:
                                        parada_min = regla_parada.get("min", 0)
                                        parada_max = regla_parada.get("max", 1440)
                                        
                                        if tiempo_excedente >= parada_min and tiempo_excedente <= parada_max:
                                            # El tiempo excedente está dentro del rango - crear parada continua
                                            eventos.append(
                                                {
                                                    "evento": "Parada",
                                                    "origen": nodo,
                                                    "destino": nodo,
                                                    "inicio": fin_parada_ajustada,
                                                    "fin": siguiente["inicio"],
                                                    "kilometros": 0,
                                                    "desc": f"Parada en {nodo} (continuación hasta siguiente viaje)",
                                                    "tipo_bus": tipo_bloque,
                                                }
                                            )
                                            if bateria_actual is not None:
                                                eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                                            # CRÍTICO: Ya se crearon todos los eventos necesarios (parada ajustada, parada continua)
                                            # No continuar procesando el caso tipo_conexion == "parada" para evitar crear paradas duplicadas
                                            continue
                                        else:
                                            # tiempo_excedente < parada_min o tiempo_excedente > parada_max - enviar al depósito (standby)
                                            _log_verbose(
                                                f"Tiempo excedente ({tiempo_excedente} min) fuera del rango de parada "
                                                f"([{parada_min}, {parada_max}] min). Enviando bus al depósito.",
                                                verbose,
                                            )
                                            # Buscar el mejor depósito disponible
                                            nombres_depositos = gestor._nombres_depositos() if hasattr(gestor, "_nombres_depositos") else [gestor.deposito_base]
                                            mejor_deposito = gestor.deposito_base
                                            mejor_tiempo_vacio = None
                                            
                                            for dep in nombres_depositos:
                                                t_vacio, km_vacio = buscar_vacio(nodo, dep, fin_parada_ajustada)
                                                if t_vacio is not None:
                                                    if mejor_tiempo_vacio is None or t_vacio < mejor_tiempo_vacio:
                                                        mejor_tiempo_vacio = t_vacio
                                                        mejor_deposito = dep
                                            
                                            if mejor_tiempo_vacio is not None:
                                                llegada_deposito = fin_parada_ajustada + mejor_tiempo_vacio
                                                
                                                # Calcular tiempo de vuelta desde depósito
                                                tiempo_vacio_vuelta, km_vacio_vuelta = buscar_vacio(
                                                    mejor_deposito,
                                                    siguiente["origen"],
                                                    llegada_deposito
                                                )
                                                
                                                if tiempo_vacio_vuelta is not None:
                                                    salida_deposito = siguiente["inicio"] - tiempo_vacio_vuelta
                                                    tiempo_standby = salida_deposito - llegada_deposito
                                                    
                                                    # Crear vacío al depósito
                                                    eventos.append(
                                                        {
                                                            "evento": "Vacio",
                                                            "origen": nodo,
                                                            "destino": mejor_deposito,
                                                            "inicio": fin_parada_ajustada,
                                                            "fin": llegada_deposito,
                                                            "kilometros": 0,
                                                            "desc": f"Vacio {nodo}->{mejor_deposito} (standby - tiempo excedente excede máximo)",
                                                            "tipo_bus": tipo_bloque,
                                                        }
                                                    )
                                                    bateria_actual = _aplicar_consumo_evento(
                                                        eventos[-1],
                                                        parametros_electricos,
                                                        bateria_actual,
                                                        verbose,
                                                        contexto_bloque,
                                                        autonomia_tipo,
                                                    )
                                                    
                                                    # Crear parada en depósito (standby)
                                                    if tiempo_standby > 0:
                                                        eventos.append(
                                                            {
                                                                "evento": "Parada",
                                                                "origen": mejor_deposito,
                                                                "destino": mejor_deposito,
                                                                "inicio": llegada_deposito,
                                                                "fin": salida_deposito,
                                                                "kilometros": 0,
                                                                "desc": f"Parada en {mejor_deposito} (standby)",
                                                                "tipo_bus": tipo_bloque,
                                                            }
                                                        )
                                                        if bateria_actual is not None:
                                                            eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                                                    
                                                    # Crear vacío de vuelta desde depósito
                                                    eventos.append(
                                                        {
                                                            "evento": "Vacio",
                                                            "origen": mejor_deposito,
                                                            "destino": siguiente["origen"],
                                                            "inicio": salida_deposito,
                                                            "fin": siguiente["inicio"],
                                                            "kilometros": km_vacio_vuelta or 0,
                                                            "desc": f"Vacio {mejor_deposito}->{siguiente['origen']} (reactivación desde standby)",
                                                            "tipo_bus": tipo_bloque,
                                                        }
                                                    )
                                                    bateria_actual = _aplicar_consumo_evento(
                                                        eventos[-1],
                                                        parametros_electricos,
                                                        bateria_actual,
                                                        verbose,
                                                        contexto_bloque,
                                                        autonomia_tipo,
                                                    )
                                                    # CRÍTICO: Ya se crearon todos los eventos necesarios (parada ajustada, vacío al depósito, parada en depósito, vacío de vuelta)
                                                    # No continuar procesando el caso tipo_conexion == "parada" para evitar crear paradas duplicadas
                                                    continue
                                                else:
                                                    _log_verbose(
                                                        f"ERROR: No hay vacío de vuelta desde {mejor_deposito} a {siguiente['origen']}. "
                                                        f"NO se crea parada continua ilegal. El bus debe ir al depósito (Fase 1).",
                                                        True,
                                                    )
                                                    continue
                                            else:
                                                _log_verbose(
                                                    f"ERROR: No hay vacío desde {nodo} a ningún depósito. "
                                                    f"NO se crea parada larga ilegal. Configure vacíos en configuracion.json. "
                                                    f"El bus debe ir al depósito (Fase 1 debe agregar standby).",
                                                    True,
                                                )
                                                continue
                                    else:
                                        # Sin regla de parada - crear parada continua
                                        eventos.append(
                                            {
                                                "evento": "Parada",
                                                "origen": nodo,
                                                "destino": nodo,
                                                "inicio": fin_parada_ajustada,
                                                "fin": siguiente["inicio"],
                                                "kilometros": 0,
                                                "desc": f"Parada en {nodo} (continuación hasta siguiente viaje)",
                                                "tipo_bus": tipo_bloque,
                                            }
                                        )
                                        if bateria_actual is not None:
                                            eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                                        # CRÍTICO: Ya se crearon todos los eventos necesarios (parada ajustada, parada continua)
                                        # No continuar procesando el caso tipo_conexion == "parada" para evitar crear paradas duplicadas
                                        continue
                                else:
                                    # El siguiente viaje comienza en otro lugar - crear vacío
                                    tiempo_vacio_excedente, km_vacio_excedente = buscar_vacio(
                                        nodo,
                                        siguiente["origen"],
                                        fin_parada_ajustada
                                    )
                                    
                                    if tiempo_vacio_excedente is not None and tiempo_vacio_excedente <= tiempo_excedente:
                                        # Hay vacío disponible
                                        llegada_vacio = fin_parada_ajustada + tiempo_vacio_excedente
                                        tiempo_parada_despues_vacio = siguiente["inicio"] - llegada_vacio
                                        parada_max_dest = 60
                                        regla_parada_dest_pre = gestor.paradas_dict.get((siguiente["origen"] or "").upper()) if hasattr(gestor, "paradas_dict") else None
                                        if regla_parada_dest_pre:
                                            parada_max_dest = regla_parada_dest_pre.get("max", 60)

                                        # REGLA: Si la parada después del vacío excede parada_max, el bus NO puede quedarse en ese nodo.
                                        # Enviar al depósito (standby) y reactivar desde ahí para reutilización.
                                        if tiempo_parada_despues_vacio > parada_max_dest:
                                            nombres_dep = gestor._nombres_depositos() if hasattr(gestor, "_nombres_depositos") else [gestor.deposito_base]
                                            mejor_dep = gestor.deposito_base
                                            t_a_dep, km_a_dep = None, 0
                                            for dep in nombres_dep:
                                                tt, kk = buscar_vacio(nodo, dep, fin_parada_ajustada)
                                                if tt is not None:
                                                    t_a_dep, km_a_dep, mejor_dep = tt, kk or 0, dep
                                                    break
                                            if destino_es_deposito(nodo, gestor) or t_a_dep is None:
                                                # Ya en depósito o sin vacío a depot: standby en lugar actual
                                                salida_vacio = siguiente["inicio"] - tiempo_vacio_excedente
                                                eventos.append(
                                                    {
                                                        "evento": "Parada",
                                                        "origen": nodo,
                                                        "destino": nodo,
                                                        "inicio": fin_parada_ajustada,
                                                        "fin": salida_vacio,
                                                        "kilometros": 0,
                                                        "desc": f"Parada en {nodo} (standby hasta reactivación)",
                                                        "tipo_bus": tipo_bloque,
                                                    }
                                                )
                                                if bateria_actual is not None:
                                                    eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                                                eventos.append(
                                                    {
                                                        "evento": "Vacio",
                                                        "origen": nodo,
                                                        "destino": siguiente["origen"],
                                                        "inicio": salida_vacio,
                                                        "fin": siguiente["inicio"],
                                                        "kilometros": km_vacio_excedente or 0,
                                                        "desc": f"Vacio {nodo}->{siguiente['origen']} (reactivación standby)",
                                                        "tipo_bus": tipo_bloque,
                                                    }
                                                )
                                                bateria_actual = _aplicar_consumo_evento(
                                                    eventos[-1], parametros_electricos, bateria_actual,
                                                    verbose, contexto_bloque, autonomia_tipo,
                                                )
                                                continue
                                            # Nodo no es depósito: ir al depósito, standby, luego vacío a destino
                                            llegada_dep = fin_parada_ajustada + t_a_dep
                                            t_de_dep, km_de_dep = buscar_vacio(mejor_dep, siguiente["origen"], llegada_dep)
                                            if t_de_dep is not None:
                                                salida_dep = siguiente["inicio"] - t_de_dep
                                                eventos.append({"evento": "Vacio", "origen": nodo, "destino": mejor_dep,
                                                    "inicio": fin_parada_ajustada, "fin": llegada_dep,
                                                    "kilometros": km_a_dep or 0,
                                                    "desc": f"Vacio {nodo}->{mejor_dep} (parada max excedida, standby en depot)",
                                                    "tipo_bus": tipo_bloque})
                                                bateria_actual = _aplicar_consumo_evento(
                                                    eventos[-1], parametros_electricos, bateria_actual,
                                                    verbose, contexto_bloque, autonomia_tipo)
                                                if salida_dep > llegada_dep:
                                                    eventos.append({"evento": "Parada", "origen": mejor_dep, "destino": mejor_dep,
                                                        "inicio": llegada_dep, "fin": salida_dep, "kilometros": 0,
                                                        "desc": f"Parada en {mejor_dep} (standby para reutilización)",
                                                        "tipo_bus": tipo_bloque})
                                                    if bateria_actual is not None:
                                                        eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                                                eventos.append({"evento": "Vacio", "origen": mejor_dep, "destino": siguiente["origen"],
                                                    "inicio": salida_dep, "fin": siguiente["inicio"],
                                                    "kilometros": km_de_dep or 0,
                                                    "desc": f"Vacio {mejor_dep}->{siguiente['origen']} (reactivación desde depot)",
                                                    "tipo_bus": tipo_bloque})
                                                bateria_actual = _aplicar_consumo_evento(
                                                    eventos[-1], parametros_electricos, bateria_actual,
                                                    verbose, contexto_bloque, autonomia_tipo)
                                                continue

                                        # Caso normal: vacío inmediato
                                        eventos.append(
                                            {
                                                "evento": "Vacio",
                                                "origen": nodo,
                                                "destino": siguiente["origen"],
                                                "inicio": fin_parada_ajustada,
                                                "fin": llegada_vacio,
                                                "kilometros": km_vacio_excedente or 0,
                                                "desc": f"Vacio {nodo}->{siguiente['origen']} (después de parada ajustada)",
                                                "tipo_bus": tipo_bloque,
                                            }
                                        )
                                        bateria_actual = _aplicar_consumo_evento(
                                            eventos[-1],
                                            parametros_electricos,
                                            bateria_actual,
                                            verbose,
                                            contexto_bloque,
                                            autonomia_tipo,
                                        )
                                        
                                        # Si hay tiempo después del vacío, crear parada
                                        if tiempo_parada_despues_vacio > 0:
                                            nodo_destino = siguiente["origen"]
                                            regla_parada_dest = gestor.paradas_dict.get(nodo_destino.upper()) if hasattr(gestor, "paradas_dict") else None
                                            
                                            if regla_parada_dest:
                                                parada_min_dest = regla_parada_dest.get("min", 0)
                                                parada_max_dest = regla_parada_dest.get("max", 1440)
                                                tiempo_parada_final = max(parada_min_dest, min(tiempo_parada_despues_vacio, parada_max_dest))
                                            else:
                                                tiempo_parada_final = tiempo_parada_despues_vacio
                                            
                                            eventos.append(
                                                {
                                                    "evento": "Parada",
                                                    "origen": nodo_destino,
                                                    "destino": nodo_destino,
                                                    "inicio": llegada_vacio,
                                                    "fin": siguiente["inicio"],
                                                    "kilometros": 0,
                                                    "desc": f"Parada en {nodo_destino}",
                                                    "tipo_bus": tipo_bloque,
                                                }
                                            )
                                            if bateria_actual is not None:
                                                eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                                    else:
                                        # No hay vacío disponible - forzar vía depósito
                                        _log_verbose(
                                            f"No hay vacío directo disponible después de parada ajustada. "
                                            f"Forzando vía depósito.",
                                            verbose,
                                        )
                                        # Continuar al código que fuerza el depósito (más abajo)
                                        # No hacer continue aquí, dejar que el código continúe
                            continue
                        else:
                            # Está dentro del rango, crear parada normal
                            tiempo_parada = tiempo_disponible
                            eventos.append(
                                {
                                    "evento": "Parada",
                                    "origen": nodo,
                                    "destino": nodo,
                                    "inicio": viaje["fin"],
                                    "fin": siguiente["inicio"],
                                    "kilometros": 0,
                                    "desc": f"Parada en {nodo}",
                                    "tipo_bus": tipo_bloque,
                                }
                            )
                            if bateria_actual is not None:
                                eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                            continue
                    else:
                        # Sin regla de parada, crear parada con el tiempo disponible
                        tiempo_parada = tiempo_disponible
                        eventos.append(
                            {
                                "evento": "Parada",
                                "origen": nodo,
                                "destino": nodo,
                                "inicio": viaje["fin"],
                                "fin": siguiente["inicio"],
                                "kilometros": 0,
                                "desc": f"Parada en {nodo}",
                                "tipo_bus": tipo_bloque,
                            }
                        )
                        if bateria_actual is not None:
                            eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                        continue
                
                # Si llegamos aquí, el siguiente viaje es desde OTRO lugar o el tiempo es menor al mínimo
                # REGLA DE OPTIMIZACIÓN: Antes de forzar vía depósito, verificar si hay vacío directo disponible
                # Esto evita vacíos cruzados innecesarios (ej: LOS TILOS -> Depósito -> LA PIRAMIDE cuando existe LOS TILOS -> LA PIRAMIDE)
                _log_verbose(
                    f"Gap inválido entre viajes {viaje.get('id') or viaje.get('_tmp_id') or '?'} y {siguiente.get('id') or siguiente.get('_tmp_id') or '?'} (bus). "
                    f"Motivo: {motivo}. "
                    f"Verificando si hay vacío directo disponible antes de forzar depósito.",
                    verbose,
                )
                
                # OPTIMIZACIÓN: Verificar primero si hay vacío directo disponible
                tiempo_vacio_directo, km_vacio_directo = buscar_vacio(
                    viaje["destino"],
                    siguiente["origen"],
                    viaje["fin"]
                )
                
                if tiempo_vacio_directo is not None:
                    # Hay vacío directo disponible
                    tiempo_disponible_total = siguiente["inicio"] - viaje["fin"]
                    if tiempo_vacio_directo <= tiempo_disponible_total:
                        llegada_directo = viaje["fin"] + tiempo_vacio_directo
                        tiempo_parada_directo = siguiente["inicio"] - llegada_directo
                        parada_max_directo = 60
                        regla_directo = gestor.paradas_dict.get((siguiente["origen"] or "").upper()) if hasattr(gestor, "paradas_dict") else None
                        if regla_directo:
                            parada_max_directo = regla_directo.get("max", 60)

                        # OPTIMIZACIÓN: Si origen es depósito y la parada en destino sería muy larga, standby en depósito
                        if destino_es_deposito(viaje["destino"], gestor) and tiempo_parada_directo > parada_max_directo:
                            salida_directo = siguiente["inicio"] - tiempo_vacio_directo
                            eventos.append(
                                {
                                    "evento": "Parada",
                                    "origen": viaje["destino"],
                                    "destino": viaje["destino"],
                                    "inicio": viaje["fin"],
                                    "fin": salida_directo,
                                    "kilometros": 0,
                                    "desc": f"Parada en {viaje['destino']} (standby hasta reactivación)",
                                    "tipo_bus": tipo_bloque,
                                }
                            )
                            if bateria_actual is not None:
                                eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                            eventos.append(
                                {
                                    "evento": "Vacio",
                                    "origen": viaje["destino"],
                                    "destino": siguiente["origen"],
                                    "inicio": salida_directo,
                                    "fin": siguiente["inicio"],
                                    "kilometros": km_vacio_directo or 0,
                                    "desc": f"Vacio {viaje['destino']}->{siguiente['origen']} (reactivación standby)",
                                    "tipo_bus": tipo_bloque,
                                }
                            )
                            bateria_actual = _aplicar_consumo_evento(
                                eventos[-1],
                                parametros_electricos,
                                bateria_actual,
                                verbose,
                                contexto_bloque,
                                autonomia_tipo,
                            )
                            continue

                        _log_verbose(
                            f"OPTIMIZACIÓN: Usando vacío directo {viaje['destino']}->{siguiente['origen']} "
                            f"en lugar de depósito. Ahorra {tiempo_vacio_directo} min de viaje al depósito.",
                            verbose,
                        )
                        
                        # Crear vacío directo
                        eventos.append(
                            {
                                "evento": "Vacio",
                                "origen": viaje["destino"],
                                "destino": siguiente["origen"],
                                "inicio": viaje["fin"],
                                "fin": llegada_directo,
                                "kilometros": km_vacio_directo or 0,
                                "desc": f"Vacio {viaje['destino']}->{siguiente['origen']} (optimizado - evita depósito)",
                                "tipo_bus": tipo_bloque,
                            }
                        )
                        bateria_actual = _aplicar_consumo_evento(
                            eventos[-1],
                            parametros_electricos,
                            bateria_actual,
                            verbose,
                            contexto_bloque,
                            autonomia_tipo,
                        )
                        
                        # Crear parada después del vacío directo
                        if tiempo_parada_directo > 0:
                            nodo_destino = siguiente["origen"]
                            regla_parada_dest = gestor.paradas_dict.get(nodo_destino.upper()) if hasattr(gestor, "paradas_dict") else None
                            
                            if regla_parada_dest:
                                parada_min_dest = regla_parada_dest.get("min", 0)
                                parada_max_dest = regla_parada_dest.get("max", 1440)
                                tiempo_parada_final = max(parada_min_dest, min(tiempo_parada_directo, parada_max_dest))
                            else:
                                tiempo_parada_final = tiempo_parada_directo
                            
                            eventos.append(
                                {
                                    "evento": "Parada",
                                    "origen": siguiente["origen"],
                                    "destino": siguiente["origen"],
                                    "inicio": llegada_directo,
                                    "fin": llegada_directo + tiempo_parada_final,
                                    "kilometros": 0,
                                    "desc": f"Parada en {siguiente['origen']}",
                                    "tipo_bus": tipo_bloque,
                                }
                            )
                            if bateria_actual is not None:
                                eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                        continue
                
                # Si no hay vacío directo disponible, forzar vía depósito
                _log_verbose(
                    f"No hay vacío directo disponible. Forzando ruta vía depósito para evitar hueco.",
                    verbose,
                )
                
                # FORZAR la creación de eventos vía depósito base
                deposito_forzado = gestor.deposito_base
                tiempo_a_dep, km_a_dep = buscar_vacio(viaje["destino"], deposito_forzado, viaje["fin"])
                tiempo_de_dep, km_de_dep = buscar_vacio(
                    deposito_forzado,
                    siguiente["origen"],
                    max(siguiente["inicio"] - gestor._t_de_dep_aprox if hasattr(gestor, "_t_de_dep_aprox") else 30, 0),
                )
                
                if tiempo_a_dep is not None and tiempo_de_dep is not None:
                    # Crear eventos forzados vía depósito
                    llegada_dep = viaje["fin"] + tiempo_a_dep
                    tiempo_min_dep = getattr(gestor, "tiempo_min_deposito", 5)
                    # OPTIMIZACIÓN: Salir justo a tiempo; considerar parada mínima en destino.
                    regla_dest = gestor.paradas_dict.get((siguiente["origen"] or "").upper()) if hasattr(gestor, "paradas_dict") else None
                    parada_min_dest = regla_dest.get("min", 0) if regla_dest else 0
                    salida_justo_a_tiempo = siguiente["inicio"] - tiempo_de_dep - parada_min_dest
                    salida_minima = llegada_dep + tiempo_min_dep
                    salida_dep = max(salida_minima, salida_justo_a_tiempo)
                    llegada_destino = salida_dep + tiempo_de_dep
                    
                    # Verificar que quepa en la ventana disponible
                    if llegada_destino <= siguiente["inicio"]:
                        # Crear vacío al depósito
                        motivo_original = detalle.get("motivo_original", motivo)
                        eventos.append(
                            {
                                "evento": "Vacio",
                                "origen": viaje["destino"],
                                "destino": deposito_forzado,
                                "inicio": viaje["fin"],
                                "fin": llegada_dep,
                                "kilometros": km_a_dep or 0,
                                "desc": f"Vacio forzado {viaje['destino']}->{deposito_forzado} ({motivo_original})",
                                "tipo_bus": tipo_bloque,
                            }
                        )
                        bateria_actual = _aplicar_consumo_evento(
                            eventos[-1],
                            parametros_electricos,
                            bateria_actual,
                            verbose,
                            contexto_bloque,
                            autonomia_tipo,
                        )
                        
                        # Crear parada en depósito
                        eventos.append(
                            {
                                "evento": "Parada",
                                "origen": deposito_forzado,
                                "destino": deposito_forzado,
                                "inicio": llegada_dep,
                                "fin": salida_dep,
                                "kilometros": 0,
                                "desc": f"Parada en {deposito_forzado} (forzada por exceso de tiempo)",
                                "tipo_bus": tipo_bloque,
                            }
                        )
                        if bateria_actual is not None:
                            eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                        
                        # Crear vacío desde depósito
                        eventos.append(
                            {
                                "evento": "Vacio",
                                "origen": deposito_forzado,
                                "destino": siguiente["origen"],
                                "inicio": salida_dep,
                                "fin": llegada_destino,
                                "kilometros": km_de_dep or 0,
                                "desc": f"Vacio forzado {deposito_forzado}->{siguiente['origen']}",
                                "tipo_bus": tipo_bloque,
                            }
                        )
                        bateria_actual = _aplicar_consumo_evento(
                            eventos[-1],
                            parametros_electricos,
                            bateria_actual,
                            verbose,
                            contexto_bloque,
                            autonomia_tipo,
                        )
                        
                        # Crear parada final antes del siguiente viaje
                        tiempo_parada_final = siguiente["inicio"] - llegada_destino
                        if tiempo_parada_final > 0:
                            eventos.append(
                                {
                                    "evento": "Parada",
                                    "origen": siguiente["origen"],
                                    "destino": siguiente["origen"],
                                    "inicio": llegada_destino,
                                    "fin": siguiente["inicio"],
                                    "kilometros": 0,
                                    "desc": f"Parada en {siguiente['origen']}",
                                    "tipo_bus": tipo_bloque,
                                }
                            )
                            if bateria_actual is not None:
                                eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                        continue
                    else:
                        _log_verbose(
                            f"ERROR: No se puede forzar ruta vía depósito: tiempo insuficiente. "
                            f"Llegada destino: {llegada_destino}, siguiente inicio: {siguiente['inicio']}",
                            verbose,
                        )
                else:
                    _log_verbose(
                        f"ERROR: No se puede forzar ruta vía depósito: no hay vacíos configurados. "
                        f"Dejando gap entre viajes {viaje.get('id') or viaje.get('_tmp_id') or '?'} y {siguiente.get('id') or siguiente.get('_tmp_id') or '?'}.",
                        verbose,
                    )
                continue

            tipo_conexion = detalle.get("tipo")
            # CRÍTICO: Si es_conexion era False, ya se crearon los eventos manualmente
            # No procesar tipo_conexion == "parada" para evitar crear paradas duplicadas
            # (el continue en línea 2008 debería evitar esto, pero agregamos esta verificación por seguridad)
            if tipo_conexion == "parada":
                # REGLA CRÍTICA: Si el tipo es "parada", puede requerir ajuste al máximo si excede
                nodo_parada = viaje["destino"]
                tiempo_disponible = siguiente["inicio"] - viaje["fin"]
                regla_parada = gestor.paradas_dict.get(nodo_parada.upper()) if hasattr(gestor, "paradas_dict") else None
                
                # Verificar si requiere ajuste
                requiere_ajuste = detalle.get("requiere_ajuste", False)
                ajuste_tipo = detalle.get("ajuste_tipo")
                
                if requiere_ajuste and ajuste_tipo == "maximo":
                    # Si requiere ajuste al máximo, usar el tiempo ajustado del detalle
                    tiempo_parada = detalle.get("tiempo_parada_ajustado", tiempo_disponible)
                    tiempo_excedente = detalle.get("tiempo_excedente", 0)
                    _log_verbose(
                        f"Parada ajustada al máximo en {nodo_parada}: "
                        f"tiempo disponible {tiempo_disponible} min > máximo {regla_parada.get('max', 0) if regla_parada else 0} min. "
                        f"Usando parada de {tiempo_parada} min. Tiempo excedente: {tiempo_excedente} min.",
                        verbose,
                    )
                else:
                    # Si no requiere ajuste, el tiempo ya está validado y dentro del rango
                    tiempo_parada = tiempo_disponible
                
                # Verificación de seguridad
                if regla_parada:
                    parada_min = regla_parada.get("min", 0)
                    parada_max = regla_parada.get("max", 1440)
                    if tiempo_parada < parada_min:
                        _log_verbose(
                            f"ERROR: Tiempo de parada menor al mínimo en {nodo_parada}: "
                            f"{tiempo_parada} min < mínimo {parada_min} min. "
                            f"Esto no debería suceder si la validación en logistica.py funciona correctamente.",
                            verbose,
                        )
                    elif tiempo_parada > parada_max and not requiere_ajuste:
                        _log_verbose(
                            f"ERROR: Tiempo de parada mayor al máximo en {nodo_parada}: "
                            f"{tiempo_parada} min > máximo {parada_max} min. "
                            f"Esto no debería suceder si la validación en logistica.py funciona correctamente.",
                            verbose,
                        )
                
                planificada = False
                consumo_proximo = _consumo_proyectado_restante(
                    bloque,
                    idx + 1,
                    parametros_electricos,
                )
                if _requiere_recarga(parametros_electricos, bateria_actual, consumo_proximo):
                    bateria_actual = _planificar_recarga_si_requiere(
                        eventos,
                        gestor,
                        parametros_electricos,
                        bateria_actual,
                        bus_id=None,
                        tipo_bus=tipo_bloque,
                        destino_actual=viaje["destino"],
                        inicio_disponible=viaje["fin"],
                        fin_disponible=siguiente["inicio"],
                        contexto=contexto_bloque,
                        verbose=verbose,
                        consumo_proyectado=consumo_proximo,
                        autonomia_km=autonomia_tipo,
                    )
                    planificada = True
                
                # REGLA CRÍTICA: SIEMPRE debe agregarse la parada, incluso si se planificó recarga
                # El evento de parada SIEMPRE debe existir y ajustarse al rango min/max
                if planificada:
                    # Si ya se planificó recarga, verificar si ya existe una parada
                    ultimo_fin = eventos[-1].get("fin", viaje["fin"]) if eventos else viaje["fin"]
                    
                    # REGLA DURA: SIEMPRE debe existir un evento de parada
                    # Verificar que haya un evento de parada que cubra el tiempo requerido
                    tiempo_parada_faltante = siguiente["inicio"] - ultimo_fin
                    
                    if tiempo_parada_faltante > 0:
                        # Calcular tiempo de parada ajustado al rango
                        if regla_parada:
                            if tiempo_parada_faltante < parada_min:
                                tiempo_parada_final = parada_min
                            elif tiempo_parada_faltante > parada_max:
                                tiempo_parada_final = parada_max
                            else:
                                tiempo_parada_final = tiempo_parada_faltante
                        else:
                            tiempo_parada_final = tiempo_parada_faltante
                        
                        # Asegurar que la parada cubra desde ultimo_fin hasta el inicio del siguiente viaje
                        eventos.append(
                            {
                                "evento": "Parada",
                                "origen": nodo_parada,
                                "destino": nodo_parada,
                                "inicio": ultimo_fin,
                                "fin": ultimo_fin + tiempo_parada_final,
                                "kilometros": 0,
                                "desc": f"Parada en {nodo_parada} (ajustada a {tiempo_parada_final} min)",
                                "tipo_bus": tipo_bloque,
                            }
                        )
                        if bateria_actual is not None:
                            eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                else:
                    # REGLA DURA: Agregar parada obligatoria con tiempo ajustado al rango
                    fin_parada = viaje["fin"] + tiempo_parada
                    eventos.append(
                        {
                            "evento": "Parada",
                            "origen": nodo_parada,
                            "destino": nodo_parada,
                            "inicio": viaje["fin"],
                            "fin": fin_parada,
                            "kilometros": 0,
                            "desc": f"Parada en {nodo_parada} ({tiempo_parada} min, ajustada al rango)",
                            "tipo_bus": tipo_bloque,
                        }
                    )
                    if bateria_actual is not None:
                        eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                    
                    # CRÍTICO: Verificar si hay tiempo excedente después de la parada ajustada
                    if requiere_ajuste and ajuste_tipo == "maximo" and tiempo_excedente > 0:
                        # Hay tiempo excedente - debemos llenarlo con un evento apropiado
                        # Si el siguiente viaje comienza en el mismo lugar, evaluar si crear parada o enviar al depósito
                        if siguiente["origen"] == nodo_parada:
                            # REGLA CRÍTICA: Respetar el mínimo y máximo de parada
                            # Si el tiempo excedente excede el máximo, enviar al depósito en lugar de violar las reglas
                            if regla_parada:
                                parada_min = regla_parada.get("min", 0)
                                parada_max = regla_parada.get("max", 1440)
                                
                                if tiempo_excedente >= parada_min and tiempo_excedente <= parada_max:
                                    # El tiempo excedente está dentro del rango - crear parada continua
                                    eventos.append(
                                        {
                                            "evento": "Parada",
                                            "origen": nodo_parada,
                                            "destino": nodo_parada,
                                            "inicio": fin_parada,
                                            "fin": siguiente["inicio"],
                                            "kilometros": 0,
                                            "desc": f"Parada en {nodo_parada} (continuación hasta siguiente viaje)",
                                            "tipo_bus": tipo_bloque,
                                        }
                                    )
                                    if bateria_actual is not None:
                                        eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                                elif tiempo_excedente < parada_min:
                                    # El tiempo excedente es menor al mínimo - no se puede crear parada válida
                                    # Enviar al depósito
                                    _log_verbose(
                                        f"Tiempo excedente ({tiempo_excedente} min) fuera del rango de parada "
                                        f"([{parada_min}, {parada_max}] min). Enviando bus al depósito.",
                                        verbose,
                                    )
                                else:
                                    # tiempo_excedente < parada_min o tiempo_excedente > parada_max - enviar al depósito (standby)
                                    # Buscar el mejor depósito disponible
                                    nombres_depositos = gestor._nombres_depositos() if hasattr(gestor, "_nombres_depositos") else [gestor.deposito_base]
                                    mejor_deposito = gestor.deposito_base
                                    mejor_tiempo_vacio = None
                                    
                                    for dep in nombres_depositos:
                                        t_vacio, km_vacio = buscar_vacio(nodo_parada, dep, fin_parada)
                                        if t_vacio is not None:
                                            if mejor_tiempo_vacio is None or t_vacio < mejor_tiempo_vacio:
                                                mejor_tiempo_vacio = t_vacio
                                                mejor_deposito = dep
                                    
                                    if mejor_tiempo_vacio is not None:
                                        llegada_deposito = fin_parada + mejor_tiempo_vacio
                                        
                                        # Calcular tiempo de vuelta desde depósito
                                        tiempo_vacio_vuelta, km_vacio_vuelta = buscar_vacio(
                                            mejor_deposito,
                                            siguiente["origen"],
                                            llegada_deposito
                                        )
                                        
                                        if tiempo_vacio_vuelta is not None:
                                            salida_deposito = siguiente["inicio"] - tiempo_vacio_vuelta
                                            tiempo_standby = salida_deposito - llegada_deposito
                                            
                                            # Crear vacío al depósito
                                            eventos.append(
                                                {
                                                    "evento": "Vacio",
                                                    "origen": nodo_parada,
                                                    "destino": mejor_deposito,
                                                    "inicio": fin_parada,
                                                    "fin": llegada_deposito,
                                                    "kilometros": 0,
                                                    "desc": f"Vacio {nodo_parada}->{mejor_deposito} (standby - tiempo excedente excede máximo)",
                                                    "tipo_bus": tipo_bloque,
                                                }
                                            )
                                            bateria_actual = _aplicar_consumo_evento(
                                                eventos[-1],
                                                parametros_electricos,
                                                bateria_actual,
                                                verbose,
                                                contexto_bloque,
                                                autonomia_tipo,
                                            )
                                            
                                            # Crear parada en depósito (standby)
                                            if tiempo_standby > 0:
                                                eventos.append(
                                                    {
                                                        "evento": "Parada",
                                                        "origen": mejor_deposito,
                                                        "destino": mejor_deposito,
                                                        "inicio": llegada_deposito,
                                                        "fin": salida_deposito,
                                                        "kilometros": 0,
                                                        "desc": f"Parada en {mejor_deposito} (standby)",
                                                        "tipo_bus": tipo_bloque,
                                                    }
                                                )
                                                if bateria_actual is not None:
                                                    eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                                            
                                            # Crear vacío de vuelta desde depósito
                                            eventos.append(
                                                {
                                                    "evento": "Vacio",
                                                    "origen": mejor_deposito,
                                                    "destino": siguiente["origen"],
                                                    "inicio": salida_deposito,
                                                    "fin": siguiente["inicio"],
                                                    "kilometros": km_vacio_vuelta or 0,
                                                    "desc": f"Vacio {mejor_deposito}->{siguiente['origen']} (reactivación desde standby)",
                                                    "tipo_bus": tipo_bloque,
                                                }
                                            )
                                            bateria_actual = _aplicar_consumo_evento(
                                                eventos[-1],
                                                parametros_electricos,
                                                bateria_actual,
                                                verbose,
                                                contexto_bloque,
                                                autonomia_tipo,
                                            )
                                        else:
                                            _log_verbose(
                                                f"ERROR: No hay vacío de vuelta desde {mejor_deposito} a {siguiente['origen']}. "
                                                f"NO se crea parada larga ilegal.",
                                                True,
                                            )
                                    else:
                                        _log_verbose(
                                            f"ERROR: No hay vacío desde {nodo_parada} a ningún depósito. "
                                            f"NO se crea parada larga ilegal. Configure vacíos.",
                                            True,
                                        )
                            else:
                                # Sin regla de parada - crear parada continua
                                eventos.append(
                                    {
                                        "evento": "Parada",
                                        "origen": nodo_parada,
                                        "destino": nodo_parada,
                                        "inicio": fin_parada,
                                        "fin": siguiente["inicio"],
                                        "kilometros": 0,
                                        "desc": f"Parada en {nodo_parada} (continuación hasta siguiente viaje)",
                                        "tipo_bus": tipo_bloque,
                                    }
                                )
                                if bateria_actual is not None:
                                    eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                        else:
                            # El siguiente viaje comienza en otro lugar - crear vacío
                            tiempo_vacio_excedente, km_vacio_excedente = buscar_vacio(
                                nodo_parada,
                                siguiente["origen"],
                                fin_parada
                            )
                            
                            if tiempo_vacio_excedente is not None and tiempo_vacio_excedente <= tiempo_excedente:
                                # Hay vacío disponible
                                llegada_vacio = fin_parada + tiempo_vacio_excedente
                                tiempo_parada_despues_vacio = siguiente["inicio"] - llegada_vacio
                                parada_max_dest_2 = 60
                                regla_pd2 = gestor.paradas_dict.get((siguiente["origen"] or "").upper()) if hasattr(gestor, "paradas_dict") else None
                                if regla_pd2:
                                    parada_max_dest_2 = regla_pd2.get("max", 60)

                                # OPTIMIZACIÓN: Si estamos en depósito y la parada después sería muy larga, standby en depósito
                                if destino_es_deposito(nodo_parada, gestor) and tiempo_parada_despues_vacio > parada_max_dest_2:
                                    salida_vacio = siguiente["inicio"] - tiempo_vacio_excedente
                                    eventos.append(
                                        {
                                            "evento": "Parada",
                                            "origen": nodo_parada,
                                            "destino": nodo_parada,
                                            "inicio": fin_parada,
                                            "fin": salida_vacio,
                                            "kilometros": 0,
                                            "desc": f"Parada en {nodo_parada} (standby hasta reactivación)",
                                            "tipo_bus": tipo_bloque,
                                        }
                                    )
                                    if bateria_actual is not None:
                                        eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                                    eventos.append(
                                        {
                                            "evento": "Vacio",
                                            "origen": nodo_parada,
                                            "destino": siguiente["origen"],
                                            "inicio": salida_vacio,
                                            "fin": siguiente["inicio"],
                                            "kilometros": km_vacio_excedente or 0,
                                            "desc": f"Vacio {nodo_parada}->{siguiente['origen']} (reactivación standby)",
                                            "tipo_bus": tipo_bloque,
                                        }
                                    )
                                    bateria_actual = _aplicar_consumo_evento(
                                        eventos[-1],
                                        parametros_electricos,
                                        bateria_actual,
                                        verbose,
                                        contexto_bloque,
                                        autonomia_tipo,
                                    )
                                    continue

                                # Caso normal
                                eventos.append(
                                    {
                                        "evento": "Vacio",
                                        "origen": nodo_parada,
                                        "destino": siguiente["origen"],
                                        "inicio": fin_parada,
                                        "fin": llegada_vacio,
                                        "kilometros": km_vacio_excedente or 0,
                                        "desc": f"Vacio {nodo_parada}->{siguiente['origen']} (después de parada ajustada)",
                                        "tipo_bus": tipo_bloque,
                                    }
                                )
                                bateria_actual = _aplicar_consumo_evento(
                                    eventos[-1],
                                    parametros_electricos,
                                    bateria_actual,
                                    verbose,
                                    contexto_bloque,
                                    autonomia_tipo,
                                )
                                
                                if tiempo_parada_despues_vacio > 0:
                                    nodo_destino = siguiente["origen"]
                                    regla_parada_dest = gestor.paradas_dict.get(nodo_destino.upper()) if hasattr(gestor, "paradas_dict") else None
                                    
                                    if regla_parada_dest:
                                        parada_min_dest = regla_parada_dest.get("min", 0)
                                        parada_max_dest = regla_parada_dest.get("max", 1440)
                                        tiempo_parada_final = max(parada_min_dest, min(tiempo_parada_despues_vacio, parada_max_dest))
                                    else:
                                        tiempo_parada_final = tiempo_parada_despues_vacio
                                    
                                    eventos.append(
                                        {
                                            "evento": "Parada",
                                            "origen": nodo_destino,
                                            "destino": nodo_destino,
                                            "inicio": llegada_vacio,
                                            "fin": siguiente["inicio"],
                                            "kilometros": 0,
                                            "desc": f"Parada en {nodo_destino}",
                                            "tipo_bus": tipo_bloque,
                                        }
                                    )
                                    if bateria_actual is not None:
                                        eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                            else:
                                # No hay vacío disponible - forzar vía depósito
                                _log_verbose(
                                    f"No hay vacío directo disponible después de parada ajustada. "
                                    f"Forzando vía depósito.",
                                    verbose,
                                )
                                # Continuar al código que fuerza el depósito (más abajo)
                                # No hacer continue aquí, dejar que el código continúe
                continue

            if tipo_conexion == "vacio":
                tiempo_vacio = detalle.get("tiempo_vacio", 0)
                kilometros_vacio = detalle.get("kilometros_vacio", 0)
                llegada = viaje["fin"] + tiempo_vacio
                eventos.append(
                    {
                        "evento": "Vacio",
                        "origen": viaje["destino"],
                        "destino": siguiente["origen"],
                        "inicio": viaje["fin"],
                        "fin": llegada,
                        "kilometros": kilometros_vacio,
                        "desc": f"Vacio {viaje['destino']}->{siguiente['origen']}",
                        "tipo_bus": tipo_bloque,
                    }
                )
                bateria_actual = _aplicar_consumo_evento(
                    eventos[-1],
                    parametros_electricos,
                    bateria_actual,
                    verbose,
                    contexto_bloque,
                    autonomia_tipo,
                )
                
                # REGLA CRÍTICA: SIEMPRE debe haber una parada después de un vacío
                # antes del siguiente viaje comercial, respetando tiempos mínimos y máximos
                tiempo_disponible_parada = siguiente["inicio"] - llegada
                nodo_destino = siguiente["origen"]
                regla_parada = gestor.paradas_dict.get(nodo_destino.upper()) if hasattr(gestor, "paradas_dict") else None
                
                if regla_parada:
                    parada_min = regla_parada.get("min", 0)
                    parada_max = regla_parada.get("max", 1440)
                    
                    # Verificar que el tiempo disponible cumpla con los requisitos
                    if tiempo_disponible_parada < parada_min:
                        _log_verbose(
                            f"ERROR: Tiempo disponible para parada insuficiente en {nodo_destino}: "
                            f"{tiempo_disponible_parada} min < mínimo requerido {parada_min} min",
                            verbose,
                        )
                        # Ajustar el tiempo de llegada para cumplir el mínimo (esto puede afectar el siguiente viaje)
                        llegada_ajustada = siguiente["inicio"] - parada_min
                        # Actualizar el evento de vacío para que termine antes
                        eventos[-1]["fin"] = llegada_ajustada
                        llegada = llegada_ajustada
                        tiempo_disponible_parada = parada_min
                    
                    # Limitar el tiempo de parada al máximo permitido
                    tiempo_parada = min(tiempo_disponible_parada, parada_max)
                else:
                    # Si no hay regla de parada configurada, usar el tiempo disponible
                    tiempo_parada = max(0, tiempo_disponible_parada)
                
                # Verificar si se requiere recarga antes de agregar la parada
                consumo_proximo = _consumo_proyectado_restante(
                    bloque,
                    idx + 1,
                    parametros_electricos,
                )
                requiere_recarga = _requiere_recarga(parametros_electricos, bateria_actual, consumo_proximo)
                
                if requiere_recarga and tiempo_parada > 0:
                    # Intentar planificar recarga si es necesario
                    bateria_actual = _planificar_recarga_si_requiere(
                        eventos,
                        gestor,
                        parametros_electricos,
                        bateria_actual,
                        bus_id=None,
                        tipo_bus=tipo_bloque,
                        destino_actual=siguiente["origen"],
                        inicio_disponible=llegada,
                        fin_disponible=siguiente["inicio"],
                        contexto=contexto_bloque,
                        verbose=verbose,
                        consumo_proyectado=consumo_proximo,
                        autonomia_km=autonomia_tipo,
                    )
                    # Si se planificó recarga, puede que ya se haya agregado una parada
                    # Verificar si el último evento es una parada o recarga
                    if eventos and eventos[-1].get("evento") in ("Parada", "Recarga"):
                        # Ya se agregó una parada o recarga, verificar si necesitamos ajustar
                        ultimo_fin = eventos[-1].get("fin", llegada)
                        if ultimo_fin < siguiente["inicio"]:
                            # Agregar parada adicional si hay tiempo
                            tiempo_restante = siguiente["inicio"] - ultimo_fin
                            if tiempo_restante > 0:
                                eventos.append(
                                    {
                                        "evento": "Parada",
                                        "origen": siguiente["origen"],
                                        "destino": siguiente["origen"],
                                        "inicio": ultimo_fin,
                                        "fin": siguiente["inicio"],
                                        "kilometros": 0,
                                        "desc": f"Parada en {siguiente['origen']}",
                                        "tipo_bus": tipo_bloque,
                                    }
                                )
                                if bateria_actual is not None:
                                    eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                else:
                    # Agregar parada obligatoria
                    if tiempo_parada > 0:
                        eventos.append(
                            {
                                "evento": "Parada",
                                "origen": siguiente["origen"],
                                "destino": siguiente["origen"],
                                "inicio": llegada,
                                "fin": llegada + tiempo_parada,
                                "kilometros": 0,
                                "desc": f"Parada en {siguiente['origen']}",
                                "tipo_bus": tipo_bloque,
                            }
                        )
                        if bateria_actual is not None:
                            eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                    else:
                        _log_verbose(
                            f"ADVERTENCIA: No hay tiempo para parada en {nodo_destino} "
                            f"(llegada: {llegada}, siguiente inicio: {siguiente['inicio']})",
                            verbose,
                        )
                continue

            if tipo_conexion == "deposito" or tipo_conexion == "deposito_forzado":
                # REGLA CRÍTICA: Detectar y evitar vacíos cruzados
                # Un vacío cruzado ocurre cuando el destino del viaje origen es el mismo que el origen del viaje destino
                # y se está usando depósito (va al depósito y vuelve al mismo lugar)
                es_vacio_cruzado = detalle.get("es_vacio_cruzado", False) or (viaje["destino"] == siguiente["origen"])
                
                # Si es un vacío cruzado, intentar evitarlo usando una parada larga en lugar del depósito
                if es_vacio_cruzado and tipo_conexion != "deposito_forzado":
                    tiempo_disponible = siguiente["inicio"] - viaje["fin"]
                    nodo_parada = viaje["destino"]
                    regla_parada = gestor.paradas_dict.get(nodo_parada.upper()) if hasattr(gestor, "paradas_dict") else None
                    
                    if regla_parada:
                        parada_min = regla_parada.get("min", 0)
                        parada_max = regla_parada.get("max", 1440)
                        
                        # Si el tiempo disponible está dentro del rango de parada, usar parada en lugar de depósito
                        if tiempo_disponible >= parada_min and tiempo_disponible <= parada_max:
                            _log_verbose(
                                f"EVITANDO VACÍO CRUZADO: Usando parada en {nodo_parada} "
                                f"({tiempo_disponible} min) en lugar de depósito.",
                                verbose,
                            )
                            # Crear parada en lugar de depósito
                            eventos.append(
                                {
                                    "evento": "Parada",
                                    "origen": nodo_parada,
                                    "destino": nodo_parada,
                                    "inicio": viaje["fin"],
                                    "fin": siguiente["inicio"],
                                    "kilometros": 0,
                                    "desc": f"Parada en {nodo_parada} (evitando vacío cruzado)",
                                    "tipo_bus": tipo_bloque,
                                }
                            )
                            if bateria_actual is not None:
                                eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                            continue
                
                espera_origen = detalle.get("espera_origen", 0) if detalle else 0
                inicio_vacio_origen = viaje["fin"] + max(0, espera_origen)
                if espera_origen > 0:
                    eventos.append(
                        {
                            "evento": "Parada",
                            "origen": viaje["destino"],
                            "destino": viaje["destino"],
                            "inicio": viaje["fin"],
                            "fin": inicio_vacio_origen,
                            "kilometros": 0,
                            "desc": f"Parada en {viaje['destino']} (espera previa a depósito)",
                            "tipo_bus": tipo_bloque,
                        }
                    )
                    if bateria_actual is not None:
                        eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"

                # Si es deposito_forzado, buscar los tiempos de vacío al depósito base
                if tipo_conexion == "deposito_forzado":
                    deposito_intermedio = gestor.deposito_base
                    # Buscar tiempos de vacío al depósito base
                    tiempo_a_dep, km_a_dep = buscar_vacio(viaje["destino"], deposito_intermedio, inicio_vacio_origen)
                    tiempo_de_dep, km_de_dep = buscar_vacio(
                        deposito_intermedio,
                        siguiente["origen"],
                        max(siguiente["inicio"] - gestor._t_de_dep_aprox if hasattr(gestor, "_t_de_dep_aprox") else 30, 0),
                    )
                    
                    if tiempo_a_dep is None or tiempo_de_dep is None:
                        _log_verbose(
                            f"ERROR: No se pueden crear eventos forzados vía depósito: "
                            f"no hay vacíos configurados desde {viaje['destino']} a {deposito_intermedio} "
                            f"o desde {deposito_intermedio} a {siguiente['origen']}",
                            verbose,
                        )
                        continue
                    
                    t_a_dep = tiempo_a_dep
                    km_a_dep = km_a_dep or 0
                    t_de_dep = tiempo_de_dep
                    km_de_dep = km_de_dep or 0
                else:
                    deposito_intermedio = detalle.get("deposito", gestor.deposito_base)
                    if not isinstance(deposito_intermedio, str):
                        deposito_intermedio = str(deposito_intermedio) if deposito_intermedio else gestor.deposito_base
                    t_a_dep = detalle.get("t_a_dep", 0)
                    km_a_dep = detalle.get("km_a_dep", 0)
                    t_de_dep = detalle.get("t_de_dep", 0)
                    km_de_dep = detalle.get("km_de_dep", 0)
                
                llegada = inicio_vacio_origen + t_a_dep
                tiempo_min_dep = getattr(gestor, "tiempo_min_deposito", 5)
                salida_minima = llegada + tiempo_min_dep
                # Parada mínima en destino: si hay que dejar tiempo, salir antes
                nodo_destino_parada = siguiente["origen"]
                regla_parada_dest = gestor.paradas_dict.get(nodo_destino_parada.upper()) if hasattr(gestor, "paradas_dict") else None
                parada_min_dest = regla_parada_dest.get("min", 0) if regla_parada_dest else 0
                salida_justo_a_tiempo = siguiente["inicio"] - t_de_dep - parada_min_dest
                salida = max(salida_minima, salida_justo_a_tiempo)
                consumo_proximo = 0.0
                if parametros_electricos:
                    consumo_proximo = _consumo_estimado_evento(
                        {
                            "kilometros": km_de_dep,
                            "origen": deposito_intermedio,
                            "destino": siguiente["origen"],
                        },
                        parametros_electricos,
                    ) + _consumo_proyectado_restante(
                        bloque,
                        idx + 1,
                        parametros_electricos,
                    )

                eventos.append(
                    {
                        "evento": "Vacio",
                        "origen": viaje["destino"],
                        "destino": deposito_intermedio,
                        "inicio": viaje["fin"],
                        "fin": llegada,
                        "kilometros": km_a_dep,
                        "desc": f"Vacio {viaje['destino']}->{deposito_intermedio}",
                        "tipo_bus": tipo_bloque,
                    }
                )
                bateria_actual = _aplicar_consumo_evento(
                    eventos[-1],
                    parametros_electricos,
                    bateria_actual,
                    verbose,
                    contexto_bloque,
                    autonomia_tipo,
                )
                if salida > llegada:
                    puede_recargar = (
                        parametros_electricos is not None
                        and gestor.permite_recarga_en_deposito(deposito_intermedio)
                    )
                    recarga_realizada = False
                    if puede_recargar:
                        # REGLA: max_entrada_recarga es el % máximo con el que un bus puede entrar a recarga
                        # Si está por debajo de este puede entrar en cualquier momento
                        max_entrada = parametros_electricos.porcentaje_max_entrada_pct
                        minimo_circular = parametros_electricos.minimo_para_circular_pct
                        
                        # Determinar si debe recargar
                        debe_recargar = False
                        objetivo_recarga = 100.0
                        
                        if bateria_actual < max_entrada:
                            # Si está por debajo del máximo de entrada, puede entrar a recarga
                            # Ideal: recargar completamente para evitar múltiples recargas
                            debe_recargar = True
                            objetivo_recarga = 100.0
                        elif (bateria_actual - consumo_proximo) < minimo_circular:
                            # Si después del consumo proyectado quedaría por debajo del mínimo, debe recargar
                            debe_recargar = True
                            objetivo_recarga = max(100.0, minimo_circular + consumo_proximo + 10.0)
                            objetivo_recarga = min(100.0, objetivo_recarga)
                        
                        recarga_info = None
                        if debe_recargar:
                            recarga_info = _calcular_recarga_disponible(
                                parametros_electricos,
                                bateria_actual,
                                llegada,
                                salida,
                                objetivo_recarga,
                            )
                        if recarga_info:
                            inicio_carga, fin_carga, bateria_final = recarga_info
                            if inicio_carga > llegada:
                                eventos.append(
                                    {
                                        "evento": "Parada",
                                        "origen": deposito_intermedio,
                                        "destino": deposito_intermedio,
                                        "inicio": llegada,
                                        "fin": inicio_carga,
                                        "kilometros": 0,
                                        "desc": f"Parada previa a recarga en {deposito_intermedio}",
                                        "tipo_bus": tipo_bloque,
                                    }
                                )
                                if bateria_actual is not None:
                                    eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                            _agregar_evento_recarga(
                                eventos,
                                deposito_intermedio,
                                inicio_carga,
                                fin_carga,
                                gestor,
                                bateria_inicial=bateria_actual,
                                bateria_final=bateria_final,
                                tipo_bus=tipo_bloque,
                            )
                            bateria_actual = bateria_final
                            recarga_realizada = True
                            if fin_carga < salida:
                                eventos.append(
                                    {
                                        "evento": "Parada",
                                        "origen": deposito_intermedio,
                                        "destino": deposito_intermedio,
                                        "inicio": fin_carga,
                                        "fin": salida,
                                        "kilometros": 0,
                                        "desc": f"Parada posterior a recarga en {deposito_intermedio}",
                                        "tipo_bus": tipo_bloque,
                                    }
                                )
                                if bateria_actual is not None:
                                    eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                    if not recarga_realizada:
                        eventos.append(
                            {
                                "evento": "Parada",
                                "origen": deposito_intermedio,
                                "destino": deposito_intermedio,
                                "inicio": llegada,
                                "fin": salida,
                                "kilometros": 0,
                                "desc": f"Parada en {deposito_intermedio}",
                                "tipo_bus": tipo_bloque,
                            }
                        )
                        if bateria_actual is not None:
                            eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                # REGLA CRÍTICA: El vacío desde el depósito debe terminar ANTES del siguiente viaje
                # para dejar tiempo para una parada obligatoria
                tiempo_vacio_de_dep = t_de_dep
                llegada_destino = salida + tiempo_vacio_de_dep
                
                # Verificar que haya tiempo para una parada después del vacío
                tiempo_disponible_parada = siguiente["inicio"] - llegada_destino
                nodo_destino = siguiente["origen"]
                regla_parada = gestor.paradas_dict.get(nodo_destino.upper()) if hasattr(gestor, "paradas_dict") else None
                
                if regla_parada:
                    parada_min = regla_parada.get("min", 0)
                    parada_max = regla_parada.get("max", 1440)
                    
                    # Si no hay tiempo suficiente para el mínimo, ajustar
                    if tiempo_disponible_parada < parada_min:
                        _log_verbose(
                            f"ERROR: Tiempo disponible para parada insuficiente en {nodo_destino}: "
                            f"{tiempo_disponible_parada} min < mínimo requerido {parada_min} min",
                            verbose,
                        )
                        # Ajustar la salida del depósito para cumplir el mínimo
                        salida_ajustada = siguiente["inicio"] - tiempo_vacio_de_dep - parada_min
                        if salida_ajustada < llegada:
                            _log_verbose(
                                f"ERROR CRÍTICO: No se puede cumplir tiempo mínimo de parada "
                                f"en {nodo_destino} sin violar tiempos de depósito",
                                verbose,
                            )
                        else:
                            salida = salida_ajustada
                            llegada_destino = salida + tiempo_vacio_de_dep
                            tiempo_disponible_parada = parada_min
                            # Actualizar Parada en depósito para que termine cuando inicia el Vacio (evitar solapamiento)
                            ult = eventos[-1] if eventos else {}
                            if (str(ult.get("evento", "")).strip().upper() == "PARADA" and
                                (ult.get("origen") == deposito_intermedio or ult.get("destino") == deposito_intermedio)):
                                ult["fin"] = salida
                    
                    # Limitar el tiempo de parada al máximo permitido
                    tiempo_parada = min(tiempo_disponible_parada, parada_max)
                else:
                    # Si no hay regla de parada configurada, usar el tiempo disponible
                    tiempo_parada = max(0, tiempo_disponible_parada)
                
                eventos.append(
                    {
                        "evento": "Vacio",
                        "origen": deposito_intermedio,
                        "destino": siguiente["origen"],
                        "inicio": salida,
                        "fin": llegada_destino,
                        "kilometros": km_de_dep,
                        "desc": f"Vacio {deposito_intermedio}->{siguiente['origen']}",
                        "tipo_bus": tipo_bloque,
                    }
                )
                bateria_actual = _aplicar_consumo_evento(
                    eventos[-1],
                    parametros_electricos,
                    bateria_actual,
                    verbose,
                    contexto_bloque,
                )
                
                # REGLA CRÍTICA: SIEMPRE debe haber una parada después del vacío
                if tiempo_parada > 0:
                    eventos.append(
                        {
                            "evento": "Parada",
                            "origen": siguiente["origen"],
                            "destino": siguiente["origen"],
                            "inicio": llegada_destino,
                            "fin": llegada_destino + tiempo_parada,
                            "kilometros": 0,
                            "desc": f"Parada en {siguiente['origen']}",
                            "tipo_bus": tipo_bloque,
                        }
                    )
                    if bateria_actual is not None:
                        eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
                else:
                    _log_verbose(
                        f"ADVERTENCIA: No hay tiempo para parada en {nodo_destino} "
                        f"(llegada: {llegada_destino}, siguiente inicio: {siguiente['inicio']})",
                        verbose,
                    )
                continue

            _log_verbose(
                f"Tipo de conexión no reconocido ({tipo_conexion}) entre viajes {viaje.get('id') or viaje.get('_tmp_id') or '?'} y {siguiente.get('id') or siguiente.get('_tmp_id') or '?'}.",
                verbose,
            )

        ultimo = bloque[-1]
        # CRÍTICO: El bus DEBE terminar en el mismo depósito donde inició
        # No buscar el mejor depósito - usar siempre el depósito de inicio
        deposito_final_usado = deposito_usado  # SIEMPRE el mismo depósito donde inició
        
        # Buscar el tiempo de vacío desde el destino del último viaje hacia el depósito de inicio
        t_vacio_fin, km_vacio_fin = _buscar_tiempo_vacio_con_respaldo(
            gestor,
            ultimo["destino"],
            deposito_final_usado,
            ultimo["fin"],
            verbose=verbose,
            contexto="retorno al depósito",
            buscar_vacio_fn=buscar_vacio,
        )
        if t_vacio_fin is None:
            # Si no hay conexión al depósito de inicio, reportar error
            print(f"ADVERTENCIA: Bus no puede regresar al depósito de inicio {deposito_final_usado} "
                  f"desde {ultimo['destino']}. No hay conexión de vacío habilitada. "
                  f"Revisar configuración de vacíos.")
            t_vacio_fin = 0
            km_vacio_fin = 0
        else:
            t_vacio_fin = t_vacio_fin or 0
        
        llegada_deposito = ultimo["fin"] + t_vacio_fin
        if parametros_electricos and bateria_actual is not None:
            bateria_actual = _planificar_recarga_si_requiere(
                eventos,
                gestor,
                parametros_electricos,
                bateria_actual,
                bus_id=None,
                tipo_bus=tipo_bloque,
                destino_actual=ultimo["destino"],
                inicio_disponible=ultimo["fin"],
                fin_disponible=llegada_deposito,
                contexto=contexto_bloque,
                verbose=verbose,
                autonomia_km=autonomia_tipo,
            )
        eventos.append(
            {
                "evento": "Vacio",
                "origen": ultimo["destino"],
                "destino": deposito_final_usado,
                "inicio": ultimo["fin"],
                "fin": llegada_deposito,
                "kilometros": km_vacio_fin,
                "desc": f"Vacio {ultimo['destino']}->{deposito_final_usado}",
                "tipo_bus": tipo_bloque,
            }
        )
        bateria_actual = _aplicar_consumo_evento(
            eventos[-1],
            parametros_electricos,
            bateria_actual,
            verbose,
            contexto_bloque,
            autonomia_tipo,
        )
        fin_servicio = llegada_deposito
        if parametros_electricos and bateria_actual is not None:
            ventana_final = llegada_deposito + max(gestor.tiempo_min_deposito, 60)
            recarga_final = _calcular_recarga_disponible(
                parametros_electricos,
                bateria_actual,
                llegada_deposito,
                ventana_final,
                100.0,
            )
            if not recarga_final and bateria_actual < 99.9 and parametros_electricos.tasa_recarga_pct_por_min > 0:
                delta = max(0.0, 100.0 - bateria_actual)
                duracion = math.ceil(delta / parametros_electricos.tasa_recarga_pct_por_min)
                if duracion > 0:
                    recarga_final = (llegada_deposito, llegada_deposito + duracion, 100.0)
            if recarga_final:
                inicio_r, fin_r, bateria_final = recarga_final
                if fin_r > inicio_r:
                    _agregar_evento_recarga(
                        eventos,
                        deposito_final_usado,
                        inicio_r,
                        fin_r,
                        gestor,
                        bateria_inicial=bateria_actual,
                        bateria_final=bateria_final,
                        tipo_bus=tipo_bloque,
                    )
                    bateria_actual = bateria_final
                    fin_servicio = max(fin_servicio, fin_r)
        eventos.append(
            {
                "evento": "FnS",
                "origen": deposito_final_usado,
                "destino": deposito_final_usado,
                "inicio": fin_servicio,
                "fin": fin_servicio,
                "kilometros": 0,
                "desc": f"Fin de servicio bus en {deposito_final_usado}",
                "tipo_bus": tipo_bloque,
            }
        )
        if bateria_actual is not None:
            eventos[-1]["porcentaje_bateria"] = f"{bateria_actual:.1f}%"
        eventos_por_bus.append(_normalizar_eventos_bus(eventos, verbose, gestor))

    return eventos_por_bus


def construir_eventos_bus(
    bloques_bus: List[List[Dict[str, Any]]],
    gestor: GestorDeLogistica,
    verbose: bool = False,
) -> List[List[Dict[str, Any]]]:
    """
    Construye la secuencia de eventos por bus desde bloques_bus (Fase 1)
    reutilizando la lógica centralizada de _construir_eventos_bus.
    """
    return _construir_eventos_bus(bloques_bus, gestor, verbose=verbose)
