# Resumen de Validaciones Implementadas para FnS

## Problemas Identificados y Corregidos

### 1. Duración del FnS

**Problema:** El FnS tenía duración de 1 minuto (09:19 a 09:20) cuando debería tener 0 minutos.

**Solución Implementada:**
- **En `_evento_a_fila_eventos_completos` (excel_writer.py, línea 406-435):**
  - Se detecta si el evento es FnS y se fuerza `fin_val = inicio_val` antes de calcular la duración.
  - Se fuerza `duracion_min = 0` para eventos FnS.
  
- **En creación/corrección de FnS (excel_writer.py, líneas 2832-2833, 2879-2880, 2909-2914):**
  - Todos los FnS creados tienen `inicio == fin`.
  - Normalización final: todos los FnS existentes se normalizan para tener `fin = inicio`.

**Resultado:** El FnS siempre tiene duración 0 (inicio == fin).

### 2. Eventos Después del FnS

**Problema:** Había eventos (Parada, Comercial) asignados al conductor después del FnS, lo cual no debería ocurrir ya que el FnS marca el fin del turno.

**Soluciones Implementadas:**

#### A. Filtrado en Exportación (excel_writer.py, líneas 1440-1469)
- Se busca el FnS real del conductor en `eventos_conductores`.
- Se filtran todos los eventos que empiezan después del fin del FnS (excepto el FnS mismo).
- Los eventos comerciales se mantienen (son la base del diagrama), pero se filtran si están después del FnS.

#### B. Filtrado de Eventos de Bus (excel_writer.py, líneas 1546-1560)
- Se busca el FnS del conductor antes de asignar eventos de bus.
- Si un evento de bus ocurre después del FnS, se elimina la asignación del conductor (el evento del bus puede seguir existiendo sin conductor).

#### C. Filtrado Final Post-Corrección (excel_writer.py, líneas 2894-2930)
- Después de todas las correcciones, se realiza un filtrado final:
  - Se agrupan eventos por conductor.
  - Se busca el FnS de cada conductor.
  - Se eliminan todos los eventos que empiezan después del fin del FnS.
  - Se reordena la lista final.

#### D. Validación Explícita (validar_jornada_conductores.py, líneas 173-223)
- Nueva función `validar_eventos_despues_fns`:
  - Detecta eventos después del FnS para cada conductor.
  - Valida que el FnS tenga duración 0 (inicio == fin).
  - Reporta errores detallados con información del conductor, evento FnS y eventos después.
- Integrada en `validar_jornada_completa` para ejecutarse automáticamente.

## Validaciones Agregadas

### Validación 1: Duración del FnS
- **Tipo:** `fns_duracion_incorrecta`
- **Mensaje:** "Conductor X: FnS debe tener duración 0 (inicio == fin). Actual: inicio=Y, fin=Z, duración=W"
- **Ubicación:** `validar_eventos_despues_fns` en `validar_jornada_conductores.py`

### Validación 2: Eventos Después del FnS
- **Tipo:** `evento_despues_fns`
- **Mensaje:** "Conductor X: evento 'TIPO' después del FnS (inicio=Y >= fin FnS=Z). El FnS marca el fin del turno, no debe haber más eventos."
- **Ubicación:** `validar_eventos_despues_fns` en `validar_jornada_conductores.py`

## Flujo de Corrección

1. **Creación de FnS:** Se crea con `inicio == fin` (duración 0).
2. **Normalización:** Todos los FnS se normalizan para tener `fin = inicio`.
3. **Filtrado Pre-Exportación:** Se filtran eventos después del FnS antes de unificar eventos.
4. **Filtrado de Eventos de Bus:** Se elimina asignación de conductor a eventos de bus después del FnS.
5. **Filtrado Final:** Se eliminan eventos después del FnS después de todas las correcciones.
6. **Validación:** Se ejecuta validación explícita que reporta cualquier evento después del FnS o FnS con duración incorrecta.

## Archivos Modificados

1. **`io/exporters/excel_writer.py`:**
   - `_evento_a_fila_eventos_completos`: Fuerza duración 0 para FnS.
   - Filtrado de eventos después del FnS (múltiples puntos).
   - Normalización final de FnS.
   - Filtrado final post-corrección.

2. **`io/validar_jornada_conductores.py`:**
   - Nueva función `validar_eventos_despues_fns`.
   - Integrada en `validar_jornada_completa`.

## Resultado Esperado

- ✅ Todos los FnS tienen duración 0 (inicio == fin).
- ✅ No hay eventos asignados al conductor después del FnS.
- ✅ La validación reporta cualquier violación de estas reglas.
- ✅ Los eventos de bus después del FnS no tienen conductor asignado (el bus continúa sin conductor).
