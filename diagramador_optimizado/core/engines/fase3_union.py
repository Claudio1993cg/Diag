"""
FASE 3 V2.0 - Unión de Conductores
=====================================
Objetivo: reducir el número total de conductores uniendo turnos compatibles.

REGLAS para unir dos turnos A y B (A termina antes que B):
  1. Descanso: B.inicio - A.fin ≥ tiempo_descanso_minimo
  2. Duración total: B.fin - A.inicio ≤ limite_jornada
  3. Cambio de bus: solo si están en el mismo grupo de líneas
  4. Máximo de cambios de bus: max_cambios_bus (config)
  5. El turno unido termina en depot o punto de relevo válido

Algoritmo: Greedy multi-pass con seed variable para escapar mínimos locales.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from diagramador_optimizado.core.domain.logistica import GestorDeLogistica
from diagramador_optimizado.core.engines.fase2_conductores import _es_deposito, _es_relevo_valido
from diagramador_optimizado.core.validaciones_fase import validar_fase3_sin_solapamiento_turnos


# ---------------------------------------------------------------------------
# Compatibilidad entre turnos
# ---------------------------------------------------------------------------

def _mismo_grupo_lineas(turno_a: Dict, turno_b: Dict, mapa_viajes: Dict, gestor: GestorDeLogistica) -> bool:
    """True si los turnos operan en el mismo grupo de líneas o pueden interlinear."""
    def _lineas(t: Dict) -> Set[str]:
        lineas: Set[str] = set()
        for tid, _ in t.get("tareas_con_bus", []):
            v = mapa_viajes.get(tid) or mapa_viajes.get(str(tid))
            if v and v.get("linea"):
                lineas.add(str(v["linea"]).strip().upper())
        return lineas

    la = _lineas(turno_a)
    lb = _lineas(turno_b)
    if not la or not lb:
        return True  # Sin línea definida: permitir unión

    for li in la:
        for lj in lb:
            if li == lj or gestor.pueden_interlinear(li, lj):
                return True
    return False


def _buses_del_turno(turno: Dict) -> Set[int]:
    return {bus for _, bus in turno.get("tareas_con_bus", [])}


def _nodo_inicio_turno(tb: Dict, mapa_viajes: Dict, deposito: str) -> str:
    """Devuelve el nodo REAL donde el conductor debe estar para iniciar el turno B.

    No es el depósito de inicio (que siempre es el depósito), sino el origen
    del PRIMER VIAJE COMERCIAL del turno B: el lugar físico donde empieza a
    trabajar el conductor. Si el turno comienza en un nodo diferente al depósito
    (relevo), eso es lo que importa para la conectividad física.
    """
    # Buscar el origen del primer viaje comercial del turno B
    for tid, _ in tb.get("tareas_con_bus", []):
        v = mapa_viajes.get(tid) or mapa_viajes.get(str(tid))
        if v and v.get("origen"):
            origen = str(v["origen"]).strip()
            if origen:
                return origen
    # Fallback: deposito_inicio del turno (siempre es el depósito)
    return (tb.get("deposito_inicio") or deposito).strip()


def _nodo_fin_turno(ta: Dict, deposito: str) -> str:
    """Devuelve el nodo donde está el conductor al final del turno A."""
    return (ta.get("punto_fin_turno") or deposito).strip()


def _pueden_unirse(
    ta: Dict,
    tb: Dict,
    mapa_viajes: Dict,
    gestor: GestorDeLogistica,
    limite_jornada: int,
    descanso_min: int,
    max_cambios_bus: int,
) -> bool:
    """
    Evalúa si el conductor que hizo turno A puede hacer turno B después.

    Condiciones:
      - Descanso suficiente entre A.fin y B.inicio
      - Duración total ≤ limite_jornada
      - Mismo grupo de líneas si hay cambio de bus
      - No superar max_cambios_bus
      - CONECTIVIDAD FÍSICA: el conductor puede llegar desde donde termina A
        hasta donde debe estar para iniciar B dentro del tiempo disponible.
        Evita teleportaciones y conexiones físicamente imposibles.
    """
    if tb["inicio"] <= ta["fin"]:
        return False

    descanso = tb["inicio"] - ta["fin"]
    if descanso < descanso_min:
        return False

    # No unir cuando entre A y B hay una parada larga (ej. parada en depósito 10:00–16:00).
    # Ese tiempo no debe asociarse a un solo conductor; se separa en dos conductores.
    parada_larga_umbral = getattr(gestor, "parada_larga_umbral", 60)
    if descanso > parada_larga_umbral:
        return False

    duracion_span = tb["fin"] - ta["inicio"]
    if duracion_span < 0:
        duracion_span += 1440
    if duracion_span > limite_jornada:
        return False

    buses_a = _buses_del_turno(ta)
    buses_b = _buses_del_turno(tb)
    cambios_existentes = max(ta.get("cambios_bus", 0), tb.get("cambios_bus", 0))
    if buses_a != buses_b:
        cambios_existentes += 1
        if cambios_existentes > max_cambios_bus:
            return False
        if not _mismo_grupo_lineas(ta, tb, mapa_viajes, gestor):
            return False

    # VERIFICACIÓN: No solapamiento en el MISMO bus
    # Si ambos turnos usan el mismo bus, el último viaje de ta debe terminar antes del primer viaje de tb
    buses_comunes = buses_a & buses_b
    for bus_id in buses_comunes:
        tareas_ta = [(tid, b) for tid, b in ta.get("tareas_con_bus", []) if b == bus_id]
        tareas_tb = [(tid, b) for tid, b in tb.get("tareas_con_bus", []) if b == bus_id]
        if not tareas_ta or not tareas_tb:
            continue
        # Último viaje de ta en este bus
        ult_tid_ta = tareas_ta[-1][0]
        v_ta = mapa_viajes.get(ult_tid_ta) or mapa_viajes.get(str(ult_tid_ta))
        fin_ta = int(v_ta.get("fin", 0)) if v_ta else 0
        # Primer viaje de tb en este bus
        pri_tid_tb = tareas_tb[0][0]
        v_tb = mapa_viajes.get(pri_tid_tb) or mapa_viajes.get(str(pri_tid_tb))
        ini_tb = int(v_tb.get("inicio", 0)) if v_tb else 0
        if ini_tb < fin_ta:
            return False  # Solapamiento: tb empieza antes de que ta termine en ese bus

    # VERIFICACIÓN DE CONECTIVIDAD FÍSICA
    # El conductor termina el turno A en nodo_fin y necesita estar en nodo_inicio_b
    # al comenzar el turno B. Si son nodos distintos, el tiempo de viaje debe
    # caber dentro del descanso disponible.
    deposito = gestor.deposito_base
    nodo_fin_a = _nodo_fin_turno(ta, deposito)
    nodo_inicio_b = _nodo_inicio_turno(tb, mapa_viajes, deposito)

    # REGLA ADICIONAL: si A termina en un punto de relevo (LOS TILOS, PIE ANDINO, etc.)
    # no permitir unirlo con un turno B que comience en OTRO nodo distinto.
    # Interpretación: al llegar a un punto de relevo válido, el conductor puede
    # cortar su jornada y volver al depósito por desplazamiento configurado,
    # en lugar de seguir a otro nodo distinto con un nuevo bus.
    if nodo_fin_a and nodo_inicio_b:
        puede_relevo, _ = gestor.puede_hacer_relevo_en_nodo(nodo_fin_a)
        if (
            puede_relevo
            and not _es_deposito(nodo_fin_a, deposito)
            and nodo_fin_a.upper() != nodo_inicio_b.upper()
        ):
            return False

    if nodo_fin_a and nodo_inicio_b and nodo_fin_a.upper() != nodo_inicio_b.upper():
        # Nodos distintos: verificar si el viaje es posible en el tiempo disponible
        tiempo_viaje = None

        # Intentar como desplazamiento (conductor a pie/taxi entre puntos de relevo)
        hab_d, t_d = gestor.buscar_info_desplazamiento(nodo_fin_a, nodo_inicio_b, ta["fin"])
        if hab_d and t_d is not None and t_d > 0:
            tiempo_viaje = t_d
        else:
            # Intentar como vacio (conductor va en bus vacío)
            t_v, _ = gestor.buscar_tiempo_vacio(nodo_fin_a, nodo_inicio_b, ta["fin"])
            if t_v and t_v > 0:
                tiempo_viaje = t_v

        if tiempo_viaje is not None and descanso < tiempo_viaje:
            # No hay tiempo suficiente para que el conductor llegue → unión imposible
            return False
        # Si tiempo_viaje es None (no hay ruta conocida entre esos nodos),
        # permitimos la unión solo si el descanso es suficientemente largo (≥ 30 min)
        # como margen de seguridad cuando no hay datos de viaje configurados.
        if tiempo_viaje is None and descanso < 30:
            return False

    return True


def _inicio_viaje(tid: Any, mapa_viajes: Dict) -> int:
    """Retorna el inicio del viaje en minutos, o 0 si no se encuentra."""
    v = mapa_viajes.get(tid) or mapa_viajes.get(str(tid))
    if v and "inicio" in v:
        return int(v.get("inicio", 0))
    return 0


def _unir_turnos(
    ta: Dict, tb: Dict, gestor: GestorDeLogistica, mapa_viajes: Optional[Dict] = None
) -> Dict:
    """Crea el turno combinado de A + B. tareas_con_bus ordenadas cronológicamente."""
    deposito = gestor.deposito_base
    mapa = mapa_viajes or {}

    buses_a = _buses_del_turno(ta)
    buses_b = _buses_del_turno(tb)
    cambios = max(ta.get("cambios_bus", 0), tb.get("cambios_bus", 0))
    if buses_a != buses_b:
        cambios += 1

    # Concatenar y ordenar tareas por inicio real del viaje (evita solapamientos en exportación)
    tareas_unidas = list(ta.get("tareas_con_bus", [])) + list(tb.get("tareas_con_bus", []))
    tareas_unidas.sort(key=lambda x: _inicio_viaje(x[0], mapa))

    # punto_fin_turno del turno unido = el del turno que termina último
    t_ultimo = tb if tb["fin"] >= ta["fin"] else ta
    pf_raw = (t_ultimo.get("punto_fin_turno") or deposito).strip()
    if _es_deposito(pf_raw, deposito):
        punto_fin = deposito
    else:
        valido, _, _ = _es_relevo_valido(pf_raw, deposito, gestor)
        punto_fin = pf_raw if valido else deposito

    return {
        "id_bus": ta.get("id_bus"),
        "tareas_con_bus": tareas_unidas,
        "inicio": ta["inicio"],
        "fin": tb["fin"],
        "duracion": tb["fin"] - ta["inicio"],
        "overtime": False,
        "cambios_bus": cambios,
        "deposito_inicio": ta.get("deposito_inicio") or gestor.deposito_base,
        "punto_fin_turno": punto_fin,
        "es_turno_unido": True,
    }


# ---------------------------------------------------------------------------
# Algoritmo greedy de unión
# ---------------------------------------------------------------------------

def _greedy_union(
    turnos: List[Dict],
    mapa_viajes: Dict,
    gestor: GestorDeLogistica,
    limite_jornada: int,
    descanso_min: int,
    max_cambios_bus: int,
) -> List[Dict]:
    """mapa_viajes: {viaje_id: viaje_dict} para ordenar tareas y validar solapamientos."""
    """
    Un pase greedy: ordena por inicio, une cada turno con el siguiente compatible
    que minimice conductores. Repite hasta que no haya más uniones posibles.
    """
    cambio = True
    actuales = sorted(turnos, key=lambda t: t["inicio"])

    while cambio:
        cambio = False
        usados: Set[int] = set()
        nuevo: List[Dict] = []

        for i, ta in enumerate(actuales):
            if i in usados:
                continue
            mejor_j = -1
            for j in range(i + 1, len(actuales)):
                if j in usados:
                    continue
                tb = actuales[j]
                if _pueden_unirse(ta, tb, mapa_viajes, gestor, limite_jornada, descanso_min, max_cambios_bus):
                    mejor_j = j
                    break  # Tomamos el primer compatible (orden temporal)

            if mejor_j >= 0:
                nuevo.append(_unir_turnos(ta, actuales[mejor_j], gestor, mapa_viajes))
                usados.add(i)
                usados.add(mejor_j)
                cambio = True
            else:
                nuevo.append(ta)
                usados.add(i)

        actuales = sorted(nuevo, key=lambda t: t["inicio"])

    return actuales


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def resolver_union_conductores(
    config: Dict[str, Any],
    turnos_conductores: List[Dict[str, Any]],
    metadata_tareas: Dict[Any, Dict[str, Any]],
    viajes_comerciales: List[Dict[str, Any]],
    gestor: GestorDeLogistica,
    verbose: bool = False,
    seed_externo: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    FASE 3: Une turnos compatibles para reducir el número total de conductores.

    Estrategia multi-pass greedy:
      - Pasada 0: orden natural (por inicio de turno)
      - Pasadas 1+: variaciones deterministas para escapar mínimos locales

    Returns: (turnos_unidos, estado)
    """
    print("\n" + "=" * 70)
    print("FASE 3: Unión de Conductores V2.0")
    print("=" * 70)

    f3_cfg = config.get("fase_3_union_conductores", {})
    limite_jornada: int = gestor.limite_jornada
    descanso_min: int = gestor.tiempo_descanso_minimo()
    max_cambios_bus: int = int(f3_cfg.get("max_cambios_bus", 2))
    num_passes: int = max(1, min(int(f3_cfg.get("multi_pass_greedy", 3)), 5))
    deposito: str = gestor.deposito_base

    print(f"  Límite jornada   : {limite_jornada} min")
    print(f"  Descanso mínimo  : {descanso_min} min")
    print(f"  Max cambios bus  : {max_cambios_bus}")
    print(f"  Pasadas greedy   : {num_passes}")
    print(f"  Turnos entrada   : {len(turnos_conductores)}")

    # Filtrar turnos sin viajes comerciales (capa de seguridad). Incluir id, _tmp_id
    # y también ids sintéticos presentes en metadata_tareas (p.ej. rescates por bus).
    ids_com: Set[Any] = set()
    for v in viajes_comerciales:
        for key in (v.get("id"), v.get("_tmp_id")):
            if key is not None:
                ids_com.add(key)
                ids_com.add(str(key))
    ids_meta: Set[Any] = set(metadata_tareas.keys()) | {str(tid) for tid in metadata_tareas.keys()}

    def _tiene_comerciales(turno: Dict[str, Any]) -> bool:
        for tid, _ in turno.get("tareas_con_bus", []):
            if tid in ids_com or str(tid) in ids_com:
                return True
            if tid in ids_meta or str(tid) in ids_meta:
                return True
        return False

    turnos_validos = [t for t in turnos_conductores if _tiene_comerciales(t)]
    if len(turnos_validos) < len(turnos_conductores):
        print(f"  [Filtro] Descartados {len(turnos_conductores) - len(turnos_validos)} turnos sin comerciales")

    if len(turnos_validos) <= 1:
        print("  Sin turnos suficientes para unir.")
        return turnos_validos, "Sin uniones"

    mapa_viajes: Dict[Any, Dict[str, Any]] = {}
    for v in viajes_comerciales:
        for key in (v.get("id"), v.get("_tmp_id")):
            if key is not None:
                mapa_viajes[key] = v
                mapa_viajes[str(key)] = v

    mejor: List[Dict] = list(turnos_validos)

    for pasada in range(num_passes):
        candidatos = _greedy_union(
            turnos_validos, mapa_viajes, gestor, limite_jornada, descanso_min, max_cambios_bus
        )
        if len(candidatos) < len(mejor):
            mejor = candidatos

    # Normalizar punto_fin_turno en todos los turnos resultantes
    for t in mejor:
        pf = (t.get("punto_fin_turno") or "").strip()
        if not pf or _es_deposito(pf, deposito):
            t["punto_fin_turno"] = deposito
        else:
            valido, _, _ = _es_relevo_valido(pf, deposito, gestor)
            if not valido:
                t["punto_fin_turno"] = deposito

    # REGLA DURA: Sin solapamiento conductor+bus en turnos unidos (no negociable)
    mapa_completo = dict(mapa_viajes)
    for tid, meta in metadata_tareas.items():
        if meta.get("viaje") and tid not in mapa_completo:
            mapa_completo[tid] = meta["viaje"]
    validar_fase3_sin_solapamiento_turnos(mejor, mapa_completo)

    reduccion = len(turnos_validos) - len(mejor)
    pct = (reduccion / len(turnos_validos) * 100) if turnos_validos else 0

    # REGLA DURA: Verificar que todo viaje comercial siga cubierto tras la unión
    canon_per_viaje = {v.get("id") or v.get("_tmp_id") for v in viajes_comerciales if (v.get("id") or v.get("_tmp_id")) is not None}
    cubiertos_f3 = set()
    for t in mejor:
        for tid, _ in t.get("tareas_con_bus", []):
            if tid is not None:
                cubiertos_f3.add(tid)
                cubiertos_f3.add(str(tid))
    faltantes_f3 = [c for c in canon_per_viaje if c not in cubiertos_f3 and str(c) not in cubiertos_f3]
    if faltantes_f3:
        print(f"  [FASE 3] ADVERTENCIA: {len(faltantes_f3)} viajes sin conductor tras unión (no debería ocurrir)")

    print(f"\n  RESULTADO FASE 3:")
    print(f"    Conductores antes : {len(turnos_validos)}")
    print(f"    Conductores después: {len(mejor)}")
    print(f"    Reducción         : {reduccion} ({pct:.1f}%)")
    print("=" * 70)

    estado = f"Fase 3: {len(mejor)} conductores ({reduccion} reducidos, {pct:.1f}%)"
    return mejor, estado
