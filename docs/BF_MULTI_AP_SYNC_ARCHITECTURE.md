# Arquitectura BF multi-AP sincronizada por 5G

## 1. Estado del documento

Este documento describe la dirección técnica confirmada para investigar
sensing WiFi BF coordinado entre dos AP sin backhaul. Es una arquitectura de
referencia y un contrato conceptual; no afirma que el perfil distribuido, el
scheduler ni el sensing 4×4 estén implementados.

La selección hardware confirmada es **B210 para recibir 5G + X410 para WiFi
4×4 en cada AP**. Los valores de timing indicados como baseline siguen siendo
parámetros de validación, no constantes científicas definitivas.

Antes de desplegar esa arquitectura final se ejecutará una fase de validación
con **un X410 por AP, WiFi 2×2 y RX 5G en el mismo dispositivo**. Su objetivo es
probar el algoritmo de sincronización con un único timekeeper local, aceptando
una capacidad de sensing espacial inferior.

Lo implementado y medido en la validación X410-only (dos AP, 4×3 + 5G, resultados
de sincronización y sensing, desviaciones respecto a este documento) se registra
en [`BF_MULTI_AP_TESTBED_STATUS.md`](BF_MULTI_AP_TESTBED_STATUS.md).

## 2. Motivación y novelty buscada

En BF convencional, AP o estaciones coordinadas intercambian señalización para
negociar sounding, quiet time, quién transmite y quién escucha. El escenario
objetivo elimina ese backhaul de coordinación durante la operación:

- Todos los AP observan la misma celda 5G comercial.
- Una configuración previa asigna turnos complementarios.
- El downlink 5G proporciona epoch y heartbeat OTA comunes.
- Cada AP calcula localmente los mismos ciclos futuros.
- Los USRP ejecutan acciones WiFi en timestamps programados.

El claim que se pretende evaluar es que la referencia 5G permite coordinar AP
WiFi desconectados entre sí, reduciendo overhead de negociación y mejorando la
alineación temporal del sounding/sensing. No se afirma que 5G proporcione una
fase RF común a las portadoras WiFi.

## 3. Topología seleccionada

```text
                         celda 5G comercial
                         SSB / PBCH / PSS
                           /           \
                          v             v
                 +----------------+  +----------------+
                 | AP A           |  | AP B           |
                 |                |  |                |
                 | B210: RX 5G    |  | B210: RX 5G    |
                 |      |         |  |      |         |
                 | reloj/epoch    |  | reloj/epoch    |
                 | local común    |  | local común    |
                 |      |         |  |      |         |
                 | X410: WiFi 4x4 |  | X410: WiFi 4x4 |
                 +----------------+  +----------------+
                         <---- enlace WiFi BF ---->
```

Cada X410 puede actuar por TDD como transmisor 4×4 en un turno y receptor 4×4
en otro. Usar el mismo X410 local para TX y RX no convierte el experimento en
monostático mientras la observación científica de un burst transmitido por A
se realice en B, y viceversa.

La asignación concreta de AP A/B a PC1-PC4 está pendiente. PC5 seguirá siendo
orquestador, almacenamiento y ejecutor del modelo externo, pero no decide el
instante RF mediante SSH.

### 3.1 Arquitectura de validación previa X410-only

```text
                         celda 5G comercial
                           /           \
                          v             v
                 +----------------+  +----------------+
                 | X410 AP A      |  | X410 AP B      |
                 | ch0/ch1 WiFi   |  | ch0/ch1 WiFi   |
                 | 2x2 TDD        |  | 2x2 TDD        |
                 | ch2 RX 5G      |  | ch2 RX 5G      |
                 | ch3 reserva o  |  | ch3 reserva o  |
                 | diagnóstico    |  | diagnóstico    |
                 +----------------+  +----------------+
                         <---- WiFi BF 2x2 ---->
```

La numeración de canales es ilustrativa y se decidirá con el cableado real.
Las propiedades obligatorias son:

- Dos cadenas WiFi bidireccionales por TDD en cada X410.
- Una cadena distinta para RX 5G.
- Un solo timekeeper local para timestamps PSS y comandos WiFi.
- Dos X410 físicamente independientes, sin referencia 10 MHz/PPS compartida.
- La misma celda y referencia SSB como única referencia común entre AP.

Esta fase elimina deliberadamente la transformación B210→X410. Si falla, el
problema está en adquisición del epoch, tracker, agenda, comandos temporizados,
multibanda/multirrate o interferencia interna; no puede atribuirse al puente
entre dos USRP.

El precio es un WiFi 2×2 con haces más anchos, menor ganancia y peor resolución
espacial que el objetivo 4×4. Es aceptable porque se evalúa primero:

- Que ambos AP identifican el mismo ciclo.
- Que arman el ciclo siguiente con margen suficiente.
- Que A-TX/B-RX y B-TX/A-RX no colisionan.
- Que el error PSS→PPDU y su jitter son medibles y repetibles.
- Que no hay late commands, underflows, overflows o discontinuidades.

No se usará un solo X410 para representar simultáneamente los dos AP: compartir
el timekeeper entre ellos invalidaría la demostración OTA. Tampoco se reutiliza
sin cambios `testmultiband/2x2.py`; ese script usa dos canales solo TX y dos
solo RX con un tono estrecho, mientras el smoke necesita dos canales WiFi TDD,
5G simultáneo y el scheduler por ciclos.

## 4. Dominios de reloj

### 4.1 Dentro de cada AP

El B210 y el X410 necesitan una transformación temporal determinista. La opción
preferida es una referencia local común:

- 10 MHz común para frecuencia.
- PPS común para frontera temporal.
- Epoch configurado de forma coherente en ambos timekeepers.
- Retardo fijo de cables, detector y pipeline medido y registrado.

Alternativamente puede usarse una transformación calibrada explícita entre
timekeepers, pero debe tener estimación de offset, drift e incertidumbre. El
host time no es sustituto.

### 4.2 Entre AP

AP A y AP B no deben compartir el mismo clock físico en el experimento final.
Cada AP mantiene su dominio local. El elemento que los relaciona es la
observación OTA de la misma celda, trama e identidad SSB.

Un distribuidor común de 10 MHz/PPS a ambos AP es útil como baseline de banco:
permite medir el techo de rendimiento y depurar el scheduler. Debe etiquetarse
como `shared_lab_reference_baseline`, porque no demuestra coordinación OTA sin
infraestructura común.

### 4.3 Fase RF

- Los cuatro canales de un X410 comparten clock/timekeeper.
- Sus offsets complejos de cadena no se consideran cero ni reproducibles tras
  sintonizar; se calibran por sesión.
- B210 y X410 no comparten necesariamente LO, aunque compartan 10 MHz/PPS.
- AP A y AP B no tienen fase de portadora WiFi común.
- El scheduler solo promete alineación temporal dentro de una tolerancia que
  debe medirse.

## 5. Adquisición del ciclo común

Contar SSB desde el arranque local es inválido. Si A arranca en t=0 y B en
t=30 ms, ambos pueden observar la misma secuencia física con contadores locales
distintos.

El proceso por AP es:

1. Buscar la celda objetivo y validar su identidad.
2. Detectar PSS/SSS y estimar el offset exacto dentro del bloque.
3. Decodificar PBCH/MIB y extraer la información necesaria para identificar la
   misma trama/ciclo en ambos AP.
4. Registrar el timestamp del PSS de referencia: X410 durante la validación
   previa y B210 en la arquitectura final.
5. Estimar periodo, fase, jitter y confianza del tracker.
6. Obtener el PSS objetivo futuro en el dominio X410 local: directamente en la
   fase X410-only o mediante la transformación B210↔X410 en la fase final.
7. Mantener seguimiento rápido con PSS/SSS.
8. Revalidar PBCH/MIB periódicamente y ante pérdida de lock, cambio de celda,
   discontinuidad o wrap relevante.

La hipótesis inicial de un SSB cada 20 ms y cinco oportunidades en 100 ms debe
medirse. NR permite otras periodicidades y varios SSB dentro de un burst; el
tracker debe fijarse en la misma referencia inequívoca.

## 6. De PSS B210 a timestamp X410

Esta transformación solo aparece al migrar a la arquitectura final. Durante la
fase X410-only, la cadena 5G y el scheduler WiFi consultan el mismo timekeeper y
el timestamp PSS ya está en el dominio de ejecución; aun así deben compensarse
retardos de detector/RF y cuantificarse la incertidumbre.

Sea:

```text
t_b210_pss = block_start_ticks + detector_offset_samples
```

La transformación local puede representarse de forma general como:

```text
t_x410 = a * t_b210 + b + d_fixed
```

donde:

- `a` captura relación de rates/drift residual.
- `b` captura diferencia de epoch.
- `d_fixed` captura retardos calibrados de antena, cable, RF, detector y
  definición del punto de referencia.
- La transformación lleva asociada una incertidumbre.

Con 10 MHz/PPS y epoch local común, idealmente `a` y `b` quedan simplificados,
pero `d_fixed` y el error del detector siguen existiendo. Nunca debe sustituirse
esta ecuación por el instante en que Python recibió el resultado.

## 7. Scheduler por ciclos

### 7.1 Agenda complementaria

Ejemplo mínimo:

```text
ciclo k, oportunidad S2: AP A TX; AP B RX
ciclo k, oportunidad S4: AP A RX; AP B TX
```

La agenda se provisiona antes de la run y contiene identidad de AP, ciclo,
oportunidad, rol, waveform, sector y límites de seguridad. No se negocia entre
AP durante la ejecución.

### 7.2 Programar el ciclo siguiente

Después de adquirir y seguir el ciclo, cada AP arma el **siguiente ciclo
completo**, aproximadamente 100 ms por adelantado. El host:

1. Selecciona muestras y pesos ya preparados.
2. Calcula el timestamp X410 futuro.
3. Encola RX y TX temporizados en UHD.
4. Comprueba ACK, late commands y eventos asíncronos.

El timekeeper X410 ejecuta la acción. No se detecta el heartbeat, se hace
`sleep(5 ms)` y se llama entonces a TX: ese patrón heredaría jitter de Linux,
Python, USB/Ethernet y scheduling.

### 7.3 Política de tardanza

Una acción con margen insuficiente no se desplaza al presente. Se marca el
ciclo como `MISSED` y el AP permanece silencioso. Transmitir tarde podría
solapar el turno contrario y destruir tanto el sensing como la seguridad RF.

### 7.4 Estados conceptuales

```text
SEARCHING -> ACQUIRING_EPOCH -> TRACKING -> LOCKED -> ARMED -> EXECUTING
                                  |           |
                                  v           v
                               HOLDOVER    MISSED_SLOT
                                  \           /
                                   -> FAILSAFE
```

Los umbrales de lock, holdover y fail-safe están pendientes de caracterización.

## 8. Timing de referencia

Baseline inicial:

```text
PSS objetivo previsto                        T
comienzo WiFi                         T + 5 ms
ventana TX                                    2 ms
paquetes                               10 HE NDP-like
separación entre comienzos                    200 us
duración aproximada total                   1.936 ms
```

Conceptos independientes:

- `lookahead`: cuánto antes se arma UHD; inicialmente un ciclo completo.
- `slot_offset`: posición WiFi respecto al PSS objetivo.
- `tx_window`: airtime reservado.
- `rx_guard_before/after`: margen de escucha, no airtime transmitido.
- `detector_uncertainty`: error del timestamp PSS/PPDU.
- `mapping_uncertainty`: error B210↔X410.

Durante caracterización el RX puede permanecer continuo. Si se usa ventana,
por ejemplo T+4 a T+8 ms, el milisegundo extra a cada lado no reduce la ventana
científica: se recorta después por timestamps. Solo tras medir sesgo y jitter
se estrechan las guardas.

## 9. Waveform WiFi y transición a 4×4

### 9.1 Baseline ya validado

El HE NDP-like actual es SISO y produce ocho estimaciones HE-LTF por paquete.
Existe a 20 y 40 MHz, con CSI `[8,122]` o `[8,242]`. El TX N310 y RX B210
actuales han demostrado alta recepción después de mejorar buffers y escritura.

### 9.2 Lo que falta para X410 4×4

No basta con enviar la waveform SISO idéntica por cuatro canales. Se necesita:

- Definir sounding espacial: secuencial por antena, ortogonal, precodificado o
  combinación controlada.
- Mantener identidad de paquete, sector, pesos y antenas en metadata.
- Aplicar calibración compleja por cadena después del tune.
- Generar un streamer de cuatro canales con timestamps idénticos.
- Recibir cuatro canales y definir CSI MIMO consistente.
- Validar potencia total, EVM, PAPR, saturación y regulación RF.
- Comprobar que el decoder y schema no confunden dimensión HE-LTF con dimensión
  de antena.

La forma de datos futura debe distinguir al menos:

```text
packet
sector_or_weight_id
tx_chain / rx_chain
he_ltf_index
subcarrier
complex_csi
device_timestamp
```

### 9.3 Repetición RF actual

El baseline HE fiable envía dos copias del mismo contador separadas 20 ms. Esa
política entra en conflicto con turnos distintos separados por SSB. Opciones a
evaluar:

- Varias copias dentro de los 2 ms.
- Una sola copia con mayor robustez.
- Repetición en otro superciclo explícitamente asignado.
- Slot adicional en la agenda.

No debe reutilizarse automáticamente la separación de 20 ms.

## 10. Calibración espacial X410

El banco `testmultiband` ha demostrado que los offsets de cadena importan y que
una calibración OTA puede corregirlos dentro de una sesión sintonizada.

Procedimiento futuro mínimo por X410:

1. Sintonizar todos los canales y comprobar lock.
2. Mantener el mismo tune durante calibración y run.
3. Emitir/recibir una referencia por cadena con geometría conocida o coupler.
4. Estimar vector complejo de corrección.
5. Verificar steering en al menos tres ángulos y registrar error.
6. Guardar frecuencia, temperatura, gains, orden físico de antenas, cables,
   pesos y digest de calibración.
7. Invalidar calibración tras retune, reinicio, cambio de cableado o deriva
   superior al umbral.

La calibración monostática de una sonda cercana no garantiza calibración del
enlace distribuido completo. Debe separarse:

- Calibración interna/local del array.
- Estimación del canal OTA AP A→AP B.
- Corrección packet-wise de CFO/CPE para sensing.

## 11. Sector sweeping con cuatro elementos

Una ULA λ/2 de cuatro elementos permite steering 1D y sectores gruesos. Como
orden de magnitud, el lóbulo principal broadside ronda unas pocas decenas de
grados y se ensancha hacia endfire. No debe presentarse como resolución angular
fina.

Riesgos:

- Ambigüedad frente/espalda de una ULA con monopolos/dipolos.
- Acoplamiento mutuo y tolerancias de separación.
- Multipath interior que desplaza máximos.
- Patrón individual de la antena y estructura del X410/cables.
- ZF sensible a matrices H mal condicionadas.

El sector ID es una condición de iluminación/recepción conocida, no una medida
perfecta de ángulo. Para 2D o sectores más estrechos se necesitará otra
geometría o más elementos.

## 12. Modelo de sensing distribuido sin fase RF común

Un burst de A recibido en B constituye sensing bistático/distribuido. El canal
por paquete contiene fase desconocida de los LOs además de propagación. Features
posibles:

- Potencia y amplitud por subportadora/antena/sector.
- Diferencias de fase entre antenas del mismo X410.
- Diferencias de fase entre subportadoras tras eliminar pendiente temporal.
- Common phase removal por paquete.
- CFO estimado y corregido.
- Referencia al camino directo o a componentes estáticas.
- PCA/subspace y Doppler sobre secuencias corregidas.

No son válidos sin más:

- Comparar fase absoluta de A y B como si compartieran LO.
- Integrar coherentemente durante largos intervalos sin seguimiento de fase.
- Atribuir toda variación de fase a movimiento.

Si el objetivo científico evolucionara a radar multistático coherente de fase,
haría falta compartir LO/referencia RF o diseñar una calibración OTA continua,
lo que sería un requisito distinto y potencialmente otra contribución.

## 13. Cadencia y Doppler

La dimensión slow-time se diseña por sector. Ejemplos:

- Repetición por sector cada 100 ms: 10 Hz, Doppler no ambiguo ideal ±5 Hz.
- Repetición cada 20 ms: 50 Hz, aproximadamente ±25 Hz.
- Paquetes cada 200 µs dentro de 2 ms: alta tasa intraburst, pero solo diez
  muestras y resolución frecuencial limitada por la duración del burst.

Para una velocidad radial `v`, la frecuencia Doppler monostática aproximada es
`2v/lambda`; en bistático depende de la suma de vectores TX-objetivo y
objetivo-RX. Por tanto, el periodo de revisita debe derivarse del rango de
velocidades y geometría, no solo del número de slots SSB.

Decisiones pendientes:

- Número de sectores.
- Orden de sweep.
- Número de paquetes por sector y burst.
- Repetición de cada sector por superciclo.
- Ventana Doppler, resolución y aliasing aceptable.
- Si se usa Doppler intraburst, interburst o ambos.

## 14. Observabilidad y contrato de datos futuro

Cada AP deberá publicar, como mínimo:

### 5G

- Identidad de celda y referencia SSB.
- SFN/índice o epoch decodificado.
- PSS timestamp exacto, dispositivo de origen (`x410` en fase cero o `b210` en
  la arquitectura final) y tasa de ticks.
- Periodo, fase, jitter, CFO y confianza.
- Eventos de lock, revalidación, discontinuidad y holdover.

### Mapeo local

- Clock/time source del receptor 5G y del ejecutor WiFi; en fase cero son el
  mismo X410 y en la arquitectura final son B210 y X410.
- Anchors simultáneos o relación de epochs.
- Coeficientes y residual de transformación.
- Retardos calibrados e incertidumbre.

### WiFi

- Timestamp X410 programado y observado.
- ACK/late/underflow/overflow por acción.
- Rol, sector, pesos, calibración y waveform.
- Timestamps PPDU y CSI MIMO.
- Ventana RX real y recorte científico.

### Sesión

- Agenda provisionada y digest.
- Versiones de código, UHD/FPGA y hardware lógico.
- Estado de cada ciclo y motivo de cualquier slot omitido.
- Manifiestos y checksums mediante el framework padre.

## 15. Plan de validación técnica, no autorización de ejecución

La secuencia lógica de reducción de riesgo es:

1. Preservar y sanear `testmultiband`; reutilizar únicamente componentes
   entendidos de timed streaming, sounding y calibración.
2. En un X410, reservar dos cadenas WiFi TDD y una 5G; validar captura
   multibanda, rates, timestamps e interferencia interna.
3. Integrar detección PSS/epoch y programar un pulso o paquete WiFi para el
   siguiente ciclo en el mismo timekeeper; medirlo con un observador.
4. Repetir con dos X410 independientes observando la misma celda.
5. Ejecutar agenda complementaria A-TX/B-RX y B-TX/A-RX en WiFi 2×2.
6. Caracterizar error PSS→PPDU, jitter, late commands, pérdidas y repetibilidad.
7. Solo después, introducir B210 en cada AP, compartir referencia local y medir
   el mapeo B210↔X410.
8. Confirmar que el algoritmo conserva el rendimiento temporal al sustituir el
   RX 5G interno por B210.
9. Medir repetibilidad de fase del X410 sin retune, con retune y tras reinicio.
10. Validar HE NDP-like por X410 y extenderlo de 2×2 a 4×4 con pesos conocidos.
11. Validar cuatro RX, schema CSI MIMO, sectores y análisis Doppler.
12. Integrar perfiles, schemas, publicación e inferencia externa en PC5.

Cada escalón necesita aceptación propia. El hecho de que el escalón 1 funcione
no valida los escalones 6-12.

## 16. Criterios de éxito futuros

Antes de afirmar coordinación 5G:

- Ambos AP identifican la misma referencia y ciclo.
- Los comandos se arman con el lookahead configurado.
- No hay late commands ni solape de roles.
- El error PSS→PPDU observado queda dentro de una tolerancia medida.
- La operación sigue correcta con clocks físicos independientes entre AP.

La fase X410-only se considera superada aunque el sensing 2×2 sea peor si los
criterios temporales anteriores se cumplen. No se considera evidencia del
mapeo B210↔X410 ni del rendimiento 4×4; esos son hitos posteriores separados.

Antes de afirmar BF 4×4:

- Calibración repetible y trazable.
- Pesos aplicados a cuatro canales con timestamp común.
- Patrón/ganancia medidos coherentes con un baseline.
- Metadata identifica canales, pesos y sector.

Antes de afirmar sensing Doppler sectorial:

- CSI MIMO íntegro y timestamps exactos.
- CFO/CPE y fase de hardware caracterizados.
- Cadencia slow-time suficiente para las velocidades objetivo.
- Movimiento controlado separable de deriva y clutter.
- Comparación contra omnidireccional/no-sync y métricas científicas acordadas.
