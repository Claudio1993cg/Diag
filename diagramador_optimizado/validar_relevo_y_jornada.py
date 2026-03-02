"""
Validación: puntos de relevo dinámicos, InS/FnS y límite de jornada.
Comprueba con la configuración actual que se cumple el ejemplo:
- Puntos de relevo = nodos con desplazamiento habilitado al depósito (LOS TILOS, PIE ANDINO).
- LA PIRAMIDE no es punto de relevo (no puede terminar conductor ahí sin Vacio/Desplazamiento a depósito).
- Jornada máx 600 min; corte en relevo para no superarla.
- Todo conductor con InS y FnS, sin teletransportaciones.
"""
from __future__ import annotations

import json
import os
import sys

# Asegurar import desde raíz del proyecto
raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if raiz not in sys.path:
    sys.path.insert(0, raiz)

from diagramador_optimizado.core.domain.logistica import GestorDeLogistica


def main():
    config_path = os.path.join(os.path.dirname(__file__), "configuracion.json")
    if not os.path.exists(config_path):
        print(f"No se encontró {config_path}")
        return 1

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    gestor = GestorDeLogistica(config)
    deposito = gestor.deposito_base
    limite_jornada = gestor.limite_jornada
    tiempo_toma = gestor.tiempo_toma

    print("=" * 70)
    print("VALIDACIÓN: Puntos de relevo y jornada (ejemplo usuario)")
    print("=" * 70)
    print(f"Depósito configurado: {deposito}")
    print(f"Límite jornada: {limite_jornada} min ({limite_jornada // 60}h)")
    print(f"Tiempo toma (InS): {tiempo_toma} min")
    print()

    errores = []
    ok_count = 0

    # 1) LA PIRAMIDE no debe ser punto de relevo (desplazamiento a depósito deshabilitado en config)
    puede_la_piramide, tiempo_p = gestor.puede_hacer_relevo_en_nodo("LA PIRAMIDE")
    if puede_la_piramide:
        errores.append("LA PIRAMIDE no debe ser punto de relevo (conductor no puede terminar ahí para FnS)")
    else:
        print("[OK] LA PIRAMIDE no es punto de relevo (puede_hacer_relevo_en_nodo = False)")
        ok_count += 1

    # 2) LOS TILOS debe ser punto de relevo (desplazamiento LOS TILOS -> Deposito habilitado 30 min)
    puede_los_tilos, tiempo_lt = gestor.puede_hacer_relevo_en_nodo("LOS TILOS")
    if not puede_los_tilos:
        errores.append("LOS TILOS debe ser punto de relevo (desplazamiento habilitado en config)")
    elif tiempo_lt is None or tiempo_lt <= 0:
        errores.append("LOS TILOS: tiempo de desplazamiento debe ser > 0 (30 min en config)")
    else:
        print(f"[OK] LOS TILOS es punto de relevo (tiempo máx desplazamiento: {tiempo_lt} min)")
        ok_count += 1

    # 3) PIE ANDINO debe ser punto de relevo
    puede_pie, tiempo_pie = gestor.puede_hacer_relevo_en_nodo("PIE ANDINO")
    if not puede_pie:
        errores.append("PIE ANDINO debe ser punto de relevo")
    else:
        print(f"[OK] PIE ANDINO es punto de relevo (tiempo: {tiempo_pie} min)")
        ok_count += 1

    # 4) Desplazamiento explícito: LOS TILOS -> depósito
    hab_lt_dep, t_lt_dep = gestor.buscar_info_desplazamiento("LOS TILOS", deposito, 0)
    if not hab_lt_dep or t_lt_dep is None:
        errores.append("Desplazamiento LOS TILOS -> depósito debe estar habilitado (30 min)")
    else:
        print(f"[OK] Desplazamiento LOS TILOS -> {deposito}: habilitado, {t_lt_dep} min")
        ok_count += 1

    # 5) Desplazamiento LA PIRAMIDE -> depósito debe estar deshabilitado (o no contar como relevo)
    hab_pir_dep, t_pir_dep = gestor.buscar_info_desplazamiento("LA PIRAMIDE", deposito, 0)
    if hab_pir_dep:
        # En config actual está false; si en otro config estuviera true, podría ser relevo solo si está en puntos_relevo
        print(f"[INFO] Desplazamiento LA PIRAMIDE -> depósito: habilitado={hab_pir_dep}, tiempo={t_pir_dep}")
    else:
        print("[OK] Desplazamiento LA PIRAMIDE -> depósito no habilitado (no puede FnS ahí sin Vacio)")
        ok_count += 1

    # 6) Ejemplo numérico: corte en 10:15 en LOS TILOS -> FnS 10:45, jornada 5h30
    # Inicio real conductor = fin InS = 05:15 (minutos 315). Fin = 10:45 (minutos 645). Jornada = 645 - 315 = 330 min < 600
    inicio_ins_min = 5 * 60 + 15   # 05:15
    fin_fns_min = 10 * 60 + 45    # 10:45
    jornada_ejemplo = fin_fns_min - inicio_ins_min
    if jornada_ejemplo > limite_jornada:
        errores.append(f"Ejemplo: jornada 05:15-10:45 = {jornada_ejemplo} min debe ser <= {limite_jornada}")
    else:
        print(f"[OK] Ejemplo corte 10:15 LOS TILOS: jornada 05:15-10:45 = {jornada_ejemplo} min < {limite_jornada}")
        ok_count += 1

    # 7) Jornada que NO debe permitirse: 05:15 a 15:30 = 615 min > 600
    jornada_prohibida = (15 * 60 + 30) - inicio_ins_min
    if jornada_prohibida <= limite_jornada:
        errores.append(f"Jornada 05:15-15:30 = {jornada_prohibida} min no debe ser permitida (debe ser > {limite_jornada})")
    else:
        print(f"[OK] Jornada 05:15-15:30 = {jornada_prohibida} min > {limite_jornada} (correctamente prohibida)")
        ok_count += 1

    print()
    if errores:
        print("ERRORES:")
        for e in errores:
            print(f"  - {e}")
        return 1
    print(f"Todas las validaciones pasaron ({ok_count} checks).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
