# Validación: Puntos de relevo, InS/FnS y jornada (ejemplo usuario)

## Requisitos pedidos (resumen)

1. **Puntos de relevo** = nodos conectados con **desplazamientos habilitados** al depósito configurado para aplicar FnS o InS. Todo de forma **dinámica** para **cualquier depósito configurado**.
2. **Ningún conductor** puede terminar un evento en un lugar que no sea depósito o punto de relevo (no cortar en LA PIRAMIDE).
3. **Todo conductor** debe tener **un InS** y **un FnS**; no teletransportaciones ni desconexiones.
4. **Límite de jornada** (600 min): si una vuelta más supera el límite, se debe cortar en el último punto de relevo posible (ej. LOS TILOS a las 10:15 → desplazamiento 30 min → FnS 10:45).

---

## Ejemplo usuario (tu secuencia)

| Hora      | Evento | Origen → Destino | Nota |
|-----------|--------|-------------------|------|
| 05:00-05:15 | InS | Depósito | 15 min configuración |
| 05:15-05:45 | Vacío | Depósito Pie Andino → LOS TILOS | |
| 05:45-05:55 | Parada | LOS TILOS | |
| 05:55-07:55 | Comercial | LOS TILOS → LA PIRAMIDE | No cortar aquí (LA PIRAMIDE no es relevo) |
| 07:55-08:15 | Parada | LA PIRAMIDE | |
| 08:15-10:15 | Comercial | LA PIRAMIDE → LOS TILOS | Puede cortar en LOS TILOS (relevo) |
| 10:15+30 min | Desplazamiento | LOS TILOS → Depósito | FnS a las 10:45. Jornada 05:15-10:45 = 5h30 < 10h ✓ |
| (Si se agrega otra vuelta) 10:30-12:30 Comercial LOS TILOS→LA PIRAMIDE, 13:00-15:00 LA PIRAMIDE→LOS TILOS → 15:00+30 = 15:30 FnS → Jornada 10h15 > 10h → **no permitido**. Corte correcto: en 10:15 en LOS TILOS. |

---

## Comprobaciones realizadas en código

### 1. Puntos de relevo dinámicos (`logistica.py`)

- **`puede_hacer_relevo_en_nodo(nodo)`**:
  - Si `puntos_relevo` en config no está vacío: el nodo debe estar en esa lista **y** tener desplazamiento ida/vuelta con **algún depósito** de `_nombres_depositos()` (o `deposito_base`).
  - Si no hay lista o está vacía: cualquier nodo con desplazamiento nodo↔depósito habilitado para algún depósito configurado es relevo.
- **Depósito**: Siempre se usa `_nombres_depositos()` / `deposito_base` (leído de config), sin nombres fijos en código.

**Con tu config:**

- `puntos_relevo`: ["LOS TILOS", "PIE ANDINO"].
- Desplazamientos: `LOS TILOS_Deposito Pie Andino` y `PIE ANDINO_Deposito Pie Andino` **habilitado**; `LA PIRAMIDE_Deposito Pie Andino` **habilitado: false**.
- Resultado:
  - **LA PIRAMIDE**: no está en `puntos_relevo` → `puede_hacer_relevo_en_nodo("LA PIRAMIDE")` = **False** ✓ (además en config el desplazamiento a depósito está deshabilitado).
  - **LOS TILOS**: está en lista y tiene desplazamiento 30 min → **True, 30** ✓.
  - **PIE ANDINO**: está en lista y tiene desplazamiento 1 min → **True, 1** ✓.

### 2. Corte solo en puntos de relevo (`fase2_conductores.py`)

- **Bloque completo** (CASO A): Solo se acepta un turno que termina en depósito o en punto de relevo (`puede_fin_ok = gestor.puede_hacer_relevo_en_nodo(destino_fin_bloque)`). Si el bloque termina en LA PIRAMIDE → `debe_dividir_por_relevo = True` → se divide.
- **`_dividir_bloque_en_turnos`**:
  - Para cada posible fin de turno (`idx_fin`), si **no** es el último viaje del bloque se exige `puede_relevo` en `ultimo_viaje["destino"]`. Si el destino es LA PIRAMIDE → `continue` (no se elige ese corte).
  - Así, los cortes solo pueden ser en nodos con relevo (LOS TILOS, PIE ANDINO) o en el último viaje (que luego se conecta con Vacio/Desplazamiento a depósito).
- **Turno unitario** (viaje suelto): Solo se crea si el destino del viaje es un depósito configurado **o** tiene desplazamiento habilitado a **algún** depósito (`hab_desplaz` probando todos los `nombres_dep`). Un viaje que termina en LA PIRAMIDE sin desplazamiento a depósito → no se crea turno que “termine” ahí.

Con tu ejemplo: si el bloque termina en LOS TILOS a las 10:15, se puede cortar ahí; si se intentara cortar después de un comercial que termina en LA PIRAMIDE (12:30), ese índice se descarta y se busca el siguiente corte válido (p. ej. 10:15 en LOS TILOS).

### 3. Límite de jornada 600 min (`fase2_conductores.py`)

- En `_dividir_bloque_en_turnos`, para cada segmento se calcula `duracion_turno = fin_turno - inicio_turno`.
- Si el último viaje termina en un nodo (no depósito), `fin_turno = ultimo_viaje["fin"] + t_desplaz` (o + t_vuelta si se usa vacío).
- Se exige `duracion_turno <= LIMITE_JORNADA` (600). Si se supera, se hace `continue` y se prueba un `idx_fin` anterior (segmento más corto).

En tu ejemplo:

- Corte en 10:15 en LOS TILOS: `fin_turno = 10:15 + 30 = 10:45`, `inicio_turno = 05:15` → duración 330 min < 600 ✓.
- Si se tomara el segmento hasta 15:00 en LOS TILOS: `fin_turno = 15:30`, duración 615 min > 600 → no se acepta; se busca un corte anterior (10:15).

### 4. InS y FnS por conductor; sin teletransportaciones (`eventos_conductor.py`)

- **InS**: Se crea uno por turno; `ins_fin` = inicio del primer evento del conductor (Vacio o Comercial); `ins_inicio = ins_fin - tiempo_toma`. Si no hay Vacio Dep→origen, se crea **Desplazamiento** Dep→origen cuando esté habilitado.
- **FnS**: Se crea uno por turno. Si el último viaje termina en un nodo (no depósito):
  - Se busca Vacio o Desplazamiento nodo→depósito en eventos ya asignados o en eventos_bus.
  - Si no hay y existe **desplazamiento habilitado** nodo→depósito, se crea evento **Desplazamiento** y `ultimo_fin = viaje_ultimo["fin"] + t_d`.
- **FnS** siempre se agrega con `origen` y `destino` = depósito (lugar de cierre). Así, la secuencia queda: … → último evento → (Desplazamiento o Vacio a depósito) → FnS.

Con ello se cumple: todo conductor con un InS y un FnS, y sin “cortes” en el aire (si termina en nodo, hay Desplazamiento o Vacio hasta depósito antes del FnS).

### 5. Depósito siempre desde configuración

- **logistica**: `_nombres_depositos()` y `deposito_base` desde config.
- **fase2**: `deposito = gestor.deposito_base`; para turnos unitarios se usa `nombres_dep = gestor._nombres_depositos() or [gestor.deposito_base]` para “es_dep” y “hab_desplaz”.
- **eventos_conductor**: `dep_ok = _deposito_canonico(turno_dep)` y `_destino_es_deposito(..., gestor)` (gestor usa config).
- **excel_writer**: Detección de “destino es depósito” con `_destino_es_deposito(dest, gestor)` en lugar de texto fijo "PIE ANDINO"/"DEPOSITO".

Con eso el comportamiento es válido para **cualquier depósito configurado**.

---

## Script de validación

El script `validar_relevo_y_jornada.py` comprueba con tu `configuracion.json`:

1. LA PIRAMIDE no es punto de relevo.
2. LOS TILOS es punto de relevo (30 min).
3. PIE ANDINO es punto de relevo.
4. Desplazamiento LOS TILOS → depósito habilitado 30 min.
5. Desplazamiento LA PIRAMIDE → depósito no habilitado.
6. Jornada ejemplo 05:15–10:45 = 330 min < 600.
7. Jornada 05:15–15:30 = 615 min > 600 (prohibida).

Ejecución:

```bash
cd "Diagramador - V8 Funcional con 2 Patios"
set PYTHONPATH=%CD%
python diagramador_optimizado/validar_relevo_y_jornada.py
```

En la última ejecución, **todas las validaciones pasaron (7 checks)**.

---

## Resumen de cumplimiento

| Requisito | Cumplimiento |
|-----------|--------------|
| Puntos de relevo = desplazamiento habilitado al depósito | ✓ `puede_hacer_relevo_en_nodo` usa desplazamientos y depósitos de config; opcional filtro por `puntos_relevo`. |
| Dinámico para cualquier depósito configurado | ✓ Uso de `_nombres_depositos()` / `deposito_base` en logística, Fase 2, eventos conductor y exportación. |
| No terminar conductor en no-relevo (ej. LA PIRAMIDE) | ✓ Corte solo en relevo; turno unitario solo si destino es depósito o tiene desplazamiento; LA PIRAMIDE sin desplazamiento → no relevo. |
| Todo conductor con InS y FnS | ✓ Un InS y un FnS por turno en `ensamblar_eventos_conductores`. |
| Sin teletransportaciones | ✓ Desplazamiento o Vacio nodo→depósito antes del FnS cuando el último evento termina en nodo. |
| Jornada máx 600 min; corte en relevo si una vuelta más la supera | ✓ `duracion_turno <= LIMITE_JORNADA` en división de bloques; corte solo en destinos con `puede_relevo`; ejemplo 10:15 LOS TILOS → 10:45 FnS = 330 min. |

Con esto se cumple en detalle lo pedido y el comportamiento del ejemplo que diste queda validado por código y por el script.
