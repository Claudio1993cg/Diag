"""
Análisis completo de cumplimiento de todas las reglas de jornada y relevo.
"""
import json
import sys
from pathlib import Path

# Agregar ruta del proyecto
sys.path.insert(0, str(Path(__file__).parent.parent))

from diagramador_optimizado.core.domain.logistica import GestorDeLogistica
from diagramador_optimizado.io.validar_jornada_conductores import (
    validar_jornada_completa,
    _es_deposito,
    _es_deposito_o_punto_relevo,
    _mismo_nodo,
)


def cargar_configuracion():
    """Carga configuración desde JSON."""
    config_path = Path(__file__).parent / "configuracion.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def analizar_puntos_relevo(gestor: GestorDeLogistica, config: dict):
    """Regla 1: Puntos de relevo dinámicos."""
    print("\n" + "="*80)
    print("REGLA 1: PUNTOS DE RELEVO DINÁMICOS")
    print("="*80)
    
    nodos = config.get("nodos", [])
    puntos_relevo_config = config.get("puntos_relevo", [])
    deposito_base = gestor.deposito_base
    
    print(f"Depósito configurado: {deposito_base}")
    print(f"Puntos de relevo en config: {puntos_relevo_config}")
    print(f"\nAnálisis por nodo:")
    
    errores = []
    for nodo in nodos:
        puede_relevo, tiempo = gestor.puede_hacer_relevo_en_nodo(nodo)
        esperado_en_config = nodo in puntos_relevo_config
        
        # Verificar desplazamiento al depósito
        hab_desplaz, t_desplaz = gestor.buscar_info_desplazamiento(nodo, deposito_base, 0)
        hab_vuelta, t_vuelta = gestor.buscar_info_desplazamiento(deposito_base, nodo, 0)
        
        estado = "OK" if puede_relevo else "NO"
        print(f"  {estado} {nodo}:")
        print(f"    - puede_hacer_relevo_en_nodo: {puede_relevo} (tiempo: {tiempo or 'N/A'})")
        print(f"    - En puntos_relevo config: {esperado_en_config}")
        print(f"    - Desplazamiento {nodo}->{deposito_base}: habilitado={hab_desplaz}, tiempo={t_desplaz}")
        print(f"    - Desplazamiento {deposito_base}->{nodo}: habilitado={hab_vuelta}, tiempo={t_vuelta}")
        
        # Validar consistencia
        if esperado_en_config and not puede_relevo:
            errores.append(f"{nodo} está en puntos_relevo pero no es relevo válido")
        if not esperado_en_config and puede_relevo:
            print(f"    [AVISO] {nodo} es relevo valido pero no esta en puntos_relevo (puede ser intencional)")
    
    if errores:
        print(f"\n[ERROR] ERRORES ENCONTRADOS:")
        for e in errores:
            print(f"  - {e}")
        return False
    else:
        print(f"\n[OK] Todos los puntos de relevo son consistentes")
        return True


def analizar_eventos_conductores(eventos, gestor: GestorDeLogistica, config: dict):
    """Analiza eventos de conductores para verificar todas las reglas."""
    print("\n" + "="*80)
    print("REGLA 2-7: ANÁLISIS DE EVENTOS DE CONDUCTORES")
    print("="*80)
    
    # Agrupar por conductor
    por_conductor = {}
    for ev in eventos:
        cid = ev.get("conductor")
        if cid is None:
            continue
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            continue
        if cid not in por_conductor:
            por_conductor[cid] = []
        por_conductor[cid].append(ev)
    
    print(f"Total conductores encontrados: {len(por_conductor)}")
    
    # Estadísticas
    stats = {
        "con_ins": 0,
        "con_fns": 0,
        "sin_teletransportacion": 0,
        "ultimo_en_deposito": 0,
        "evento_antes_fns_ok": 0,
        "ins_fns_sin_bus": 0,
        "desplazamiento_sin_bus": 0,
        "jornada_ok": 0,
    }
    
    limite_jornada = config.get("limite_jornada", 600)
    errores_por_regla = {
        "regla_2_ins_fns": [],
        "regla_3_teletransportacion": [],
        "regla_4_ultimo_deposito": [],
        "regla_5_evento_antes_fns": [],
        "regla_6_sin_bus": [],
        "regla_7_jornada": [],
    }
    
    for cid, evs in sorted(por_conductor.items()):
        evs_ord = sorted(evs, key=lambda e: (e.get("inicio", 0), e.get("fin", 0)))
        
        # REGLA 2: Todo conductor tiene InS y FnS
        tiene_ins = any(str(e.get("evento", "")).strip().upper() == "INS" for e in evs_ord)
        tiene_fns = any(str(e.get("evento", "")).strip().upper() == "FNS" for e in evs_ord)
        if tiene_ins:
            stats["con_ins"] += 1
        if tiene_fns:
            stats["con_fns"] += 1
        if not tiene_ins or not tiene_fns:
            errores_por_regla["regla_2_ins_fns"].append(
                f"Conductor {cid}: InS={tiene_ins}, FnS={tiene_fns}"
            )
        
        # REGLA 3: Sin teletransportaciones (continuidad de nodos)
        continuidad_ok = True
        for i in range(1, len(evs_ord)):
            ant = evs_ord[i - 1]
            act = evs_ord[i]
            dest_ant = (ant.get("destino") or "").strip()
            orig_act = (act.get("origen") or "").strip()
            if not _mismo_nodo(dest_ant, orig_act, gestor):
                continuidad_ok = False
                errores_por_regla["regla_3_teletransportacion"].append(
                    f"Conductor {cid}: evento {ant.get('evento')} termina en '{dest_ant}' "
                    f"pero siguiente '{act.get('evento')}' inicia en '{orig_act}'"
                )
        if continuidad_ok:
            stats["sin_teletransportacion"] += 1
        
        # REGLA 4: Último evento en depósito
        ultimo = evs_ord[-1] if evs_ord else None
        if ultimo:
            dest_ultimo = (ultimo.get("destino") or "").strip()
            if _es_deposito(dest_ultimo, gestor):
                stats["ultimo_en_deposito"] += 1
            else:
                errores_por_regla["regla_4_ultimo_deposito"].append(
                    f"Conductor {cid}: último evento termina en '{dest_ultimo}' (debe ser depósito)"
                )
        
        # REGLA 5: Evento antes del FnS en depósito/relevo o con vacío habilitado
        eventos_sin_fns = [e for e in evs_ord if str(e.get("evento", "")).strip().upper() != "FNS"]
        if eventos_sin_fns:
            evento_antes_fns = eventos_sin_fns[-1]
            dest_antes_fns = (evento_antes_fns.get("destino") or "").strip()
            if _es_deposito_o_punto_relevo(dest_antes_fns, gestor):
                stats["evento_antes_fns_ok"] += 1
            else:
                # Verificar si tiene vacío habilitado
                deposito_ref = gestor.deposito_base
                tiempo_vacio, _ = gestor.buscar_tiempo_vacio(
                    dest_antes_fns, deposito_ref, evento_antes_fns.get("fin", evento_antes_fns.get("inicio", 0))
                )
                if tiempo_vacio and tiempo_vacio > 0:
                    stats["evento_antes_fns_ok"] += 1
                else:
                    errores_por_regla["regla_5_evento_antes_fns"].append(
                        f"Conductor {cid}: evento antes del FnS termina en '{dest_antes_fns}' "
                        f"(no es depósito/relevo ni tiene vacío habilitado)"
                    )
        
        # REGLA 6: InS, FnS y Desplazamiento sin bus
        ins_fns_ok = True
        desplazamiento_ok = True
        for ev in evs_ord:
            tipo = str(ev.get("evento", "")).strip().upper()
            bus = ev.get("bus")
            tiene_bus = bus is not None and str(bus).strip() != "" and bus != 0
            
            if tipo in ("INS", "FNS") and tiene_bus:
                ins_fns_ok = False
                errores_por_regla["regla_6_sin_bus"].append(
                    f"Conductor {cid}: {tipo} tiene bus={bus}"
                )
            if tipo == "DESPLAZAMIENTO" and tiene_bus:
                desplazamiento_ok = False
                errores_por_regla["regla_6_sin_bus"].append(
                    f"Conductor {cid}: Desplazamiento tiene bus={bus}"
                )
        
        if ins_fns_ok:
            stats["ins_fns_sin_bus"] += 1
        if desplazamiento_ok:
            stats["desplazamiento_sin_bus"] += 1
        
        # REGLA 7: Límite de jornada
        if evs_ord:
            ins_event = next((e for e in evs_ord if str(e.get("evento", "")).strip().upper() == "INS"), None)
            fns_event = next((e for e in evs_ord if str(e.get("evento", "")).strip().upper() == "FNS"), None)
            if ins_event and fns_event:
                inicio = ins_event.get("inicio", 0)
                fin = fns_event.get("fin", fns_event.get("inicio", 0))
                duracion = fin - inicio
                if duracion <= limite_jornada:
                    stats["jornada_ok"] += 1
                else:
                    errores_por_regla["regla_7_jornada"].append(
                        f"Conductor {cid}: jornada {duracion} min > {limite_jornada} min "
                        f"(InS: {inicio}, FnS: {fin})"
                    )
    
    # Mostrar estadísticas
    print(f"\nEstadísticas:")
    print(f"  - Conductores con InS: {stats['con_ins']}/{len(por_conductor)}")
    print(f"  - Conductores con FnS: {stats['con_fns']}/{len(por_conductor)}")
    print(f"  - Sin teletransportaciones: {stats['sin_teletransportacion']}/{len(por_conductor)}")
    print(f"  - Último evento en depósito: {stats['ultimo_en_deposito']}/{len(por_conductor)}")
    print(f"  - Evento antes FnS OK: {stats['evento_antes_fns_ok']}/{len(por_conductor)}")
    print(f"  - InS/FnS sin bus: {stats['ins_fns_sin_bus']}/{len(por_conductor)}")
    print(f"  - Desplazamiento sin bus: {stats['desplazamiento_sin_bus']}/{len(por_conductor)}")
    print(f"  - Jornada <= {limite_jornada} min: {stats['jornada_ok']}/{len(por_conductor)}")
    
    # Mostrar errores
    total_errores = sum(len(errs) for errs in errores_por_regla.values())
    if total_errores > 0:
        print(f"\n[ERROR] ERRORES ENCONTRADOS ({total_errores} total):")
        for regla, errs in errores_por_regla.items():
            if errs:
                print(f"\n  {regla.upper()}: {len(errs)} errores")
                for e in errs[:5]:  # Mostrar primeros 5
                    print(f"    - {e}")
                if len(errs) > 5:
                    print(f"    ... y {len(errs) - 5} mas")
        return False
    else:
        print(f"\n[OK] Todas las reglas se cumplen correctamente")
        return True


def main():
    """Ejecuta análisis completo."""
    print("="*80)
    print("ANÁLISIS COMPLETO DE CUMPLIMIENTO DE REGLAS")
    print("="*80)
    
    # Cargar configuración
    config = cargar_configuracion()
    gestor = GestorDeLogistica(config)
    
    # Analizar puntos de relevo
    regla1_ok = analizar_puntos_relevo(gestor, config)
    
    # Para analizar eventos, necesitamos ejecutar el diagramador o cargar resultados
    # Por ahora, ejecutamos validación estándar
    print("\n" + "="*80)
    print("NOTA: Para análisis completo de eventos, ejecutar diagramador primero")
    print("="*80)
    print("\nEjecutando validación estándar...")
    print("(Ejecuta el diagramador completo para análisis detallado de eventos)")
    
    # Ejecutar validación de relevo
    from diagramador_optimizado.validar_relevo_y_jornada import main as validar_main
    print("\nEjecutando validación de relevo y jornada...")
    validar_main()
    
    print("\n" + "="*80)
    print("RESUMEN")
    print("="*80)
    print(f"Regla 1 (Puntos de relevo dinamicos): {'[OK]' if regla1_ok else '[ERROR]'}")
    print("\nPara análisis completo de eventos, ejecuta:")
    print("  python diagramador_optimizado/cli/main.py")
    print("Y luego revisa la salida de validación.")


if __name__ == "__main__":
    main()
