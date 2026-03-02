from __future__ import annotations

import os
import sys
from typing import Optional

from diagramador_optimizado.io.loaders import cargar_config, cargar_salidas_desde_excel
from diagramador_optimizado.io.config_validator import validar_configuracion, ConfigValidationError
from diagramador_optimizado.io.exporters.excel_writer import exportar_resultado_excel
from diagramador_optimizado.core.domain.logistica import GestorDeLogistica
from diagramador_optimizado.core.engines.fase1_buses import resolver_diagramacion_buses
from diagramador_optimizado.core.engines.fase2_conductores import resolver_diagramacion_conductores
from diagramador_optimizado.core.engines.fase3_union import resolver_union_conductores


def main(
    archivo_excel: str = "datos_salidas.xlsx",
    archivo_config: str = "configuracion.json",
    archivo_salida: str = "resultado_diagramacion.xlsx",
    random_seed: Optional[int] = 42,
) -> None:
    """
    Punto de entrada principal. Orquesta la carga de datos, ambas fases de optimización
    y la exportación final reutilizando un único GestorDeLogistica.
    """
    # Detectar si estamos ejecutando desde un ejecutable compilado
    es_ejecutable = getattr(sys, 'frozen', False)
    
    if es_ejecutable:
        # Si estamos en un ejecutable, usar el directorio de trabajo actual
        # (donde está el .exe) en lugar del directorio del paquete
        raiz_proyecto = os.getcwd()
    else:
        # Si estamos ejecutando desde Python normal, usar la lógica original
        paquete_dir = os.path.abspath(os.path.dirname(__file__))
        raiz_proyecto = os.path.abspath(os.path.join(paquete_dir, os.pardir))
    
    # Si las rutas ya son absolutas, usarlas directamente; si no, unirlas con raiz_proyecto
    if os.path.isabs(archivo_config):
        ruta_config = archivo_config
    else:
        ruta_config = os.path.join(raiz_proyecto, archivo_config)
    
    if os.path.isabs(archivo_excel):
        ruta_excel = archivo_excel
    else:
        ruta_excel = os.path.join(raiz_proyecto, archivo_excel)
    
    if os.path.isabs(archivo_salida):
        ruta_salida = archivo_salida
    else:
        ruta_salida = os.path.join(raiz_proyecto, archivo_salida)
    
    # Asegurar que las rutas sean absolutas
    ruta_config = os.path.abspath(ruta_config)
    ruta_excel = os.path.abspath(ruta_excel)
    ruta_salida = os.path.abspath(ruta_salida)

    print("\n" + "=" * 70)
    print("ARCHIVOS DE ESTA EJECUCIÓN")
    print("=" * 70)
    print(f"  Entrada:  {ruta_excel}")
    print(f"  Salida:   {ruta_salida}")
    print("  Cierra Excel si tienes el archivo de salida abierto para que se pueda guardar.")
    print("=" * 70 + "\n")

    if not os.path.exists(ruta_config):
        print(f"ERROR: No se encontró el archivo de configuración en {ruta_config}")
        return
    if not os.path.exists(ruta_excel):
        print(f"ERROR: No se encontró el archivo Excel en {ruta_excel}")
        return

    config = cargar_config(ruta_config)
    try:
        validar_configuracion(config)
    except ConfigValidationError as e:
        print(f"ERROR: Configuración inválida. {e}")
        return
    viajes = cargar_salidas_desde_excel(ruta_excel)
    if not viajes:
        print("ERROR: No se encontraron viajes comerciales en el Excel.")
        return

    gestor = GestorDeLogistica(config)
    modo_verbose = bool(config.get("modo_verbose", False))
    opt_iter = config.get("optimizacion_iterativa", {}) or {}
    max_iter = int(opt_iter.get("max_iteraciones", 1))

    mejor_turnos = None
    mejor_bloques = None
    mejor_eventos_bus = None
    mejor_metadata = None
    mejor_status_f1 = mejor_status_f2 = mejor_status_f3 = ""
    mejor_conteo = 999999

    for iteracion in range(max_iter):
        seed_actual = (random_seed or 42) + iteracion * 1000
        if max_iter > 1:
            print(f"\n--- Iteración {iteracion + 1}/{max_iter} (seed={seed_actual}) ---")

        # Flujo: Fase 1 -> Fase 2 -> Fase 3.
        try:
            print("Iniciando Fase 1 (Buses) ...")
            bloques_bus, eventos_bus, status_f1 = resolver_diagramacion_buses(
                config,
                viajes,
                gestor,
                random_seed=seed_actual,
                verbose=modo_verbose,
            )
            if not bloques_bus:
                print("No se generaron bloques de buses.")
                continue
            ids_fase1 = set()
            for bloque in bloques_bus:
                for ev in bloque:
                    if isinstance(ev, dict) and "id" in ev:
                        ids_fase1.add(ev["id"])
            ids_viajes = {v["id"] for v in viajes}
            if ids_fase1 != ids_viajes and iteracion == 0:
                faltan = ids_viajes - ids_fase1
                if faltan:
                    print(f"  [AVISO] Viajes sin asignar en Fase 1: {len(faltan)}")
            elif ids_fase1 == ids_viajes and iteracion == 0:
                print(f"  [OK] Mismos viajes: {len(ids_viajes)} en Fase 1.")
        except Exception as e:
            print(f"[ERROR] Error en Fase 1: {e}")
            if iteracion == 0:
                import traceback
                traceback.print_exc()
            continue

        try:
            print("Iniciando Fase 2 (Conductores) ...")
            turnos_seleccionados, metadata_tareas, status_f2 = resolver_diagramacion_conductores(
                config,
                viajes,
                bloques_bus,
                gestor,
                verbose=modo_verbose,
            )
        except Exception as e:
            print(f"[ERROR] Error en Fase 2: {e}")
            if iteracion == 0:
                import traceback
                traceback.print_exc()
            continue

        try:
            print("Iniciando Fase 3 (Unión de Conductores) ...")
            turnos_seleccionados, status_f3 = resolver_union_conductores(
                config,
                turnos_seleccionados,
                metadata_tareas,
                viajes,
                gestor,
                verbose=modo_verbose,
                seed_externo=seed_actual,
            )
        except Exception as e:
            print(f"[ERROR] Error en Fase 3: {e}")
            if iteracion == 0:
                import traceback
                traceback.print_exc()
            continue

        conteo = len(turnos_seleccionados) if turnos_seleccionados else 999999
        if conteo < mejor_conteo:
            mejor_conteo = conteo
            mejor_turnos = turnos_seleccionados
            mejor_bloques = bloques_bus
            mejor_eventos_bus = eventos_bus
            mejor_metadata = metadata_tareas
            mejor_status_f1, mejor_status_f2, mejor_status_f3 = status_f1, status_f2, status_f3
            if max_iter > 1:
                print(f"  [OK] Nueva mejor solución: {conteo} conductores")

    if mejor_turnos is None:
        print("[ERROR] No se pudo generar ninguna solución válida.")
        return

    turnos_seleccionados = mejor_turnos
    bloques_bus = mejor_bloques
    eventos_bus = mejor_eventos_bus
    metadata_tareas = mejor_metadata
    status_f1, status_f2, status_f3 = mejor_status_f1, mejor_status_f2, mejor_status_f3

    ruta_salida_abs = os.path.abspath(ruta_salida)
    directorio_salida = os.path.dirname(ruta_salida_abs)
    
    # Asegurar que el directorio de salida existe
    if directorio_salida and not os.path.exists(directorio_salida):
        try:
            os.makedirs(directorio_salida, exist_ok=True)
            print(f"[OK] Directorio de salida creado: {directorio_salida}")
        except Exception as e_dir:
            print(f"[ERROR] No se pudo crear el directorio de salida: {directorio_salida}")
            print(f"  Error: {e_dir}")
            return
    
    print(f"\n{'=' * 80}")
    print(f"EXPORTANDO RESULTADOS")
    print(f"{'=' * 80}")
    print(f"  Ruta de salida: {ruta_salida_abs}")
    print(f"  Directorio: {directorio_salida}")
    print(f"  Directorio existe: {os.path.exists(directorio_salida) if directorio_salida else 'N/A'}")
    print(f"{'=' * 80}\n")
    
    # Validar datos antes de exportar
    print(f"\n{'=' * 80}")
    print(f"VALIDACIÓN ANTES DE EXPORTAR")
    print(f"{'=' * 80}")
    print(f"  Bloques de buses: {len(bloques_bus) if bloques_bus else 0}")
    print(f"  Turnos recibidos (Fase 3): {len(turnos_seleccionados) if turnos_seleccionados else 0}")
    print(f"  Viajes comerciales: {len(viajes) if viajes else 0}")
    print(f"  Ruta de salida: {ruta_salida_abs}")
    print(f"  Directorio existe: {os.path.exists(directorio_salida) if directorio_salida else False}")
    print(f"{'=' * 80}\n")
    
    if not bloques_bus or len(bloques_bus) == 0:
        print("  [ERROR] No hay bloques de buses para exportar!")
        return
    
    if not turnos_seleccionados or len(turnos_seleccionados) == 0:
        print("  [ERROR] No hay turnos seleccionados para exportar!")
        return

    # Filtro final: no exportar conductores sin tareas (InS/FnS solos = innecesarios).
    # Considerar comerciales por id, _tmp_id y por tareas en metadata (ej. _ev_bus_idx) para 100% cobertura.
    ids_comerciales = set()
    for v in (viajes or []):
        for key in (v.get("id"), v.get("_tmp_id")):
            if key is not None:
                ids_comerciales.add(key)
                ids_comerciales.add(str(key))
    # Incluir tids que aparecen en metadata_tareas (viajes de bloques con id sintético _ev_*)
    for tid in (metadata_tareas or {}):
        if tid is not None:
            ids_comerciales.add(tid)
            ids_comerciales.add(str(tid))
    turnos_con_comerciales = [
        t for t in turnos_seleccionados
        if any(
            tid in ids_comerciales or str(tid) in ids_comerciales
            for tid, _ in t.get("tareas_con_bus", [])
        )
    ]
    eliminados = len(turnos_seleccionados) - len(turnos_con_comerciales)
    if eliminados > 0:
        print(f"  [FILTRO] Excluidos {eliminados} conductores sin eventos comerciales (no exportados)")
        turnos_seleccionados = turnos_con_comerciales

    conductores_exportados = None
    try:
        print("Iniciando exportación de resultados...")
        resultado_export = exportar_resultado_excel(
            config,
            bloques_bus,
            turnos_seleccionados,
            viajes,
            metadata_tareas,
            status_f1,
            status_f2,
            ruta_salida_abs,
            gestor=gestor,
            verbose=modo_verbose,
            status_f3=status_f3,
            eventos_bus=eventos_bus,
        )
        print(f"[OK] Exportacion completada: {ruta_salida_abs}")
        conductores_exportados = (
            resultado_export.get("conductores_exportados")
            if isinstance(resultado_export, dict) else None
        )
    except Exception as e:
        print(f"  [ERROR] Error en exportacion: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    # Verificar que el archivo se generó correctamente
    if os.path.exists(ruta_salida_abs):
        tamaño = os.path.getsize(ruta_salida_abs)
        print(f"\n{'=' * 80}")
        print(f"VERIFICACIÓN FINAL DEL ARCHIVO GENERADO")
        print(f"{'=' * 80}")
        print(f"  Archivo: {ruta_salida_abs}")
        print(f"  Tamaño: {tamaño:,} bytes ({tamaño / 1024:.2f} KB)")
        print(f"  Existe: SÍ")
        print(f"{'=' * 80}\n")
    else:
        print(f"\n{'=' * 80}")
        print(f"[ADVERTENCIA] EL ARCHIVO NO SE GENERO")
        print(f"{'=' * 80}")
        print(f"  Ruta esperada: {ruta_salida_abs}")
        print(f"  Directorio actual: {os.getcwd()}")
        print(f"{'=' * 80}\n")

    print("\n" + "=" * 80)
    print("--- RESUMEN FINAL DEL DIAGRAMADOR ---")
    print("=" * 80)
    print(f"Total de viajes comerciales procesados: {len(viajes)}")
    print(f"Total de buses utilizados (bloques): {len(bloques_bus)}")
    n_cond = conductores_exportados if conductores_exportados is not None else len(turnos_seleccionados)
    print(f"Total de conductores exportados (TurnosConductores / EventosCompletos): {n_cond}")
    if conductores_exportados is not None and conductores_exportados != len(turnos_seleccionados):
        print(f"  (turnos generados en Fase 3: {len(turnos_seleccionados)}; exportados con al menos un Comercial: {conductores_exportados})")
    print(f"Estado Optimización Fase 1 (Buses): {status_f1}")
    print(f"Estado Optimización Fase 2 (Conductores): {status_f2}")
    print(f"Estado Optimización Fase 3 (Unión de Conductores): {status_f3}")
    print("=" * 80)


if __name__ == "__main__":
    main()

