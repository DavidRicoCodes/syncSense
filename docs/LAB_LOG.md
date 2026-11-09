# Estado del banco BF multi-AP X410 (paper tracker)

Snapshot: **2026-10-14, 14:40**, con los resultados de las ejecuciones hasta
el 2026-10-14 (t9, t10 y t11; localización a 100 MHz confirmada con una
repetición analizada con parámetros fijados de antemano). Resumen rápido en
§0; plan de comparación con el estado del arte en §10.2. Este documento registra lo que existe, cómo se usa, lo medido y lo
que falta en la validación X410-only de dos AP coordinados por 5G descrita en
[`BF_MULTI_AP_SYNC_ARCHITECTURE.md`](BF_MULTI_AP_SYNC_ARCHITECTURE.md).
Cuando un dato no está aquí o en los ficheros de resultados, no se ha medido.

Reglas de lectura:

- "Medido" significa calculado a partir de `sense_rx.npz`/`summary.json` de
  una ejecución concreta. "Derivado" es un cálculo a partir de parámetros
  (tasa, ventana, geometría). "Modelo" es una simulación geométrica.
- Las identidades hardware (IPs, seriales, hostnames reales) no se versionan.
  Se usan los alias SSH `pc3pc4` (AP A) y `x410srv` (AP B).
- El código vive en `~/syncSense/testmultiband/` de ambos servidores, **fuera
  del padre y sin versionar** (ver §9, riesgo 1).

## 0. Lo conseguido a 2026-10-14 (resumen para el artículo)

Dashboard de detalle de las pruebas de baldosas (t9, t10, t13, t16 y comparativa;
tres modos por prueba con el mejor marcado):
https://claude.ai/artifact/3g41WQ92z4xSnxDRRcmMRJ. Datos y scripts en
`analysis/dashboard_detalle/` de la copia de seguridad.

Dashboard de resultados (t9, t10 y t11):
https://claude.ai/artifact/5n7nVJ5eVUkJweup81eNsM. Dashboard anterior (tanda 3
y 10-13): https://claude.ai/artifact/XbE7A2y1b49GnB3XB8HL7x.

| Resultado | Valor | Dónde |
|---|---|---|
| Coordinación de dos AP sin backhaul solo con la celda 5G (SFN del PBCH + PSS) | 0 discrepancias de numeración en todas las pruebas | §5.1, §5.2 |
| Sincronización en sala vacía a 100 MHz (t11) | desviación de 8,9 ns, ruido rápido de 1,2 ns, deriva lenta de 6,9 ns | §6.7 |
| Localización de una persona, análisis fijado de antemano (t10, A→B) | mediana de 0,15 m, máx. de 0,25 m, 16 de 16 a ≤ 0,3 m | §6.6 |
| Localización combinando los dos sentidos (t10) | mediana de 0,09 m, máx. de 0,16 m | §6.6 |
| Localización con ventanas de 0,5 s | mediana de 0,11–0,16 m, 83–92 % a ≤ 0,3 m | §6.8 |
| Distancia biestática desde el perfil de retardos | correlación de 0,95, error mediano de 0,14–0,20 m (t9 y t10) | §6.4, §6.6 |
| Rechazo trasero con chapas pequeñas | delante +20–24 dB sobre el vacío; detrás, en el suelo de ruido (≥ 15 dB) | §6.4, §6.6 |
| Separación entre antenas medida | 10 cm entre las antenas extremas, es decir, 3,33 cm (deducido de los datos: 3,36 cm) | §6.7 |
| Robustez del sensing a la sincronización | localización sin degradación con desfases A–B de hasta ~230 ns (persona en zona y 5G de B degradado en t10) | §6.6 |
| Turnos perdidos | 0 % tras el arranque con `--nr-workers 4` (t11) | §6.7 |
| CFO entre AP predicho por el 5G | medido 381 Hz frente a 365 Hz predichos por el ppm del tracker; ±7 Hz con 10 s de promedio | §10.3 |

Lo que falta para el artículo: la comparación con los esquemas sin
sincronización 5G (§10.2), la prueba de sincronización en frecuencia (§10.3),
una persona caminando, dos personas y pruebas largas.

## 1. Resumen en una tabla

| Pregunta | Respuesta corta |
|---|---|
| ¿Funciona la coordinación temporal por 5G entre dos AP independientes? | **Sí**: 8 ns rms de residuo, 0 colisiones, CFO entre AP predicho con 6 Hz de error |
| ¿Es ya "sin backhaul"? | **Sí para la coordinación** desde el 2026-10-13: la numeración de SSB sale del SFN decodificado del PBCH/MIB. La hora del host solo fija cuándo empieza y acaba una prueba |
| ¿Qué sensing hay? | Presencia en movimiento, velocidad Doppler, **posición** y **distancia biestática** (§0) |
| ¿Posición? | **Sí** (2026-10-14): mediana de 0,10–0,15 m por baldosa, confirmada con una repetición analizada con parámetros fijados de antemano; 0,11–0,16 m con 0,5 s de datos |
| ¿Distancia? | **Sí** a 100 MHz: exceso de camino biestático con correlación de 0,95 (1 muestra = 2,44 m; precisión, no resolución entre dos objetivos) |
| ¿Comparación sin sincronizar? | **En curso** (§10.2): la emulación muestra igual precisión si hay datos; la diferencia está en disponibilidad, coste y escalado. B0/B1/B2 validados en simulación; falta la campaña real |

## 2. Hardware y cableado vigentes

| Elemento | Valor |
|---|---|
| AP A | servidor `pc3pc4` + X410 propio |
| AP B | servidor `x410srv` + X410 propio |
| Referencia entre AP | **ninguna**: relojes internos libres, sin 10 MHz/PPS compartido (requisito de la arquitectura) |
| Enlace host–X410 | un 10GbE por AP, imagen FPGA `X4_200`, master clock 245,76 MHz, UHD 4.6.0 |
| Canales WiFi | 4 TX y 3 RX por AP, todos por `TX/RX0` en TDD; RX en ch0, ch1, ch3 |
| Canal 5G | ch2 `RX1`, 3541,44 MHz, 15,36 Msps (celda comercial, NCellID 0, SSB cada 20 ms) |
| Orden físico del array | ch0, ch1, ch3, ch2 (el array RX ch0-ch1-ch3 queda contiguo) |
| Antenas | 5 por AP (4 WiFi + 1 5G), magnéticas genéricas "600–6000 MHz"; a 5 GHz pierden unos 25 dB |
| Separación del array | **3,33 cm medidos** (10 cm entre las antenas extremas; 0,58 λ a 5,18 GHz), bases tocándose. Hasta el 10-13 se suponían 3,0 cm |
| Montaje vigente (10-14) | arrays a 0°, paralelos a su borde y centrados: A en (1,50; 0) con antenas 3, 4, 2, 1 de izquierda a derecha; B en (3,00; 1,50) con antenas 1, 2, 4, 3 de arriba abajo. Zona de 3 × 3 m con baldosas de 60 cm. Chapas pequeñas (switches metálicos) detrás de cada array; antenas 5G a más de 1 m (la de B encima del rack, reajustada a las 13:55) |
| Geometría | "L" sobre baldosas de 60 cm: A ≈ (2,4; 4,8) m, B ≈ (0,48; 3,48) m, centro de zona Z ≈ (2,4; 3,0) m, zona de paso 1,8 × 1,8 m (baldosas 3–5 × 4–6). Estimada en foto, ±30 cm |
| Ángulos usados | A ve a B a +32° y la zona a −22°; B ve a A a +30° y la zona a −18° |
| Host | `sysctl` `net.core.rmem_max=50000000`, `net.core.wmem_max=25000000` en `/etc/sysctl.d/90-uhd-x410.conf` (ambos servidores) |
| Red host–X410 (2026-10-13) | **2 enlaces 10GbE por AP**: lane 0 `192.168.10.2` y lane 1 `192.168.11.2` del cable QSFP28→4×SFP+ (lanes 2 y 3 sin conectar; la placa base no tiene ranuras para otra tarjeta). MTU 9000, buffers 4096 y gobernador `performance`, persistentes (NetworkManager + `cpu-performance.service`). UHD: `--args addr=192.168.10.2,second_addr=192.168.11.2` |
| Benchmark UHD (`benchmark_rate`, 20 s continuo) | 1 enlace: 1 canal a 245,76 Msps sin pérdidas solo con MTU 9000 (con MTU 1500 perdía el 39 %). 2 enlaces: **3 RX + 4 TX a 122,88 Msps sin pérdidas** (x410srv 0 underruns; pc3pc4 73 en TX continuo); 2 RX a 245,76 sin pérdidas, pero 2 TX a 245,76 con 73–433 underruns → **≈ 100 MHz viable con 4×3; 200 MHz necesita 4 enlaces** |
| Entorno Python | `~/syncSense/modulos_rx_tx/.venv_uhd` con `--system-site-packages`, numpy 1.26.4 |

Comando base en cualquiera de los dos servidores:

```bash
cd ~/syncSense/testmultiband && PYTHONPATH=/usr/local/lib/python3.10/site-packages ~/syncSense/modulos_rx_tx/.venv_uhd/bin/python -u <script> ...
```

## 3. Scripts de `testmultiband` y para qué sirven

| Script | Función | Estado |
|---|---|---|
| `x410_2x2_5g.py` | smoke 2×2 tono + 5G en un X410; aporta `load_nr_dsp`, que importa sin cambios el DSP 5G de `modulos_rx_tx` | validado |
| `x410_pss_timed_tx.py` | núcleo común: `Burst` (ZC con raíz distinta por pulso/cadena), `Tracker` PSS (ajuste lineal de 50 PSS), `UhdRadio`, `SimRadio`. Fases A (solo 5G), B (burst con offset), C (burst sobre el SSB). Desde la tanda 2 separa tasa de ticks, 5G y WiFi y empareja ventanas RX por `time_spec` | validado; copia previa `*_t1.py` |
| `plot_pss_timed_tx.py` | dashboard del banco de un X410 | validado |
| `beam_from_h.py` | apuntamiento desde la H medida en un X410 | validado |
| `x410_two_ap.py` | coordinación de dos AP a 10 Hz: A en el slot 1 y B en el 3 de cada ciclo de 5 SSB, centinelas que detectan numeración distinta | validado |
| `plot_two_ap.py` | análisis conjunto A/B con ida y vuelta | validado |
| `beam_two_ap.py` | fase 0 de BF desde registros de dos AP (patrones, sectores, pérdida MRT, deriva) | validado (2,45 GHz) |
| `x410_two_ap_sense.py` | **sensing biestático** a 200 Hz por sentido; versión vigente de la tanda 2 (§4) | validado; copia tanda 1 `*_t1.py` |
| `sense_doppler.py` | Doppler por sector: CFO por burst, interpolación de bursts con interferencia, referencia de fase común, supresión de lo estático, sectores virtuales y espectrogramas | validado; separación por defecto 3,0 cm |
| `x410_spectrum_scan.py` | barrido solo RX de 2,4/5/6 GHz con ocupación por canal de 20 MHz | validado |
| `nr_pbch.py`, `nr_pbch_tables.py` | decodificador PBCH/BCH en numpy: ecualización, desaleatorización, polar SC-list (L = 8) + CRC24C, MIB/SFN. Tablas polares derivadas de funciones públicas del 5G Toolbox (`pbch_ref/pbch_ref.m`) | validado (§5.1) |
| `x410_pbch_check.py` | comprobador **solo RX** del PBCH en un X410 (no configura TX) | validado |
| `compare_pbch.py` | compara la numeración de dos AP (salidas del comprobador o `records.jsonl`) | validado |
| `build_dash.py` | genera los datos del dashboard a partir de las ejecuciones | copiado a ambos servidores |

Todos admiten `--sim` para validar sin hardware.

## 4. Cómo se lanza una prueba de sensing

**Comando vigente (2026-10-14, 100 MHz)**, el mismo en los dos AP cambiando
`--ap`, `--out-dir` y siempre con una hora real:

```bash
cd ~/syncSense/testmultiband && PYTHONPATH=/usr/local/lib/python3.10/site-packages ~/syncSense/modulos_rx_tx/.venv_uhd/bin/python -u x410_two_ap_sense.py --ap A --args addr=192.168.10.2,second_addr=192.168.11.2 --wifi-rate 122.88e6 --pulse-spacing-us 50 --lookahead-ssb 7 --min-margin-ms 8 --nr-workers 4 --tx-start HH:MM:SS --duration-s 180 --tx-gain 40 --rx-gain 40 --latency-ns 1300 --out-dir <nombre>_A 2>&1 | tee <nombre>_A.log
```

- Opcional: `--freq-sync-5g` (§10.3).
- Diagnóstico en `summary.json`: `stalls` (parones del bucle principal),
  `gc` (pausas del GC), `freq_sync`.
- Antes de lanzar, comprobar MTU 9000 en los dos enlaces, el governor en
  `performance` y `net.core.wmem_max` = 25000000. En pc3pc4, tras reiniciar,
  vuelve a 1 MB por causa desconocida: `sudo sysctl -w
  net.core.wmem_max=25000000`.

Lo que sigue es la versión histórica de la tanda 3 (61,44 Msps):

Mismo comando en los dos AP, con la **misma hora de inicio real** (nunca un
marcador como `HH:MM:00`) y en una sola línea:

```bash
... x410_two_ap_sense.py --ap A --tx-start 12:55:00 --duration-s 120 --tx-gain 40 --rx-gain 40 --latency-ns 1720 --min-grid-margin-ms 0 --out-dir t3_empty_A
... x410_two_ap_sense.py --ap B --tx-start 12:55:00 --duration-s 120 --tx-gain 40 --rx-gain 40 --latency-ns 1720 --min-grid-margin-ms 0 --out-dir t3_empty_B
```

Parámetros por defecto de la versión vigente:

| Parámetro | Valor | Nota |
|---|---|---|
| WiFi | 5,18 GHz, 61,44 Msps | 122,88 Msps no cabe en un 10GbE con 4 TX/3 RX |
| Ticks | 245,76 MHz (4,07 ns) | todos los comandos temporizados y timestamps |
| Burst | 8 pulsos ZC de 3067 muestras (49,9 µs) cada 100 µs = 0,75 ms | raíces A 25/34/43/52, B 113/122/131/140, +37 por pulso |
| Agenda | A en PSS + 1,25 ms + j·5 ms, B en PSS + 3,75 ms + j·5 ms | 200 Hz por sentido, entrelazados cada 2,5 ms |
| Antelación | 6 SSB (unos 120 ms); margen mínimo 15 ms | tarde → turno perdido y AP en silencio |
| Ventana RX | burst ± 50 µs de guarda | — |
| Retardo TX→aire→RX | se mide solo en los primeros 40 bursts (`[LATENCY]`) | 1,66–1,77 µs a 61,44 Msps; era 4,0 µs a 15,36 Msps |
| Extracción | filtro adaptado en posición conocida ± 500 ns + refinado fraccional a 1/40 de muestra, en 4 procesos | `--integer-extract` reproduce la tanda 1 |
| Sesiones | `--session-period-cycles 100 --session-len 100` (continuo) | bajo demanda: `--session-start 70 --session-len 30` (sin probar en hardware) |

Numeración: por defecto `--numbering sfn` (también en `x410_two_ap.py`). Se
decodifica el PBCH en cada SSB hasta tener `--pbch-min 3` decodificaciones
coherentes, y después cada `--pbch-every 25` SSB (0,5 s) para revalidar. El número
común es (2·SFN + media trama)/4 mod 512. `--numbering ntp` conserva el método
antiguo (rejilla de 20 ms sobre la hora del host) para comparar.

Qué vigilar en la salida: `[NUMBERING] SFN from PBCH/MIB: ...` antes de
`[LOCKED]`; `[LATENCY] ... found X of Y` al principio;
`peak/floor` en cientos; `late 0`; `5G ovf 0 disc 0`; `queue` pequeña. Las
líneas `LLLL` son paquetes TX que llegaron tarde al X410 (§9).

Análisis:

```bash
python3 sense_doppler.py --a t3_walk_A --b t3_walk_B --marks 30 90 --out walk.png
```

Con `--numbering sfn` ya no hacen falta `--grid-offset-ms` ni
`--min-grid-margin-ms` (solo afectan al modo `ntp`).

## 5. Resultados de sincronización

Convención: «residuo» es la desviación del instante de llegada de cada burst
respecto al previsto con el 5G, medida en el AP receptor; «desfase entre AP» es
(medido en A − medido en B)/2, que separa la diferencia de los instantes 5G que
ve cada AP del retardo físico del WiFi.

| Ejecución (fecha) | WiFi | Método | σ en A | σ en B | Desfase entre AP |
|---|---|---|---|---|---|
| Coordinación enfrentados `two_ap_run1` (10-08) | 2,45 GHz, 15,36 Msps, 10 Hz | correlación completa + sinc | 27,6 ns | 28,8 ns | +2,3 ns |
| Coordinación en L `two_ap_L_run1` (10-08) | ídem | ídem | 33,7 ns | 33,3 ns | −85,2 ns |
| Tanda 0 vacía `sense_L_empty` (10-08) | 2,45 GHz, 100 Hz | posición conocida, muestra entera | 40,8 ns | 40,4 ns | −6,7 ns |
| Tanda 1 cables `sense_L_cables` (10-09) | ídem | ídem | 33,8 ns | 34,1 ns | −73,4 ns |
| Tanda 3 comprobación `t2_check3` (10-09) | 5,18 GHz, 61,44 Msps, 200 Hz | posición conocida, fraccional | **8,3 ns** | **8,4 ns** | −37,6 ns |
| Tanda 3 vacía `t3_empty` (10-09) | ídem | ídem | **7,7 ns** | **7,6 ns** | −22,4 ns |
| Tanda 3 andando `t3_walk` (10-09) | ídem | ídem | 17,7 ns | 17,8 ns | −34,6 ns |

Otros resultados de sincronización:

| Medida | Valor | Origen |
|---|---|---|
| Residuo del tracker PSS | 17–19 ns rms; ppm frente al gNB: A ≈ −0,15, B ≈ −0,22 | todas las ejecuciones |
| Un X410, 4×3 + 5G (`pss_4x3_run1`, 10-06) | 0 tardíos, 5G al 100 % incluso con burst sobre el SSB, sin skew medible entre daughterboards | banco de un AP |
| Colisiones entre AP | 0 en todas las ejecuciones; compañero oído en el 99,1–99,8 % de sus slots a 10 Hz y en el 98,9–99,7 % de los bursts a 200 Hz | — |
| CFO entre AP medido en los pulsos frente a predicho por el 5G ((ppm_tx − ppm_rx)·f) | +0,6 a +3,3 Hz sobre unos 167 Hz (2,45 GHz); +1,6 a +9,5 Hz sobre unos 355 Hz (5,18 GHz) | — |
| Efecto de una persona | el camino WiFi (A + B)/2 no cambia; el desfase entre AP se mueve unos 25 ns mientras la persona anda o está cerca de un AP (multitrayecto 5G) | `t3_walk` |

Correcciones registradas:

- Las primeras cifras de sincronización del banco de un X410 ("3973 ± 0,5 ns")
  estaban infravaloradas por interpolación parabólica sesgada; se sustituyó por
  interpolación sinc.
- Para las tandas 0/1 se llegó a citar 16–27 ns con otro estimador; con la
  métrica uniforme de la tabla son 34–43 ns.

Artefacto de 1,05 Hz (tandas 0/1): oscilación de amplitud del 25 % de media y
hasta el 62 % en algún enlace. **No eran los cables.** La causaba el redondeo
de los instantes a muestra entera de 15,36 Msps: con relojes que difieren unas
0,07 ppm, la fracción de muestra da una vuelta cada 1 / (15,36 MHz × 0,07 ppm)
≈ 1 s. Se eliminó programando a 4 ns y con refinado fraccional. Pico en
0,95–1,15 Hz: +23 a +28 dB en las tandas 0/1 y −1 dB en la tanda 3.

### 5.1 Numeración por SFN (paso 1, 2026-10-13)

| Prueba | Resultado |
|---|---|
| Codificador Python frente a `nrBCH`/`nrPBCH` de MATLAB | 300/300 bit a bit |
| Decodificación con ruido (AWGN) | 100 % hasta −7 dB por subportadora, 87 % a −9 dB, 0 decodificaciones falsas con CRC válido |
| Cadena completa con forma de onda SSB de MATLAB a través del pipeline 5G de `modulos_rx_tx` | 11/11 SFN correctos a 10 dB, con 300 Hz de CFO y cruzando 1022 → 0 |
| Solo RX, celda real, 60 s (`pbch_check_A/B`) | **A 2762/2762 y B 2341/2341 SSB decodificados, 0 fallos de CRC**, SFN siempre coherente con el tracker, MIB constante; EVM −19,5 dB (A) y −31,9 dB (B); 8–27 ms por decodificación |
| MIB de la celda | SCS común 30 kHz, ssb-SubcarrierOffset 6, DMRS tipo A en el símbolo 2, CORESET#0 12, SearchSpace#0 2, notBarred, intraFreqReselection 0; SSB solo en media trama 0 y SFN par |
| Coordinación con SFN, 5,18 GHz, 10 Hz, 3 min (`two_ap_sfn_A/B`) | **0 discrepancias de numeración en 496/478 revalidaciones**, 0 aciertos de centinela, compañero oído en 1785/1793 (A) y 1805/1811 (B) slots; residuo 25–30 ns a 15,36 Msps; desfase entre AP de −67 ns |
| Misma numeración en A y B | la época del SSB común 0, medida con el ancla dispositivo↔host, difiere **8,3 ms** entre A y B (< 20 ms de un SSB): es el desfase de las horas NTP de los dos servidores, no de la numeración |

Lectura: los dos AP numeran igual los SSB sin usar la hora del host. Además, las
horas NTP de los dos servidores difieren unos 8 ms, que es casi media rejilla de
20 ms: por eso fallaba la numeración provisional por NTP. B no pasó el veredicto
por 8 turnos TX y 6 RX tardíos (no relacionados con la numeración).


### 5.2 Método de coordinación OTA y numeración por SFN (para el artículo)

Esta sección describe el método tal como está implementado (`nr_pbch.py`,
`x410_pss_timed_tx.py`, `x410_two_ap.py`, `x410_two_ap_sense.py`), con el nivel
de detalle necesario para la sección de métodos.

#### 5.2.1 Problema

Cada AP tiene un X410 con su propio oscilador interno y su propia época de
tiempo, sin referencia común con el otro AP y sin backhaul. Para ejecutar una
agenda complementaria (A transmite y B escucha en un turno; al revés en otro),
los dos AP deben acordar dos cosas solo con la celda 5G:

1. **El instante fino** de cada turno, a escala de nanosegundos: lo da el PSS.
2. **La identidad del turno**: qué SSB es «el turno 1 del ciclo». Lo da el SFN
   del PBCH/MIB.

Contar SSB desde el arranque local no vale: dos AP que arrancan en momentos
distintos asignan números distintos al mismo SSB físico. Redondear la hora del
host a la rejilla de 20 ms (método provisional anterior) depende de NTP. Las
horas NTP de los dos servidores difieren unos 8 ms (§5.1), casi media rejilla,
lo que provocaba abortos por margen insuficiente.

#### 5.2.2 Parámetros de la celda y de la captura

| Parámetro | Valor |
|---|---|
| Banda / frecuencia SSB | n78 TDD, 3541,44 MHz |
| SCS del SSB | 30 kHz (caso C); Lmax = 8 |
| Celda | NCellID 0; SSB de índice 0 (`--force-ibar-ssb 0`); periodicidad medida de 20 ms |
| Captura | canal ch2 `RX1` del X410, 15,36 Msps (FFT de 512), bloques de 20 ms con 1 ms de solape, flujo continuo |
| Ticks | 245,76 MHz (master clock; 16 ticks por muestra 5G) |

#### 5.2.3 Instante fino: PSS y tracker

1. Por bloque, el pipeline 5G sin modificar de `modulos_rx_tx` detecta PSS/SSS,
   corrige el CFO estimado en el warm-up y extrae la rejilla del SSB (240
   subportadoras × 4 símbolos OFDM).
2. Timestamp del PSS en ticks del dispositivo:
   `T = tick(time_spec del bloque) + 16 · desplazamiento del detector (muestras)`.
   Nunca se usa el instante en que el resultado llega a Python.
3. Tracker: ajuste lineal `T_k = a + b·k` sobre los últimos 50 PSS aceptados
   (k = índice local del SSB). Da el periodo propio frente al gNB (ppm) y
   predice T para SSB futuros. Bloqueo con 20 puntos, residuo < 300 ns y
   holdover de 200 ms; los PSS a más de 1 µs de la predicción se rechazan.
   Residuo típico de 17–19 ns rms.

#### 5.2.4 Identidad: decodificación PBCH/BCH

Sobre la misma rejilla del SSB de cada bloque (TS 38.211 §7.3.3 y TS 38.212 §7.1):

1. **Estimación de canal** (`modulos_rx_tx`, sin cambios): LS sobre PSS, SSS y
   los 144 DMRS del PBCH, interpolación en frecuencia por símbolo y suavizado.
   Se obtiene H en todas las RE y la potencia residual como estimación de ruido.
2. **RE de datos del PBCH**: 432 RE (símbolos 1 y 3 completos y bordes del 2,
   sin las posiciones DMRS `k mod 4 = NCellID mod 4`), en orden frecuencia y
   luego símbolo. Verificado contra `nrPBCHIndices`.
3. **Ecualización y LLR**: `s = y·H*/|H|²`, `σ² = P_residual/|H|²`, y para QPSK
   `LLR = 2√2·Re(s)/σ²` y `2√2·Im(s)/σ²`. Son 864 LLR.
4. **Desaleatorización del PBCH**: secuencia Gold con `c_init = NCellID` y
   desplazamiento `v·864`, con v = 3 LSB del índice de SSB (Lmax = 8).
5. **Recuperación de tasa polar** (E = 864 → N = 512): se suman los LLR de las
   repeticiones de cada bit del código (352 bits repetidos dos veces, 160 una).
6. **Decodificador polar SC-list** con L = 8 y CRC24C: K = 56 bits de
   información (32 de carga útil + 24 de CRC), nMax = 9, entrelazado de entrada
   activado. Se toma el primer camino de la lista que pasa el CRC.
7. **Carga útil** (TS 38.212 §7.1.1–7.1.2):
   - se lee primero v de los dos bits no aleatorizados (2.º y 3.º LSB del SFN);
   - se deshace la aleatorización (`c_init = NCellID`, M = A − 3 = 29);
   - se deshace el entrelazado G(j);
   - se extraen el MIB (24 bits), los 4 LSB del SFN, el bit de media trama y el
     MSB de k_SSB.
8. **SFN** = 6 MSB del MIB · 16 + 4 LSB de la carga útil.

**Cómo se obtuvieron las tablas.** Las tablas fijas del código polar del PBCH
(posiciones de información con el entrelazado de entrada, y el mapa de
adaptación de tasa con entrelazado por sub-bloques) se derivaron numéricamente
de las funciones públicas `nrPolarEncode` y `nrRateMatchPolar` del 5G Toolbox de
MATLAB R2026b, aprovechando que son lineales (`pbch_ref/pbch_ref.m`). La tabla
de entrelazado G(j) y las reglas de aleatorización siguen el estándar. El
codificador Python completo reproduce bit a bit `nrBCH` y `nrPBCH` en 300 casos
aleatorios de MIB, SFN, media trama, NCellID e índice de SSB.

**Coste.** 6 ms por SSB en un PC (numpy) y 8–27 ms en los servidores con la
carga de la recepción.

#### 5.2.5 Del SFN al número de SSB común

- Número común: `n = ((2·SFN + media_trama) / 4) mod 512`, es decir, el índice
  de semitrama dividido por 4. Con periodicidad de 20 ms el SSB cae siempre en
  la misma fase (aquí, media trama 0 y SFN par), y hay 512 SSB por periodo de
  SFN (1024 tramas × 10 ms / 20 ms = 10,24 s).
- Relación con el índice local: `K0 = (n − k) mod 512`, constante mientras el
  tracker no pierda SSB.
- **Adquisición**: tras el bloqueo del tracker, se decodifica el PBCH en cada
  SSB y se fija K0 cuando las últimas `--pbch-min 3` decodificaciones dan el
  mismo K0. En las pruebas reales hicieron falta 20–21 decodificaciones, porque
  el tracker necesita 20 PSS para bloquear.
- **Revalidación**: después se decodifica el PBCH cada `--pbch-every 25` SSB
  (0,5 s) y se compara con `(k + K0) mod 512`. Cada discrepancia se cuenta y se
  avisa; hoy no hay readquisición automática (§5.2.8).
- **Agenda**: `slot = (n mod 512) mod 5` y `ciclo = (n mod 512) div 5`. A
  transmite en el slot 1 y B en el 3 (coordinación a 10 Hz), o en sensing A a
  PSS + 1,25 ms + j·5 ms y B a PSS + 3,75 ms + j·5 ms. Como 512 no es múltiplo de
  5, el ciclo 102 solo tiene dos SSB (510 y 511) antes de volver a 0; los dos AP
  ven el mismo salto, así que no hay colisión.
- **Programación**: cada AP calcula `T_pred(k)` con su tracker, suma el offset
  del turno en sus propios ticks y encola el comando temporizado de UHD unos
  6 SSB (≈ 120 ms) antes. Si el margen es inferior a 15–20 ms, el turno se
  marca perdido y el AP queda en silencio.

#### 5.2.6 Qué usa todavía la hora del host

- El inicio y el fin de una prueba (`--tx-start HH:MM:SS`, `--duration-s`). Es
  solo operacional: si las horas difieren, un AP empieza o acaba unos segundos
  antes y solo cuenta el tramo común.
- La anotación de cada evento en tiempo UTC en los ficheros de resultados.
- Nada de la temporización ni de la asignación de turnos.

#### 5.2.7 Cómo se verificó

| Verificación | Qué demuestra | ¿Necesita NTP? |
|---|---|---|
| Codificador frente a MATLAB (300/300) | bit-exactitud de la cadena PBCH/BCH | no |
| AWGN y forma de onda SSB de MATLAB por el pipeline real | robustez del decodificador y de la integración | no |
| Solo RX con la celda real, 60 s, ambos AP | 100 % de los SSB decodificados, 0 fallos de CRC, SFN coherente con el tracker y MIB constante | no |
| Revalidaciones durante la coordinación | 974 comprobaciones en total, 0 discrepancias | no |
| Centinelas (escucha en los slots que no son del compañero) | ambos AP coinciden en el slot (n mod 5): 0 aciertos en 14 567 ventanas | no |
| Compañero oído en su slot | 99,6 % (A) y 99,7 % (B) | no |
| Época del SSB común 0 en la hora de cada host, vía ancla dispositivo↔host | ambos AP coinciden en el número completo: difieren 8,3 ms, frente a 20 ms de un SSB | **sí** (verificación externa, no parte del sistema) |

Pendiente: repetir la coordinación con NTP desactivado y el reloj de un host
desajustado varios segundos (§10).

#### 5.2.8 Limitaciones conocidas

- Una sola celda y un solo índice de SSB forzado (0). Para una celda con varios
  haces SSB habría que detectar el índice (los DMRS lo permiten) y fijar en ambos
  AP la misma referencia.
- Lmax = 8 fijado por la banda; el código soporta 4 y 64 pero solo se ha probado 8.
- Sin readquisición automática tras una discrepancia: hoy se cuenta y se avisa.
- La decodificación compite por CPU con la recepción; en los servidores tarda
  hasta unos 27 ms con decodificación forzada en cada SSB (solo durante la
  adquisición).
- La deriva relativa entre los X410 (≈ 0,07 ppm) no afecta a la numeración,
  porque cada AP la absorbe con su propio tracker.

#### 5.2.9 Frase resumen para el artículo

Cada AP adquiere de forma independiente el instante (PSS) y la identidad
(SFN del PBCH/MIB) de la misma referencia SSB de una celda 5G comercial. Asigna
a cada SSB el número común n = ⌊(2·SFN + h)/4⌋ mod 512 y programa con
temporización hardware los turnos WiFi de una agenda provisionada de antemano,
sin backhaul ni reloj común. En 3 min de operación a 5,18 GHz, los dos AP
coincidieron en la numeración en el 100 % de 974 revalidaciones, no se observó
ninguna transmisión fuera de turno en 14 567 ventanas de vigilancia, y el
residuo de sincronización entre AP fue de 25–30 ns rms a 15,36 Msps (8 ns rms a
61,44 Msps en sensing).

## 6. Resultados de sensing

| Medida | Valor | Tipo |
|---|---|---|
| SNR por pulso | 25–27 dB (tanda 3, ganancias 40/40); 14–16 dB (tanda 1) | medido |
| Bursts útiles | 98,9–99,7 % (tanda 3) | medido |
| Detección andando, umbral al p99 de la sala vacía | **93 % con 2 s, 85 % con 1 s, 64 % con 0,5 s**, exigiendo ambos sentidos; FA 1,3–1,7 % | medido |
| Detección de pie | 0 % | medido |
| Un solo sentido | A→B 93 % (2 s); B→A 14 % por interferencia en el receptor A durante la sala vacía (222 bursts afectados frente a 32) | medido |
| Contraste por par de sectores | +4 a +14 dB andando frente a vacía; percentil 95 de +11 a +22 dB | medido |
| Reparto de velocidades andando | 80 % de la energía por debajo de 1,7 m/s; 96 % por debajo de 3,5 m/s | medido |
| Revisita / Doppler sin ambigüedad | 200 Hz / ±100 Hz (±5,8 m/s de velocidad de camino) | derivado |
| Resolución Doppler / velocidad | 2 Hz / 0,12 m/s (ventana de 0,5 s) | derivado |
| Velocidad mínima detectable | ≈ 0,14 m/s (umbral estático de 2,5 Hz) | derivado |
| Resolución en camino biestático | ≈ 6 m (49 MHz útiles) | derivado |
| Tanda 0 (2,45 GHz, 100 Hz) | andando 67–89 % con 2 s y 1 % FA; de pie 0–9 % | medido |

Arrays (5,18 GHz, 3,0 cm):

| Medida | TX (4 antenas) | RX (3 antenas) | Tipo |
|---|---|---|---|
| Haz a −3 dB al frente / a 31° | 25,3° / 30,0° | 34,8° / 42,0° | derivado |
| Sectores distinguibles en ±60° (cruce a −2 dB) | 5 | 4 | derivado |
| Sin lóbulos de rejilla hasta | 68° | 68° | derivado |
| Sectores útiles sobre la zona de paso | 2 por AP | 2 por AP | medido en la fase 0 (2,45 GHz) |
| Pérdida con la calibración de la misma sesión (60 s después) | 0,25–0,69 dB | 0,18–0,47 dB | medido |
| Pérdida con la calibración de la sesión anterior (tras resintonizar) | 2,8–3,5 dB | 4,8–6,1 dB | medido |
| Sin calibrar | haces desviados 40–70° | — | medido (2,45 GHz) |

**Hay que calibrar en cada sesión**, como ya preveía la arquitectura.

Barrido de espectro (10-09): 2,4 GHz ocupado (2–7 % del tiempo, picos de
+70 dB), lo que explicaba el 3–5 % de bursts con interferencia de la tanda 1.
5 GHz limpio salvo 5210–5230, 5290–5310, 5530–5570 y 5690–5710 MHz. 6 GHz vacío
salvo actividad esporádica en 6205–6225 MHz.

Dashboard con todos estos datos (privado, compartir desde su menú):
<https://claude.ai/artifact/XbE7A2y1b49GnB3XB8HL7x>. Lo generó
`build_dash.py` desde los `sense_rx.npz`; el script quedó en un directorio
temporal de la sesión y no está versionado.


### 6.1 Prototipo de localización por cruce de ángulos (2026-10-13, no concluyente)

`loc_proto.py` (en `testmultiband` de ambos servidores) sobre `t3_walk`:

1. **Procesado de partida:** CFO por burst, referencia de fase común y calibración de rango 1 del canal estático, con el otro AP en el ángulo supuesto (32° / 30°).
2. **Parte con Doppler:** ventanas de 0,5 s con salto de 0,1 s; se quedan los bins Doppler de 2,5 a 80 Hz.
3. **Supresión de la señal directa:** se anula el subespacio estático dominante.
4. **Ángulos:** barrido 2D del ángulo de salida en el TX y el de llegada en el RX.
5. **Posición:** cruce de los dos rayos desde las posiciones estimadas por baldosas.

| Métrica (andando, 35–95 s) | A→B | B→A | Nivel de azar |
|---|---|---|---|
| Posiciones dentro de la zona de paso | 13 % | 27 % | 36 % (ángulos aleatorios) |
| Dentro de la zona ± 60 cm | 59 % | 55 % | 75 % |
| Distancia entre las estimaciones A→B y B→A en la misma ventana (mediana) | 1,24 m | | 2,03 m (instantes barajados) |
| Dispersión de los ángulos (rango intercuartílico) | 74–78° | 50–51° | — |

**Corrección importante (2026-10-13, mismo día).** Los análisis de haces suponían
que las antenas TX estaban montadas en orden de canal (ch0, ch1, ch2, ch3). El
orden físico acordado y montado es **ch0, ch1, ch3, ch2**: posiciones 0, 1, 3, 2
para los TX y 0, 1, 2 para los RX ch0, ch1, ch3. Corregido en `loc_proto.py` y
`sense_doppler.py` (`--naive-order` reproduce lo anterior). Con la corrección,
sobre `t3_walk`:

| | Orden de canal (antes) | Orden físico (corregido) | Azar |
|---|---|---|---|
| Distancia entre las estimaciones A→B y B→A (mediana) | 1,24 m | **0,64 m** | 2,03 m |
| Ángulo en A: salida en A→B frente a llegada en B→A | 21° | **9°** | — |
| Ángulo en B, igual | 31° | **20°** | — |
| Posiciones en la zona ± 60 cm (A→B / B→A) | 59 / 55 % | 80 / 70 % | 75 % |

**Afectados por el error de orden** (están por recalcular): las energías por par
de sectores, el «haz medido» del dashboard, los resultados por sector de la fase
0 (`beam_two_ap.py`) y `beam_from_h.py`.

**No afectados:** la detección con todos los enlaces, la sincronización, los
haces ideales, los anchos de haz y el número de sectores.

**Lectura:** con estos datos el prototipo **no localizaba**. (tabla de arriba, antes de la corrección) Los ángulos de la parte con Doppler saltan por todo el rango y las posiciones se agrupan cerca de los propios AP. Sin una ruta conocida no se puede separar la causa:

- calibración y suposiciones geométricas: ángulos del otro AP supuestos, posiciones estimadas en foto ±30 cm, signo y orden físico de los elementos, arrays movidos al pasar a 3 cm;
- multitrayecto del eco;
- ambigüedad delante/detrás;
- apertura de 4 × 3.

**Siguiente:** prueba controlada con la persona moviéndose en el sitio (brazos o marcando el paso) en baldosas conocidas, más medidas reales con cinta de las posiciones y orientaciones de los arrays.


### 6.2 Prueba de baldosas conocidas `t4_tiles` (2026-10-13)

**Montaje.** Cuadrícula de 3 × 3 m (baldosas de 60 cm). AP A con centro en
(1,50; −0,03) m y AP B en (3,03; 1,50) m: cada uno a 1,53 m del centro, A–B a
2,16 m y L de 90°. Orden físico de las antenas 1, 2, 4, 3 (ch0, ch1, ch3, ch2),
separación de 3,0 cm y **chapas metálicas detrás de los arrays**. 61,44 Msps,
numeración por SFN.

**Protocolo.** 0–20 s vacío; después 20 s en el sitio por baldosa: (3,3), (2,3),
(3,4), (2,4), (4,4), (2,2), (1,4) y (4,2), esta última como control en la
diagonal A–B. Inicio a las 17:15:00. Los intentos anteriores, interrumpidos, se
guardaron como `*_aborted*`.

**Funcionamiento.** 47 776 y 47 778 bursts recibidos (de 48 000). TX tardíos del
0,4 %, menos que antes gracias a la MTU 9000. 0 discrepancias de numeración en
638/640 revalidaciones. Con las chapas, el canal estático pasa a tener un camino
dominante del 92–94 % (64–84 % sin chapas).

**Sincronización peor que en la tanda 3.** El residuo dentro de cada tramo es de
22–49 ns, frente a 8 ns. El desfase entre AP varía de 59 a 148 ns según dónde
está la persona, mientras que el camino WiFi (A + B)/2 se mantiene constante
(−0,7 ± 0,5 ns). La calidad del PSS de A bajó a ~0,75 (antes 0,90–0,97):
probablemente la chapa tapa la antena 5G de A. **Hay que sacar la antena 5G de la
sombra de la chapa.**

**Localización** (`loc_tiles.py`, `ang_tiles.py`; steering con las posiciones
reales de los elementos, calibración de rango 1 con el otro AP, Doppler de 2,5 a
80 Hz, señal directa anulada):

| Variante | Error mediano |
|---|---|
| Geometría leída del dibujo (giro hacia el otro AP) | 0,82 m (azar 1,36 m) |
| Giro invertido, hipótesis «pegada a la esquina» | 0,57 m |
| Orientación de cada array calibrada con **una** baldosa conocida, evaluada en las otras 7 | **0,43–0,61 m** según la baldosa de calibración |
| Orientación estimada dejando fuera la baldosa evaluada (leave-one-out) | **0,33 m** |

- Los ángulos tienen un sesgo constante de ≈ −19° en A y ≈ +20–28° en B; los dos
  sentidos coinciden (reciprocidad).
- Tras corregirlo, el error de ángulo en A es ≤ 3° en 6 de 8 baldosas y en B de
  6–11° en 5 de 8.
- Calibrando en (3,3): (3,4) a 0,09 m, (4,4) a 0,01 m, (1,4) a 0,36 m y (2,4) a 0,57 m.
- Fallan (2,3) y (2,2), donde el ángulo de B sale muy desviado, y (4,2), el
  control junto a la diagonal, como se esperaba.

**Conclusión.** El método localiza a nivel de baldosa cuando se conoce la
orientación de los arrays. Lo que limita ahora es conocer esa orientación con
precisión y algunas posiciones mal vistas desde B. Protocolo propuesto: una
baldosa de calibración al inicio de cada sesión, o medir la orientación con
precisión.


### 6.3 Preparado para 100 MHz (2026-10-13, sin probar aún transmitiendo)

- **Red.** Segundo enlace 10GbE en cada servidor: lane 1 del X410 en 192.168.11.2,
  host 192.168.11.1 (pc3pc4) y 192.168.11.15 (x410srv). MTU 9000, buffers de 4096
  y gobernador `performance`, persistentes vía NetworkManager y el servicio
  `cpu-performance`.
- **Benchmark UHD (tráfico continuo, 20 s).**
  - 4 TX + 3 RX a 122,88 Msps a la vez: x410srv sin pérdidas ni underruns;
    pc3pc4 sin pérdidas y con 73 underruns.
  - 2 RX a 245,76 Msps: sin pérdidas.
  - TX a 245,76 Msps: con underruns. 200 MHz descartado por ahora.
- **`x410_two_ap_sense.py`:**
  - acepta `--args addr=192.168.10.2,second_addr=192.168.11.2 --wifi-rate 122.88e6`;
    el ZC pasa a 6143 muestras (50 µs) y la búsqueda a ±62 muestras;
  - guarda el **perfil de retardos** de cada burst (`profile`: burst × rx × tx × 24
    retardos, de −8 a +15 muestras respecto al pico, pulsos alineados en fase),
    junto con `profile_first_lag` y `profile_delta`;
  - Ctrl+C hace una parada ordenada que siempre guarda resultados, y los procesos
    de extracción lo ignoran.
- **Validación.**
  - Simulación a 122,88 Msps: 1200/1200 bursts y PASS; extracción ≈ 11 ms por
    burst, asumible con 4 procesos.
  - Hardware **sin transmitir** (la hora de inicio no llega a alcanzarse) en los
    dos AP: los 7 canales WiFi a 122,88 Msps y el 5G a 15,36 Msps configurados con
    el LO enganchado; numeración por SFN fijada; 0 overflows.
  - Falta la primera ejecución real transmitiendo a 122,88 Msps.

**Primeras pruebas transmitiendo a 100 MHz (2026-10-13, 18:50 y 18:57; sala vacía, con chapas).**

| | `t5_empty122` (pulsos cada 100 µs, burst de 0,75 ms) | `t5_empty122ps50` (pulsos cada 50 µs, burst de 0,40 ms) |
|---|---|---|
| Recepción | sin pérdidas, pico sobre suelo 530–573 | sin pérdidas, 533–569 |
| Turnos no programados a tiempo (A / B) | 4,1 % / 3,6 % | 1,3 % / 2,5 % (en rachas: al arrancar y caídas puntuales del margen a ~0) |
| Avisos de TX tardío en el X410 (A / B) | 8517 / 12 163 | **406 / 445** |
| Underflows (A / B) | 792 / 832 | **0 / 0** |
| Numeración por SFN | 0 discrepancias | 0 discrepancias |
| Latencia TX→aire→RX | 1181 / 1502 ns | 1261 / 1431 ns |
| PSS de A | 0,968 (vuelve a ser buena) | — |

- **A 100 MHz conviene usar pulsos seguidos:** `--pulse-spacing-us 50`. En
  simulación se mantiene la precisión de CFO y de sincronización.
- **Para los atascos puntuales,** probar `--lookahead-ssb 7` y
  `--min-margin-ms 8`; con 7 hay 28 ventanas RX en cola, por debajo del límite de
  32 del X410.

**Deriva lenta de la sincronización desde que hay chapas.** Descomposición del
residuo:

| Prueba | Total | Ruido rápido (de un burst al siguiente) | Deriva lenta (medias de 1 s) |
|---|---|---|---|
| `t3_empty` (10-09, sin chapas) | 7,7 ns | 1,2 ns | **5,8 ns** |
| `t4_tiles` (10-13, chapas, con persona) | 42,9 ns | 2,4 ns | **37,4 ns** |
| `t5_empty122` y `ps50` (10-13, chapas, vacía) | ≈ 24,5 ns | 1–3,5 ns | **23,7 ns** |
| `t6_nopanel` (10-14 11:03, sin chapas, vacía, 122,88 Msps) | 9,8 ns | 1,3 ns | **7,5 ns** |
| `t7_smallpanel` (10-14 11:15, chapas pequeñas, 5G alejadas, vacía) | 13,0 ns | 1,3 ns | **11,0 ns** |

El ruido rápido no ha cambiado; empeora una deriva lenta, de segundos, de la
referencia 5G de un AP frente al otro, y no por falta de señal (PSS > 0,94).
Hipótesis: las chapas cerca de las antenas 5G añaden multitrayecto al 5G.

**Confirmado (10-14):** al quitar las chapas, la deriva lenta vuelve a 7,5 ns
(rango de las medias de 1 s: 39 ns). La causa eran las chapas.
- `t6_nopanel` también mejora los turnos no programados a tiempo con
  `--lookahead-ssb 7 --min-margin-ms 8`: 0,86 % en A y 0,52 % en B (antes
  1,3–2,5 %).
- Avisos de TX tardío: 555 en A y 950 en B. Numeración SFN: 0 discrepancias.
- El camino medio (lag_A + lag_B)/2 es estable (desviación de 0,2 ns).

**Chapas pequeñas (`t7_smallpanel`, 10-14).** Las chapas son switches metálicos
del tamaño del array, y las antenas 5G se alejaron más de 1 m.
- Sincronización: deriva lenta de 11,0 ns (rango de 66 ns). Es mejor que con las
  chapas grandes (23,7 ns) y algo peor que sin chapas (7,5 ns). Ruido rápido de
  1,3 ns. Todavía no se sabe cuánto varía de un día a otro.
- Turnos no programados a tiempo: 1,3 % en A y 0,4 % en B.
- Efecto de las chapas en sala vacía, con t6 → t7 (y las chapas grandes de t5
  como referencia):
  - pico sobre suelo de 419/385 a 633/590, unos +1,8 dB;
  - energía de los ecos tardíos (más de 3 muestras tras el pico): de −1,5/−0,4 dB
    a −4,0/−3,4 dB (las grandes: −5,4/−3,8 dB);
  - fracción de rango 1 del canal estático en B: del 82 % al 96 % (las grandes:
    97 %); en A no cambia (89 %).
- En sala vacía no se puede medir el rechazo trasero, y el espectro Doppler es
  plano. Falta una prueba controlada: una persona camina detrás y luego delante
  de cada array.
- Geometría para `t9_tiles` (10-14), confirmada por el usuario:
  - los dos arrays están a 0°, paralelos a su borde, con la chapa pequeña detrás;
  - A: visto desde arriba en el mapa, de izquierda a derecha, las antenas van
    3, 4, 2, 1 (la 1 hacia +x), así que u_A = (−1, 0);
  - B: de arriba abajo, las antenas van 1, 2, 4, 3 (la 1 hacia +y), así que
    u_B = (0, −1);
  - es el mismo orden que el 10-13; solo cambia el giro (antes se estimaba en
    ~14° y ~18°).
- Decisión de 10-14: no se hace una prueba de rechazo trasero aparte. Se mide
  dentro de `t9_tiles`, comparando 20 s detrás de cada array (a ~1 m) con la
  baldosa equivalente delante: (3,2) para A y (4,3) para B. Hasta entonces, que
  las chapas rechazan lo de atrás es una suposición, no un resultado.

**Nota de análisis.** `n` se guarda sin envolver y los K0 de A y B pueden
diferir en 512, una vuelta completa (en t6, A = B + 512). Para emparejar los
bursts de A y B hay que usar `n mod 512` junto con la época de 10,24 s sacada
del reloj del host. Si se empareja por `n` sin envolver, los bursts quedan
desplazados 10,24 s.

### 6.4 Prueba de baldosas `t9_tiles` a 100 MHz (2026-10-14, 11:38)

**Montaje.** Arrays a 0°, paralelos a su borde y centrados. A con antenas 3, 4,
2, 1 de izquierda a derecha y B con 1, 2, 4, 3 de arriba abajo. Chapas pequeñas
(switches) detrás y antenas 5G a más de 1 m. 122,88 Msps, pulsos cada 50 µs,
`--lookahead-ssb 7 --min-margin-ms 8`, 520 s.

**Protocolo.**
- 0–20 s, sala vacía.
- 16 tramos de 20 s, moviéndose en el sitio: (3,3) para calibrar y luego (3,4),
  (3,5), (2,5), (1,5), (1,4), (2,4), (2,3), (1,3), (1,2), (2,2), (3,2), (4,3),
  (4,4), (4,5) y otra vez (3,3).
- 430–450 s detrás de A y 460–480 s detrás de B, a ~1 m.
- 485–510 s, sala vacía.

**Funcionamiento.**
- A perdió el 0,5 % de los turnos. B perdió el 5,6 %, cada vez más con el
  tiempo: del 0–2 % hasta los 300 s al 21 % al final.
- Las pérdidas de B son bloqueos del bucle de 10–100 ms en el host, crecientes,
  con el 5G bien (PSS de 0,93–0,96, sin pérdidas de enganche).
- Hipótesis: el recolector de basura de Python recorre los ~10⁶ objetos que se
  acumulan (`rx_rows`, `events`). Propuesta: `gc.freeze()` periódico y vaciar
  `events`. No probado.
- Numeración SFN con 0 discrepancias y ninguna ventana RX perdida.

**Sincronización: depende de dónde esté la persona.**

| Tramo | Mediana del desfase | Desviación |
|---|---|---|
| Vacío inicial / final | 3,0 / 1,1 ns | 8,5 / 10,0 ns |
| Persona en la zona | −39 a +6 ns según la baldosa | 14–41 ns ((4,3): 40,8 ns) |
| Persona detrás de A / B | 14,5 / 11,3 ns | 12,6 / 11,6 ns |

- El ruido rápido se mantiene en 1,3–2,2 ns y el camino WiFi (lag_A + lag_B)/2
  en −1,0 ± 0,1 ns todo el tiempo.
- Lo que se mueve es la referencia 5G de un AP respecto del otro: la persona
  cambia el multitrayecto del SSB (3,6 MHz). Es el mismo efecto que en
  `t4_tiles`, más pequeño.
- Este error no afecta a las medidas relativas (ángulo, Doppler, exceso de
  retardo), que se toman respecto del propio directo. Sí afecta a la
  temporización absoluta y a las guardas.
- El intercambio WiFi bidireccional mide el desfase burst a burst con ~1,3 ns
  (propuesta: corregirlo con él).

**Rechazo trasero de las chapas.** Energía Doppler (2,5–80 Hz, relativa al
estático) con el cargador que alinea la fase antes de rellenar huecos:

| | Vacío | Delante, a ~1 m | Detrás, a ~1 m |
|---|---|---|---|
| A (A→B / B→A) | −20,1 / −18,1 dB | (3,2): +2,0 / +2,0 dB | −19,7 / −17,5 dB |
| B | igual | (4,3): +2,9 / +2,4 dB | −20,0 / −17,9 dB |

- Delante se ve a la persona 20–23 dB por encima del vacío; detrás, nada
  (+0,1–0,5 dB, ya en el suelo de ruido).
- Descontando ~5 dB de geometría biestática, el rechazo trasero es de ≥ 15 dB.
  Las chapas pequeñas bastan.

**Ángulos y posición** (grid restringido a la zona 3 × 3 m):
- En A→B, el ángulo estimado crece sistemáticamente respecto del real, igual en
  A y en B. Encaja con una separación efectiva de 3,36 cm (k = 1,12) en lugar de
  3,0 cm. Las bases de las antenas se tocan, así que la separación probablemente
  es su diámetro. Pendiente de medir.
- Con 3,0 cm (solo calibración con el compañero): mediana de 0,30 m, máximo de
  0,67 m, 13 de 15 a ≤ 0,6 m.
- Con k = 1,12: mediana de 0,10 m y máximo de 0,18 m. Dejando una baldosa fuera
  también sale k = 1,12 en las 16 y una mediana de 0,10 m.
- Calibrar el factor con la (3,3) sola no lo fija bien (kA = 1,08 y kB = 0,96 o
  0,92), porque esa baldosa queda de frente a los dos.
- B→A es peor: en las baldosas lejanas confunde a la persona con la dirección
  del compañero (mediana de 0,36 m y media de 1,07 m). Combinando los dos
  sentidos, la mediana es de 0,32 m con 3,0 cm.
- La (3,3) repetida al final da 0,11 m frente a 0,10–0,11 m al principio: nada
  se movió.

**Distancia biestática** con el perfil de retardos (1 muestra = 2,44 m):
- Proceso: anular las dos firmas espaciales estáticas en cada retardo, tomar la
  energía Doppler por retardo, restar la de la sala vacía (0–20 s) y refinar el
  pico con una parábola.
- A→B: correlación de 0,95 con el exceso de camino real (0,39–3,79 m) y error
  mediano de 0,20 m. B→A: correlación de 0,73 y 0,28 m.
- Las peores son (4,4) y (4,5), en la parte con huecos de B.
- Es un resultado preliminar: la cadena de proceso se eligió con estos mismos
  datos. Hay que confirmarlo en otra ejecución.

Scripts de análisis (scratchpad, no versionados): `t9c.py` (cargador con
rejilla uniforme), `energy.py`, `loc9.py`, `pos9.py`, `range9b.py`.

### 6.5 Repetición `t10_tiles`: análisis fijado de antemano (2026-10-14)

Decidido con el usuario antes de grabar: la repetición se analiza con los
parámetros de abajo, sin ajustar nada con sus datos. Sirve de comprobación
independiente de §6.4.
- Mismo montaje y mismo protocolo que `t9_tiles` (mismas baldosas, orden y
  tiempos; tramos detrás de A y de B).
- Separación efectiva fija de **3,36 cm (k = 1,12)** en A y en B; geometría de
  §6.4.
- Localización con el sentido **A→B**, rejilla de 0,05 m restringida a la zona
  de 3 × 3 m, ventanas de 0,5 s, Doppler de 2,5–80 Hz, anulación del estático
  de rango 1 y 3 s recortados en cada borde de tramo. Error frente al centro de
  la baldosa.
- Distancia biestática: cadena `range9b.py` (anulación espacial de rango 2 por
  retardo y resta del vacío de 0–20 s), sin cambios.
- Criterio: se considera confirmado si la mediana de localización es ≤ 0,2 m y
  la correlación de la distancia biestática es ≥ 0,8.
- Software: el mismo comportamiento que en t9 (`--gc-mode default`), con
  diagnóstico añadido en `summary.json`: pausas del GC por generación y parones
  del bucle principal de más de 20 ms (`stalls`), indicando si coinciden con un
  GC completo. Copia previa: `x410_two_ap_sense_pre_gc.py`.

**Investigación de los turnos perdidos de B (10-14).**
- En simulación (PC5, Python 3.12, 240 s), las pausas del GC completo llegan a
  39–43 ms y crecen con la duración.
- Congelar los objetos (`gc.freeze()`) o desactivar el GC completo baja esas
  pausas a 6–10 ms. Pero las dos opciones provocan parones de ~100 ms cada
  ~40 s (74–93 turnos tarde, frente a 0 por defecto), así que se descartan.
- No hay basura con ciclos (`DEBUG_SAVEALL` vacío) y al final solo hay ~45 000
  objetos vigilados.
- En x410srv (Python 3.10, 520 s simulados) el GC completo llega a 103 ms. Eso
  coincide con los márgenes de −10 a −100 ms de t9, pero solo hubo 9 pausas,
  frente a ~900 rachas de retraso de B en t9: el GC explica una parte, no todo.
- La simulación en el servidor no reproduce bien el caso real (el simulador
  consume mucha CPU).
- La causa sigue abierta: se diagnostica en la repetición con el registro de
  parones.

### 6.6 Resultado de la repetición `t10_tiles` (2026-10-14, 13:40): CONFIRMADO

Los servidores y los X410 se apagaron 2 min antes de la prueba. El montaje no
se tocó y se usó el mismo protocolo que en t9. Análisis fijado de antemano
(§6.5).

| | t9 | **t10 (fijado de antemano)** |
|---|---|---|
| Localización A→B, 3,36 cm | mediana de 0,10 m, máx. de 0,18 m (k ajustado con estos datos) | **mediana de 0,15 m, máx. de 0,25 m; 16 de 16 a ≤ 0,3 m** |
| Localización A→B, 3,0 cm nominal | 0,30 m | 0,30 m (máx. de 0,63 m) |
| B→A / los dos sentidos, 3,36 cm (no fijado de antemano) | peor (confunde con el compañero) | 0,09 m / **0,09 m, máx. de 0,16 m** |
| Distancia biestática A→B | correlación de 0,95, error mediano de 0,20 m | **correlación de 0,95, 0,14 m** (B→A: 0,97, 0,22 m) |
| Rechazo trasero (delante / detrás sobre el vacío) | +20–23 / +0,1–0,5 dB | +22–24 / −0,3–+0,1 dB |

- Se cumplen los dos criterios (mediana ≤ 0,2 m y correlación ≥ 0,8). La mejor
  k en t10 sería (1,12; 1,06), con una mediana de 0,07 m: solo diagnóstico, no
  se usa.
- **La sincronización fue mucho peor que en t9 con la persona en la zona:**
  - con la sala vacía o la persona detrás de los arrays, el desfase tiene una
    desviación de 10–15 ns (mediana de −8 a +4 ns);
  - con la persona en la zona, la mediana va de −230 a −3 ns según la baldosa,
    con una desviación de 27–150 ns. Las peores son (3,3), (3,4), (4,4) y la
    (3,3) repetida;
  - el camino WiFi se mantiene en 0,7 ± 0,1 ns.
  - Causa probable: el PSS de B está en 0,60–0,86 durante t10, frente a
    0,93–0,96 en t9, y cae más (0,60–0,64) justo en esas baldosas centrales. Al
    apagar el USRP pudo moverse la antena 5G de B, encima del rack.
- **Aun así, el sensing no se degrada:** la localización y la distancia salen
  iguales o mejor con errores de sincronización de hasta ~200 ns, porque ángulo,
  Doppler y distancia se miden respecto al directo de cada AP. Es un resultado
  clave para el artículo sobre qué sincronización necesita el sensing
  multiestático por turnos.
- **Turnos perdidos, esta vez en A:** 7,6 % (B: 0,3 %).
  - Solo hubo 4 parones del bucle de más de 20 ms en cada AP, 3 de ellos con GC
    completo (70–87 ms).
  - La causa es la latencia del 5G: el margen mediano en A fue de 28–60 ms,
    frente a 81–100 ms en B. Equivale a ~115 ms de latencia de proceso del PSS
    en A frente a ~60 ms en B. Además, A tuvo 611 bloques 5G descartados
    (`nr_dsp_drops`), frente a 34 en B.
  - El pipeline 5G usa 2 hilos (`--nr-workers 2`) y se satura. En t9 le pasó a
    B. Propuesta: `--nr-workers 4` y vigilar `nr_dsp_drops`.
- Copias: `t10_tiles_*` en PC5 (`~/sync_backups/x410_two_ap_results_20261014/`)
  y en `~/x410_backup/20261014/` de cada servidor, con los checksums
  verificados.

### 6.7 Separación medida y `t11_nrw4` (2026-10-14, 14:00:30)

- **Separación medida por el usuario:** 10 cm entre los centros de las antenas
  extremas (1 y 3), es decir, 3,33 cm entre antenas (k = 1,11). Coincide con los
  3,36 cm (k = 1,12) deducidos de los datos de t9 y confirmados en t10: el factor
  queda justificado físicamente.
- **`t11_nrw4`:** 3 min con `--nr-workers 4`, sala vacía (energía Doppler en el
  suelo durante toda la prueba), antena 5G de B reajustada. Lanzado por el
  asistente con autorización del usuario.
  - PSS de A: 0,95 (p10 de 0,88). PSS de B: 0,97 (p10 de 0,95).
  - Turnos perdidos: 3–3,5 % solo en los primeros 30 s (arranque) y **0,0 %
    después**. Margen mediano de 82–91 ms en los dos AP y `nr_dsp_drops` = 0.
    En t10 eran 611 en A.
  - Sincronización: desviación del desfase de 8,9 ns, ruido rápido de 1,2 ns,
    deriva lenta (1 s) de 6,9 ns y camino de −0,9 ± 0,1 ns. Es el mejor
    resultado a 100 MHz.
  - Conclusión: la latencia del pipeline 5G era la causa de los turnos
    perdidos. Se usará `--nr-workers 4` a partir de ahora.

### 6.8 Precisión con ventanas cortas y dashboard (2026-10-14)

Error de localización con 3,36 cm, por ventanas de integración (`shortwin.py`,
mapas de 0,5 s promediados):

| Ventana | t9 A→B | t9 ambos | t10 A→B | t10 ambos |
|---|---|---|---|---|
| 0,5 s | 0,15 m (90 % a ≤ 0,3 m) | 0,14 m | 0,16 m (83 %) | 0,11 m (92 %) |
| 1 s | 0,11 m | 0,11 m | 0,16 m | 0,11 m |
| 2 s | 0,11 m | 0,10 m | 0,15 m | 0,11 m |
| 5 s | 0,10 m | 0,10 m | 0,15 m | 0,11 m |
| ~14 s | 0,11 m | 0,09 m | 0,15 m | 0,07 m |

- Con 0,5 s el error ya es casi el mismo que con 14 s, y el 99 % de las
  ventanas queda a ≤ 0,6 m. Es suficiente para seguir a alguien que camina (no
  probado todavía).
- Dashboard de resultados de t9, t10 y t11:
  https://claude.ai/artifact/5n7nVJ5eVUkJweup81eNsM. Recoge la sincronización
  en sala vacía como cifra principal y una sección aparte con el efecto de la
  persona sobre la sincronización y la robustez del sensing.

**Plan para la próxima sesión.**
1. Chapas más pequeñas y la antena 5G de A fuera de su sombra.
2. Confirmar el giro de cada array: qué antena (1 o 3) queda más cerca del borde
   de la cuadrícula y a qué distancia está cada extremo.
3. Ejecución de 3 min sin nadie a 122,88 Msps, para comprobar que la
   sincronización vuelve a ~8 ns y la calidad del PSS de A a > 0,9.
4. Baldosas con una baldosa de calibración al principio, más baldosas
   (incluida la fila 5) y perfiles de retardo. Marcar las baldosas en el suelo
   para no equivocarse.

## 7. Progreso frente a la arquitectura y al artículo

| Objetivo | Estado | Evidencia o hueco |
|---|---|---|
| Dos X410 independientes, sin 10 MHz/PPS común, misma celda 5G | ✅ | todas las ejecuciones de dos AP |
| Mismo ciclo identificado por ambos AP | ✅ (2026-10-13) | SFN decodificado del PBCH/MIB, 0 discrepancias y 0 aciertos de centinela (§5.1) |
| Heartbeat PSS y tracker | ✅ | 17–19 ns rms |
| Programar el ciclo siguiente y quedarse en silencio si es tarde | ✅ | antelación de unos 120 ms; turnos tardíos ≈ 1 % |
| Agenda complementaria | ✅ | 10 Hz (slots 1/3) y 200 Hz (entrelazado) |
| Error PSS→PPDU caracterizado | ✅ | 8 ns rms con la sala quieta; desfase entre AP de −22 a −85 ns según la geometría |
| Sin late commands ni underflows | ⚠️ | TX tardíos en el X410 en ≈ 1 % de los envíos y algunos underflows en B |
| Burst HE NDP-like | ❌ **desviación** | se usan pulsos Zadoff-Chu; decidir si se migra o se justifica |
| WiFi 2×2 TDD + RX 5G | ✅ superado | 4×3 + 5G por AP (aprobado por el usuario) |
| Arquitectura final B210 5G + X410 con 10 MHz/PPS local | ❌ | falta la fuente de referencia local y la integración del B210 |
| CFO/CPE caracterizados sin fase común entre AP | ✅ | CFO predicho por el 5G; corrección hecha con los propios pulsos |
| Doppler sectorial | ✅ parcial | detección y espectrogramas por sector; sin protocolo por sector |
| **Comparación sin sincronizar** (relojes libres / LBT) | 🔄 | emulación hecha (§10.2.1) y baselines B0/B1/B2 validados en simulación (§10.2.2); falta la campaña real |
| Localización por baldosa | ✅ (2026-10-14) | t9: 0,10 m; t10, fijado de antemano: 0,15 m (ambos sentidos: 0,09 m); 0,5 s: 0,11–0,16 m (§6.4–6.8) |
| Distancia biestática | ✅ (2026-10-14) | correlación de 0,95 en t9 y t10 (§6.4, §6.6) |
| Sincronización en frecuencia con el 5G | ✅ (2026-10-14) | t12: CFO entre AP de 381 → 15 Hz sin intercambio (§10.3) |
| 5G también como canal de sensing (multibanda) | ❌ | el 5G solo da tiempo y frecuencia |
| Integración en el framework (perfiles, schemas, NFS, catálogo, inferencia en PC5) | ❌ | `testmultiband` va por fuera |

Desviaciones conscientes respecto a lo descrito en la arquitectura:

1. Waveform ZC en lugar de HE NDP-like.
2. 4×3 en lugar de 2×2.
3. ~~Numeración de SSB por NTP en lugar de SFN~~: resuelto el 2026-10-13.
4. CFO estimado con los pulsos WiFi; la predicción del 5G se usa como
   comparación, no como corrección.
5. Tasa WiFi de 61,44 Msps en lugar de los 122,88 planteados.

## 8. Lo que no se puede afirmar todavía

- Coordinación de sensing a 200 Hz con SFN: solo está probada la coordinación a
  10 Hz; falta repetir una ejecución de sensing con `--numbering sfn`.
- Funcionamiento con NTP desactivado y relojes de host desajustados: el sistema
  no usa NTP para coordinar (§5.2.6), pero falta la prueba explícita.
- Estabilidad a largo plazo: la ejecución más larga dura 3 minutos; no se han
  probado varias celdas ni otras salas.
- Rendimiento con HE NDP-like o 4×4 completo.
- Que la sincronización mejore el sensing: no existe todavía la referencia sin
  sincronizar.
- Localización, distancia o detección de personas quietas.

## 9. Problemas abiertos y riesgos

1. **Código sin versionar:** `testmultiband` solo existe en los dos servidores.
   Falta decidir su repositorio de destino y sanear defaults de laboratorio.
2. **Avisos `L` (TX tardío en el X410):** ≈ 1 % de los envíos, sobre todo al
   arrancar y en algún pico a mitad de ejecución; algunos `U` en B. Los
   instantes de los avisos no se guardan en fichero.
3. **Picos cortos de energía de movimiento:** bursts con interferencia o
   tardíos que no se interpolan.
4. **Antenas** con unos 25 dB de pérdida a 5 GHz; compensado con ganancias
   40/40.
5. **Lóbulo espejo** de los arrays lineales (delante/detrás).
6. **Ancho de banda** limitado por un único 10GbE y por el procesado en Python,
   no por el X410.

## 10. Siguientes pasos propuestos

Sin comprar hardware, por orden:

1. ~~Decodificar SFN/PBCH/MIB~~ (hecho el 2026-10-13). Quedan dos pruebas:
   - coordinación con NTP desactivado en ambos hosts y el reloj de B
     desajustado unos 7 s (`sudo timedatectl set-ntp false`; restaurar con
     `set-ntp true`), 240 s;
   - una ejecución de sensing a 200 Hz con SFN.

   Opcional, para un revisor exigente: transmitir en cada burst el número de SSB
   común (BPSK sobre los 8 pulsos) para verificar por radio el número completo
   sin reloj externo. No es necesario para que el sistema funcione.
2. Comparación sin sincronizar (relojes libres —diseño en §10.1, PENDING—, LBT o
   turnos aleatorios) con el mismo hardware: colisiones, irregularidad de revisita, calidad Doppler y
   pérdida de detección.
3. Localización por cruce de ángulo de salida y de llegada sobre la componente
   Doppler, con la persona en baldosas conocidas.
4. Ejecuciones largas (30–60 min) y corrección de los TX tardíos.
5. Decidir waveform: HE NDP-like o ZC justificado.
6. Formato de dataset del framework para el equipo del modelo.

Hardware a valorar (no aprobado):

| Opción | Efecto |
|---|---|
| Antenas buenas de 5–7 GHz | unos +20 dB; permite probar 6 GHz |
| Chapa reflectora detrás de cada array | elimina el lóbulo espejo |
| 100GbE o varios 10GbE con otra imagen FPGA | 160 MHz → 1,9 m, 400 MHz → 0,75 m de resolución |
| 6 GHz (WiFi 6E) | espectro limpio y canales anchos; no estrecha los haces |
| Referencia 10 MHz + PPS local por AP (p. ej. Leo Bodnar LBE-1421) | arquitectura final B210 + X410 |
| Más USRPs por AP (8 antenas) | haces de unos 12°; coste alto |
| Ondas milimétricas | no: el X410 llega a 7,2 GHz y cambiaría el artículo |


### 10.1 Diseño inicial del baseline con relojes libres (2026-10-13; integrado en §10.2)

Diseño propuesto el 2026-10-13 y aceptado por el usuario como pendiente. No hay
código todavía.

**Qué es.** Cada AP transmite su burst de forma periódica con su propio reloj
(cada 5 ms en ticks del X410), sin anclarse al PSS ni al SFN. La separación
entre los bursts de A y B depende de cuándo arrancó cada uno y de la diferencia
de sus relojes.

**Qué se espera.**
- Con los X410 tal cual (≈ 0,07 ppm entre ellos, unos 70 ns/s de deriva), la
  separación es prácticamente fija. Cada AP pierde unos 0,85 ms de cada 5 ms
  (su propio burst más la guarda), así que en ≈ 2 × 0,85/5 ≈ 35 % de los
  arranques chocan durante toda la prueba y en el resto no chocan nunca: el
  rendimiento depende del arranque.
- Emulando cristales de AP comerciales (tolerancia WiFi ±20 ppm) con un periodo
  de 5 ms × (1 + ε), por ejemplo +10 ppm en A y −10 ppm en B, la separación da
  la vuelta a los 5 ms cada ≈ 4 min y hay rachas periódicas de colisión durante
  ≈ 1/3 del tiempo.

**Receptor del baseline (favorable, para una comparación conservadora).**
1. Adquisición: escucha periodos completos y busca el burst del compañero.
2. Seguimiento: predice la llegada a partir de las anteriores (sigue el reloj del
   compañero, como un beacon) y abre ventanas estrechas.
3. Readquisición si lo pierde.

Los bursts del compañero que caen sobre el propio TX se marcan como colisión.
El 5G se graba solo como diagnóstico (verdad temporal a posteriori) y no se usa
para programar.

**Métricas frente al modo sincronizado.** Colisiones (% de bursts solapados),
bursts recibidos, regularidad de la revisita (histograma y huecos),
espectrogramas Doppler con huecos, detección a igual falsa alarma y, más
adelante, continuidad y error de localización.

**Plan.**
1. `--mode freerun` en `x410_two_ap_sense.py` con `--period-ms`,
   `--period-ppm` y fase de arranque aleatoria o fijada, más el receptor de
   adquisición y seguimiento.
2. Simulación con el compañero en su propio reloj y su ε.
3. `compare_sync_freerun.py`, reutilizando `sense_doppler.py`.
4. Campaña: referencia sincronizada de 2 min, 10 × 30 s libres con ε = 0, 5 min
   con ±10 ppm (también ±2 ppm, típico de un TCXO), y después andando y en
   baldosas.

**Por confirmar al implementarlo:** el receptor favorable, los valores de ε y
mantener el 5G solo como diagnóstico.

### 10.2 Plan de comparación con el estado del arte (2026-10-14; puntos 1 y 2 aprobados)

Sustituye al diseño de §10.1, que queda integrado aquí como B0/B1.

**Preocupación del usuario.** Si una alternativa sin 5G localiza igual de
bien, el artículo no puede apoyarse en la precisión. Debe demostrar que la
sincronización por 5G da coordinación de calidad sensing:
- sin negociación ni tramas de control;
- sin líder;
- sin que los AP tengan que oírse;
- con muestreo regular y mejor eficiencia de tiempo de aire, y que escala
  con N AP.

**Estado del arte revisado.**
- IEEE 802.11bf: sensing entre un iniciador y respondedores, normalmente
  AP–estación. Incluye intercambio de capacidades, sesión negociada y, en cada
  medida, tramas de control (sondeo, NDPA o trigger, NDP, informe). No define
  sensing AP–AP entre redes independientes, y el multiestático exige una base
  de tiempo común sin decir cómo conseguirla
  (https://arxiv.org/html/2310.17661v1).
- IEEE 802.11bn (Wi-Fi 8), coordinación multi-AP: descubrimiento y
  negociación por aire, sin backhaul; Co-TDMA con ICF/ICR y MU-RTS TXS en cada
  TXOP. Exige que los AP se oigan y sean Wi-Fi 8, y respetar la contención
  clásica. Orientado a comunicaciones; el tutorial no trata sensing
  (https://arxiv.org/html/2606.13759v1).
- Sensing biestático asíncrono (radar WiFi pasivo): el receptor busca y sigue
  al transmisor y limpia los desfases con técnicas entre antenas (CACC/DCACC,
  CASR), con ambigüedad espejo o pérdida de fase, o usando solo potencia. No
  coordina quién transmite cuándo (https://arxiv.org/pdf/2602.14004,
  https://arxiv.org/pdf/2109.06006, https://arxiv.org/html/2511.22144v2).
- Sincronización por balizas (TSF): ~4 µs en la especificación original. Como
  referencia de infraestructura, 3GPP supone ±130 ns entre emplazamientos
  (https://arxiv.org/pdf/2511.13104).

**Esquemas.**

| Id | Esquema | Cómo se evalúa |
|---|---|---|
| S5G | El nuestro: turnos y numeración desde el SSB/SFN 5G | t9–t11 (y t12 con sincronización en frecuencia) |
| B0 | Horario fijo sin sincronización de radio (relojes libres o NTP) | prueba real y emulación |
| B1 | Relojes libres y receptor que busca y sigue al otro AP (estado del arte entre AP no conectados) | prueba real y emulación |
| B2 | Seguidor WiFi: B toma la hora de los bursts de A, sin 5G. Rival más fuerte entre dos AP que se oyen | prueba real, también con el enlace degradado |
| B3 | Negociado tipo 802.11bf TB / 802.11bn Co-TDMA (control por medida y contención CSMA) | emulación con tiempos de aire de tramas reales |

**Lo que se espera.**
- B0 y B1 pierden por colisiones, huecos y muestreo irregular.
- B2 probablemente iguale a S5G en precisión si A y B se oyen bien: el WiFi a
  100 MHz mide el tiempo mejor que el SSB de 3,6 MHz.
- B3 puede localizar bien, pero gasta tiempo de aire en control y muestrea
  con jitter.
- S5G gana en ausencia de negociación, independencia de líder y de que los AP
  se oigan, escalado, arranque y eficiencia.

**Métricas.**
- Sensing: error de localización, detección y calidad Doppler.
- Fracción del tiempo de aire que es sensing útil, y tramas de control por
  medida (S5G: 0).
- Regularidad del muestreo: huecos y jitter.
- Colisiones.
- Tiempo hasta el primer turno válido y reenganche.
- Robustez ante pérdida del líder, enlace AP–AP degradado y tráfico.
- Escalado con N AP (2–16) y AP ocultos.

**Método.**
1. *(Aprobado.)* Implementar en `x410_two_ap_sense.py` `--mode freerun`
   (B0/B1: periodo con el reloj propio, `--period-ppm`, fase de arranque,
   receptor de adquisición y seguimiento) y `--mode wifi-follow` (B2: el
   seguidor programa con los bursts del líder en lugar del PSS). Validar en
   simulación en PC5 sin tocar los servidores.
2. *(Aprobado.)* Emulación sobre las trazas reales de t9/t10. Se aplica al
   canal medido a 200 Hz el patrón de muestreo de cada esquema (colisiones con
   relojes a ε = 0 y ±10 ppm; contención con distintas cargas; control por
   medida en B3) y se recalculan localización, detección y Doppler.
3. Campaña real con el mismo montaje y protocolo de baldosas: S5G de
   referencia; B0/B1 con ε = 0 y ±10 ppm; B2 normal, con el enlace degradado y
   con caída del líder.
4. *(Preparado; en ejecución el 2026-10-14.)* Sincronización en frecuencia con
   el 5G (§10.3).
5. Modelo de escalado con N AP: tiempo de aire de control, error encadenado de
   seguidores, AP ocultos.

**Riesgos y matices.**
- Normativa: en 5 GHz, ETSI EN 301 893 exige LBT. Hay que verificar si los
  bursts periódicos (~8 % del tiempo por AP) entran en la excepción de
  señalización corta (≤ 10 % en 50 ms) o si hace falta LBT. Sin verificar.
- El «trigger» de sensing es implícito: el SSB sustituye a las tramas trigger
  de 802.11bf. Por eso S5G no tiene control por medida.

### 10.2.1 Resultado del punto 2: emulación sobre las trazas reales (2026-10-14)

`emu.py` (copia en `~/sync_backups/.../analysis/emulation/`) aplica a los
canales medidos a 200 Hz de t9 y t10 el destino de cada burst según el
esquema (recibido, perdido o recibido con retraso) y recalcula la
localización con la cadena de §6.5 (3,36 cm; un receptor que rellena huecos y
remuestrea, que es lo favorable al baseline).

Modelos:
- B0: B escucha ±50 µs donde dice el horario acordado, con su propio reloj.
- B1: B escucha siempre salvo durante su propio TX (bursts de 0,40 ms y
  guardas de 50 µs; colisión si las fases distan menos de 0,45 ms, el 18 % de
  las fases).
- B3: acceso por contención (DIFS, backoff de 0–15 ranuras de 9 µs y 0,30 ms
  de control por medida) con tráfico de fondo de ráfagas de 2 ms y ocupación
  ρ; la medida se pierde si se retrasa más de 5 ms.

| Esquema | Bursts recibidos | Baldosas localizadas | Error t9 (A→B / ambos) | Error t10 (A→B / ambos) |
|---|---|---|---|---|
| S5G (medido) | 100 % | 16/16 | 0,10 / 0,11 m | 0,15 / 0,09 m |
| B0, ε = 0 | 0 % | 0/16 | — | — |
| B0, ±10 ppm | 1,9 % | 2/16 | — | — |
| B1, ε = 0, fase buena (82 % de los arranques) | 100 % | 16/16 | igual que S5G | igual que S5G |
| B1, ε = 0, fase en colisión (18 % de los arranques) | 0 % | 0/16 | — | — |
| B1, ±10 ppm (dos fases de arranque) | 82,7 % (apagones de ~43 s cada ~250 s) | 14–15/16 | 0,10 / 0,09–0,11 m | 0,14–0,18 / 0,07–0,11 m |
| B1, ±2 ppm (TCXO) | 100 % durante los 520 s (ciclo de ~21 min) | 16/16 | igual que S5G | igual que S5G |
| B3, carga del 0 / 30 / 60 / 80 % | 100 / 96 / 89 / 78 % | 16/16 | 0,10–0,11 / 0,11 m | 0,14–0,15 / 0,09 m |

Con B3, el retraso de acceso mediano va de 0,41 a 1,25 ms y el p95 llega a
12 ms con un 80 % de carga.

**Conclusión (confirma la preocupación del usuario).**
- Cuando un esquema consigue los bursts, la precisión de localización es la
  misma: a 200 Hz sobra muestreo y el remuestreo tapa huecos de hasta un 22 %.
- Lo que separa los esquemas es la disponibilidad y el coste:
  - B0 no funciona;
  - B1 depende del azar del arranque y de los cristales (ciego en el 18 % de
    los despliegues con ε = 0 y apagones periódicos con ±10 ppm);
  - B3 funciona, pero con control en cada medida, retrasos de acceso y la
    exigencia de AP 802.11bf/bn que se oigan y negocien.
- El artículo debe apoyarse en: disponibilidad determinista sin negociación,
  cero tramas de control, independencia de que los AP se oigan, escalado con
  N AP y sincronización en frecuencia (§10.3). No en la precisión.

### 10.2.2 Resultado del punto 1: baselines implementados y validados en simulación (2026-10-14)

`x410_two_ap_baseline.py` (desplegado en los dos servidores sin ejecutar,
md5 409ac8f8ca34; script aparte para no tocar `x410_two_ap_sense.py`). Mismo
burst, extracción y formato de salida (`sense_rx.npz` compatible con
`t9c.load`), más `rx_log.jsonl`.
- `--mode freerun --rx schedule`: B0. También es el líder de B2.
- `--mode freerun --rx track`: B1, con ventanas anchas de búsqueda entre los
  propios bursts y después seguimiento con un ajuste lineal y ventanas
  estrechas. Si la llegada predicha cae sobre su propio TX, cuenta colisión.
- `--mode follow`: B2. No transmite hasta encontrar al líder; después
  transmite `--follow-offset-ms` (2,5 ms) tras cada burst predicho.
- Parámetros: `--period-ppm` (error de reloj emulado), `--phase-ms`, y en
  simulación `--sim-partner {freerun,follower}`, `--sim-partner-ppm`,
  `--sim-partner-phase-ms`.
- El 5G se graba solo como verdad (`nr_events.jsonl`); no se usa para
  programar.

Simulación de 40 s a 122,88 Msps (copias en `analysis/baseline_sims/`):

| Caso | Resultado |
|---|---|
| B0 (fases 1,0 / 2,6 ms) | 0 de 8000 bursts recibidos |
| B1, compañero a +20 ppm | 100 % recibidos, encontrado en 0,04 s, 0 pérdidas de seguimiento |
| B1, fase en colisión (1,0 / 1,2 ms) | 0,6 % (solo al acabar el propio TX) |
| B2, seguidor (líder a +10 ppm) | encuentra al líder en 0,04 s, primer TX propio a 0,92 s, 100 % recibidos; 2 % de TX tarde al arrancar |
| B2, líder | 8000 de 8000 bursts del seguidor recibidos |

Corregido durante la validación: el seguidor extrapolaba demasiados periodos
con el periodo nominal antes de medirlo (a ±20 ppm se salía de la búsqueda de
±500 ns), y descartaba detecciones que llegaban desordenadas.

**Siguiente paso propuesto (no aprobado).**
- Campaña real corta: B0 3 min; B1 a ±10 ppm 6 min (para ver un apagón); B2
  3 min; B2 con el enlace A–B degradado; B2 parando el líder a mitad. Todo con
  una persona caminando.
- Modelo analítico de tiempo de aire y escalado con N AP (punto 5).

### 10.2.3 Campaña real de baselines (aprobada y preparada el 2026-10-14, 17:01)

Una persona camina por la zona durante todas las pruebas. 122,88 Msps,
`--nr-workers 4`, ganancias 40/40 salvo donde se indica.

| Id | Hora | Esquema | A | B |
|---|---|---|---|---|
| c1_b0 | 17:06:00, 180 s | B0 | `--mode freerun --rx schedule --phase-ms 1.25` | `--mode freerun --rx schedule --phase-ms 3.75` |
| c2_b1 | 17:12:00, 360 s | B1 a ±10 ppm | `--mode freerun --rx track --period-ppm 10 --phase-ms 1.25` | `--mode freerun --rx track --period-ppm -10 --phase-ms 3.75` |
| c3_b2 | 17:21:00, 180 s | B2 | líder: `--mode freerun --rx schedule --period-ppm 10 --phase-ms 1.25` | `--mode follow` |
| c4_b2stop | 17:27:00 | B2 con caída del líder | líder 100 s; relanzado a las 17:30:20 durante 40 s (`c4_b2stop_A2`) | `--mode follow`, 240 s |
| c5_b2weak | 17:34:00, 180 s | B2 con el enlace A→B degradado | líder con `--tx-gain 20` | `--mode follow` |
| c6_s5gweak | 17:40:00, 180 s | S5G de referencia, mismo enlace degradado | `x410_two_ap_sense.py`, `--tx-gain 20` | `x410_two_ap_sense.py` |

Como las fases se cuentan desde la hora del host de cada AP (NTP, ~8 ms de
diferencia), la fase relativa real en B0/B1 es aleatoria, como en un
despliegue real.

**Ejecución.**
- **c1_b0 (17:06):** 0 de 36 000 bursts recibidos en A y en B, como predijo la
  emulación.
- **c2_b1 (17:12): fallo del script, no del esquema.** La búsqueda abría una
  ventana de 4,5 ms × 3 canales cada 10 ms (~5 Gbit/s). Llegaron a acumularse
  ~900 ventanas sin procesar, hubo miles de avisos de TX tardío y al final
  `RfnocError: OpTimeout` en el control del X410. Detenido con SIGKILL; los
  X410 siguen respondiendo. Restos en `c2_b1_*_crashed`.
- **Corregido** (`x410_two_ap_baseline.py`, md5 8e97e172a4b3; versión
  anterior en `*_v1.py`) y validado en simulación:
  - ventanas de búsqueda de 1,2 ms que barren el periodo, 10 por segundo como
    mucho y no más de 4 pendientes;
  - hasta medir el periodo, las ventanas de seguimiento buscan en ±50 µs y
    extrapolan hasta 150 periodos;
  - el TX del seguidor tiene su propio puntero: empieza a 0,88 s, sale
    2,498 ms después del líder con 1 ns de dispersión y sigue su reloj a
    +10 ppm.
- Nuevo horario: c2_b1 a las 17:21; c3_b2 a las 17:30; c4_b2stop a las
  17:36 (relanzamiento de A a las 17:39:20); c5_b2weak a las 17:43;
  c6_s5gweak a las 17:49.
- Se camina al azar por la zona: la campaña mide disponibilidad; la
  precisión por baldosa ya está en la emulación (§10.2.1).

### 10.2.4 Resultado de la campaña real de baselines (2026-10-14, 17:06–17:36)

Pruebas 1–3 hechas, con una persona caminando al azar. El usuario y el
asistente acuerdan descartar 4–6:
- 5 y 6 (enlace degradado) no separan B2 de S5G, porque el sensing usa el
  mismo enlace;
- 4 (caída del líder) es evidente por diseño y va al modelo de escalado.

| Prueba | A recibe de B | B recibe de A | Detalle |
|---|---|---|---|
| c1_b0 (B0, 3 min) | 0 % | 0 % | 0 de 36 000 bursts |
| c2_b1 (B1, ±10 ppm, 6 min) | 86,8 % | 88,3 % | apagón de ~45 s (200–250 s); 4–5 pérdidas de seguimiento |
| c3_b2 (B2, 3 min) | 99,5 % | 99,3 % | el seguidor encuentra al líder en 0,03 s y transmite a los 0,34 s |

- **B1 en hardware coincide con el modelo.** La fase de A vista desde B deriva
  0,6 ms cada 30 s (20 ppm relativos), recorre el periodo de 5 ms y, cuando
  cae sobre el propio TX, la recepción se hunde (10 %, 0 % y 58 % en los
  tramos de 20 s de 200–260 s). La emulación predecía el 82,7 % en 520 s con
  un apagón de ~43 s; equivale a ~88 % en 360 s.
- **B2 tiene mejor precisión temporal que S5G.** En el líder, el desfase del
  seguidor tiene una desviación de 4,9 ns (ruido rápido de 1,8 ns, deriva
  lenta de 2,1 ns), frente a 8,9 ns de S5G en sala vacía (t11), y no se mueve
  con la persona. Era lo esperado: el WiFi a 100 MHz mide el tiempo mejor que
  el SSB de 3,6 MHz.
- **Consecuencia para el artículo.** Con dos AP que se oyen, B2 iguala o
  mejora a S5G en disponibilidad y precisión. Las ventajas de S5G hay que
  demostrarlas donde B2 no llega:
  - AP que no oyen al líder, o cadenas de seguidores con error acumulado;
  - escalado con N AP sin elegir líder ni negociar;
  - numeración absoluta común (SFN) sin acordar el horario por aire;
  - referencia de frecuencia común (§10.3);
  - independencia entre operadores.
  Propuesta: modelo y simulación de escalado (punto 5).
- Observado: muchos avisos `L` (TX tardío) en el script de baselines, hasta
  ~7500 en el líder de c3. Sin efecto visible en la recepción; pendiente de
  revisar.

### 10.4 S5G frente a B2 (seguidor WiFi): argumentos para el artículo (2026-10-14)

Es la parte delicada del artículo. B2 (un AP toma la hora de los bursts de otro
AP por WiFi, sin 5G) es el rival natural, y en el caso medido de dos AP que se
oyen bien iguala o mejora a S5G. Hay que reconocerlo y demostrar dónde gana
S5G. Abajo, cada argumento lleva su evidencia: «medido», «modelo» o «por
demostrar».

**A. Dónde B2 es mejor o igual que S5G (reconocerlo en el artículo).**

1. **Precisión temporal entre dos AP que se oyen (medido).** B2: 4,9 ns de
   desviación (ruido rápido de 1,8 ns, deriva lenta de 2,1 ns; c3_b2). S5G:
   8,9 ns en sala vacía (t11) y 14–41 ns con una persona en la zona (t9).
   - Causa física: B2 mide el tiempo con una señal de 100 MHz por un enlace
     directo corto; S5G con un SSB de 3,6 MHz que llega con multitrayecto
     desde una celda lejana.
   - El SSB, además, se perturba con la persona: el desfase se mueve de −39 a
     +6 ns en t9, y hasta −230 ns con la antena 5G de B mal colocada (t10).
2. **Disponibilidad con dos AP (medido).** B2 recibe el 99,3–99,5 %, como S5G.
3. **Enganche rápido (medido).** B2 encuentra al líder en 0,03 s y transmite a
   los 0,34 s. S5G necesita el seguimiento del PSS y varias decodificaciones
   PBCH (~1 s) más el calentamiento de CFO.
4. **No depende de una red ajena.** Sin cobertura 5G interior, cambios de
   celda, barrido de haces SSB, reconfiguraciones o caídas del operador, B2
   sigue funcionando.
5. **Hardware.** B2 no necesita una cadena RX a 3,5 GHz ni decodificar PBCH.
6. **Sensing igual.** Como el sensing se mide respecto al directo de cada AP,
   la mejor precisión temporal de B2 no se traduce en mejor localización
   (§10.2.1: igual que S5G cuando hay datos).

**B. Dónde S5G es mejor que B2 (lo que hay que demostrar con fuerza).**

1. **No hace falta que los AP se oigan (modelo; por demostrar en
   hardware).**
   - B2 exige que cada seguidor reciba al líder con buena SNR y en el mismo
     canal.
   - Con AP en salas o pisos distintos, o AP ocultos, B2 necesita cadenas de
     seguidores.
   - En S5G cada AP solo necesita cobertura de la celda (topología en
     estrella con el gNB).
   - Matiz: el par biestático que sensa sí debe oírse. Pero la
     sincronización de la red es global, y los pares de sensing son locales.
2. **El error no se acumula (modelo).**
   - En una cadena de B2 de k saltos, el ruido crece ~√k·4,9 ns.
   - Los sesgos de cada salto se suman linealmente: el retardo TX→aire→RX de
     cada enlace (1,16–1,51 µs medidos) solo se calibra si hay intercambio en
     los dos sentidos.
   - En S5G todos referencian la misma celda. Su sesgo es la diferencia de
     distancia al gNB, Δd/c (100 m son 333 ns): fijo y acotado. Para el
     artículo, decirlo y medirlo o acotarlo.
3. **Sin líder ni punto único de fallo (medible; por demostrar).**
   - Si el líder de B2 se apaga, cambia de canal o deja de sensar, sus
     seguidores se callan y hay que reelegir.
   - El gNB es infraestructura de operador, siempre encendida.
4. **El líder tiene que seguir transmitiendo aunque no sense (modelo).** En
   B2, las balizas del líder son el reloj de todos: si deja de sensar, debe
   seguir emitiendo y gastando tiempo de aire. En S5G el reloj está siempre
   gratis; las sesiones de sensing pueden empezar y parar bajo demanda
   (`--session-*`) sin coste de sincronización en reposo.
5. **Contención del canal WiFi: NO es un argumento diferenciador
   (revisado el 2026-10-14, 17:40).**
   - La idea inicial era que el LBT retrasa las balizas del líder de B2 y
     corrompe su seguimiento.
   - Pero un B2 bien diseñado no transmite un burst si el canal está ocupado
     en su instante (no lo retrasa): el seguidor ve un hueco y lo cubre con
     su ajuste lineal.
   - S5G pierde los mismos bursts. Ambos sufren igual y una prueba saldría
     empatada salvo que se implementase mal B2. Se descarta la prueba «B2 con
     contención».
   - Lo único defendible: con muchos huecos seguidos, el seguidor de B2 vive
     de su predicción, mientras que en S5G la referencia no depende del
     canal WiFi. Es un efecto de segundo orden.
6. **Funciona entre canales y bandas (argumento).**
   - B2 solo sincroniza AP en el mismo canal WiFi.
   - Con S5G, AP en canales distintos (o en 2,4/5/6 GHz) comparten tiempo y
     numeración. Es la base del sensing multibanda del proyecto.
7. **Numeración absoluta común sin acordarla (medido: SFN con 0
   discrepancias).**
   - Con el SFN todos los AP saben qué turno toca a quién con una regla fija
     (por ejemplo, por identificador); un AP nuevo se incorpora sin hablar
     con nadie.
   - B2 da tiempo relativo, pero el reparto de turnos hay que negociarlo por
     aire (como el Co-TDMA de 802.11bn, con su control).
8. **Referencia de frecuencia común (medido: t12, 381 → 15 Hz).**
   - Matiz: un seguidor de B2 también podría estimar el CFO del líder con sus
     bursts.
   - La ventaja de S5G es que todos los AP quedan referidos al mismo
     oscilador (el gNB) sin cadenas ni estimación, y la corrección se aplica
     antes de transmitir.
9. **Varios operadores y confianza (argumento).**
   - En B2, seguir la hora de un AP de otra red supone confiar en él: puede
     desviarla o suplantarla.
   - La celda es infraestructura regulada.
   - Matiz: el PBCH tampoco está autenticado. No sobrevender.
10. **Escalado del tiempo de aire (modelo).** S5G: cero tramas de control
    para cualquier N. B2: balizas del líder y negociación de turnos. B3:
    control en cada medida, que crece con N.

**C. Experimentos y modelos que faltan para defender B.**
- Modelo y simulación con N AP de 2 a 16 y topologías con AP ocultos:
  cadenas de B2 (ruido y sesgo por salto medidos), colisiones de B1, tiempo de
  aire de control de B3, efecto de la pérdida del líder.
- ~~B2 con contención~~: descartado (ver B.5).
- Acotar el sesgo de S5G por distancia al gNB (geometría de la celda o dos
  ubicaciones).
- Demostración opcional de canales distintos: A y B en canales WiFi
  diferentes. B2 es imposible; S5G mantiene la numeración (útil para
  multibanda).

### 10.5 Siguientes pasos posibles (2026-10-14, para decidir con el usuario)

| # | Qué | Para qué | Esfuerzo | Laboratorio |
|---|---|---|---|---|
| 1 | ~~Modelo de escalado con N AP y AP ocultos (§10.4.C)~~ | hecho el 2026-10-15 (§10.7) | — | — |
| 2 | ~~B2 con contención emulada~~ | descartado: no separa B2 de S5G (§10.4 B.5) | — | — |
| 3 | Integrar `--freq-sync-5g` en las pruebas de sensing y medir el beneficio (integración coherente, suelo Doppler, baldosas lejanas) | convertir t12 en ganancia de sensing | ½ día + 1 prueba | sí |
| 4 | Pruebas largas de S5G (30–60 min): estabilidad, numeración y eventos de celda | lo pedirá un revisor | ½ día | sí |
| 5 | Reproducibilidad: repetir baldosas otro día o en otra geometría/sala | evitar «un solo día, una sala» | 1 día | sí |
| 6 | Seguimiento de una persona caminando con ventanas de 0,5 s y trayectoria conocida (cinta en el suelo) | demostración dinámica | ½ día + 1 prueba | sí |
| 7 | Tiempo real: localización en línea | demo y segundo artículo; no imprescindible | 2–4 días | sí |
| 8 | Dos personas (resolución) | capacidad de sensing; mejor para otro artículo | 2–3 días | sí |
| 9 | NTP apagado y relojes de host desajustados | cerrar «sin backhaul ni NTP» | 1 h | sí |
| 10 | Waveform HE NDP-like frente a ZC | compatibilidad con estándar (revisor) | 2–3 días | sí |
| 11 | Más de dos AP en hardware (si hay un tercer X410 o un B210) | escalado real | depende del hardware | sí |
| 12 | Arreglar avisos `L` / GC del script de baselines y limpiar el código | calidad del artefacto | ½ día | no |
| 13 | Repositorio y dataset publicables (código saneado, datos t9–t12) | disponibilidad de datos (revistas Q1) | 1–2 días | no |
| 14 | Medir el sesgo por distancia al gNB y comprobar otra celda u operador | generalidad de S5G | ½ día | sí |

### 10.6 Sesión de laboratorio S5G + CFO 5G (preparada el 2026-10-14, 17:42)

Pasos 3–6 de §10.5, mientras el asistente prepara el modelo de escalado.
Todo con `x410_two_ap_sense.py --nr-workers 4 --freq-sync-5g`.

| Id | Hora | Qué | Para qué |
|---|---|---|---|
| t13_tiles_fs | 17:47:00, 520 s | mismo protocolo de baldosas que t9/t10 | reproducibilidad en otra sesión, con la corrección de frecuencia integrada |
| t14_track | 17:59:00, 190 s | recorrido en bucle de 12 baldosas, una cada 2 s, 6 vueltas (20–164 s) | seguimiento dinámico con verdad temporizada |
| t15_long | 18:06:00, 1800 s | 30 min con la sala en uso normal | estabilidad de sincronización, numeración y CFO a largo plazo |

Recorrido de t14: (1,2) → (1,3) → (1,4) → (1,5) → (2,5) → (3,5) → (4,5) →
(4,4) → (4,3) → (3,3) → (2,3) → (2,2) → (1,2)…

### 10.6.1 Resultados de t13 y t14 (2026-10-14, 17:47 y 18:05)

**Funcionamiento.**
- Corrección de frecuencia aplicada en los dos AP: A +801,6 Hz, B +1153,8 Hz.
- Turnos perdidos ~0,2 %, numeración SFN con 0 discrepancias y 0–3 bloques
  5G descartados.

**t13 (baldosas, otra sesión, con `--freq-sync-5g`).**
- Rechazo trasero: delante +24–26 dB; detrás, en el suelo de ruido. Mejor que
  en t9/t10.
- Distancia biestática A→B: correlación de 0,95 y error mediano de 0,29 m.
  Reproduce el resultado.
- **Localización con los parámetros fijados (3,36 cm, A→B): mediana de
  0,60 m (no reproduce).** Con los dos sentidos, 0,52 m; con 3,0 cm, 0,36 m.
- **Diagnóstico:**
  - No es la corrección de frecuencia: las fases entre cadenas del X410 son
    estables en todas las pruebas, con y sin ella (máximo de 1–8° en medias
    de 10 s, comparable a t10).
  - Lo que cambia es la calibración angular. Calibrando la escala con las 3
    primeras baldosas de cada sesión:

    | Sesión | k (A / B) | Error en las 13 restantes |
    |---|---|---|
    | t9 | 1,12 / 1,12 | 0,10 m |
    | t10 | 1,14 / 1,08 | 0,11 m |
    | t13 | 0,92 / 0,84 | 0,30 m A→B; 0,21 m con los dos sentidos |

  - Una k < 1 no es física con la separación medida (3,33 cm, k = 1,11).
    Algo cambió en el montaje o en la calibración entre las 14:00 y las 17:47:
    un array desplazado o girado, un cable o antena de la parte WiFi tocado al
    reajustar la antena 5G de B, u objetos nuevos cerca de los arrays. Hay que
    revisarlo físicamente.
  - El giro estimado con una sola baldosa no es fiable: en t9/t10 también
    «encuentra» unos 15° y empeora el resultado.
- **Lección para el artículo:**
  - la calibración angular con el compañero no basta entre sesiones;
  - hace falta una calibración corta por sesión (por ejemplo, 3 baldosas, ~1
    min) o una calibración física de los arrays;
  - tiempo, numeración, CFO, distancia y rechazo trasero sí se reproducen.

**t14 (recorrido caminando, ventanas de 0,5 s, calibración de t13).**
- Error mediano de 0,78 m con el horario del avisador.
- Desplazando la verdad 2–3 s, baja a **0,31 m**: la persona llegaba a cada
  baldosa ~1 paso después del pitido. El desfase está ajustado a posteriori.
- Por paso, las estimaciones siguen el bucle con una dispersión de 0,1–0,6 m.
- Pendiente de repetir con la geometría revisada y con un registro de verdad
  mejor (por ejemplo, vídeo).
- Figura de presentación: `figures/t14_trayectoria.png` en la copia de
  seguridad. Con la estimación suavizada (mediana de 1,25 s) y la verdad
  desplazada 2,5 s: error mediano de 0,25 m, 63 % a ≤ 0,3 m y 96 % a ≤ 0,6 m.
  El mapa muestra un sesgo hacia fuera (hacia los bordes), coherente con el
  problema de calibración de esa sesión.
- **Procesado mejorado** (`better.py`, `fig2.py`; figura `t14_trayectoria_v2.png`,
  añadida al dashboard en la sección «Persona caminando»):
  - calibración de escala y giro por AP con las 16 baldosas de t13 (misma
    sesión, prueba distinta);
  - filtro de Kalman de velocidad constante, causal y con puerta de 4σ.

  | Variante | Error mediano | ≤ 0,3 m | ≤ 0,6 m |
  |---|---|---|---|
  | Ventana cruda de 0,5 s | 0,18 m | 80 % | — |
  | Kalman causal | **0,16 m** | **84 %** | **98 %** |
  | Kalman + RTS (offline) | 0,14 m | 95 % | — |

  Verdad desplazada 2,5 s. Escala y giro se compensan entre sí: los
  parámetros calibrados no son físicamente únicos (giros de +12/−15° o
  +18/−27° dan resultados parecidos).
- Versiones de la figura en `figures/`:
  - v1, mediana de 1,25 s y calibración de 3 baldosas: 0,25 m;
  - v2, Kalman causal: 0,16 m, ruidosa;
  - v3, Kalman + RTS con σ = 0,4 y q = 0,3: 0,13 m y 96 % a ≤ 0,3 m;
    demasiado suave, «parece falsa»;
  - **v4, Kalman + RTS ligero con σ = 0,25 y q = 1,5, con los puntos crudos
    de cada ventana de 0,5 s: 0,15 m y 93 % a ≤ 0,3 m**. Es la elegida para
    enseñar. El dashboard muestra la v4 (por defecto), la v3 y la versión
    sin Kalman (mediana de 1,25 s con la calibración de 16 baldosas: 0,16 m,
    90 % a ≤ 0,3 m, 100 % a ≤ 0,6 m; `t14_trayectoria_mediana.png`) con un
    selector. La cruda sin ningún filtro (0,18 m, 80 %;
    `t14_trayectoria_cruda.png`) queda solo en las figuras. La v1 parecía
    limpia por la mediana y porque el mapa de búsqueda acababa en el borde de
    la zona (estimaciones recortadas a x = 0).

### 10.6.2 Pendiente para el 2026-10-15: por qué t13/t14 localizan peor

El usuario comprobó los arrays antes y después y parecían intactos. Hipótesis,
por orden de lo fácil que es descartarlas:

1. **`--freq-sync-5g`.** Solo t12, t13 y t14 lo usan, y t12 no tiene verdad.
   Las fases entre cadenas son estables dentro de cada prueba, pero la
   resintonía podría cambiar algo que la calibración con el compañero no
   absorbe.
   - Prueba decisiva: las mismas 4–6 baldosas dos veces seguidas, con y sin
     la opción.
   - Análisis previo sin laboratorio: comparar los vectores de calibración
     estática (fases de `g_rx`/`g_tx`) de t9–t14 y ver si t12–t14 se separan
     del resto.
2. **Cambios del entorno cerca de los arrays** (sillas, portátiles, la chapa,
   gente del laboratorio). Cambian el multitrayecto estático que entra en la
   calibración con el compañero.
3. **Calentamiento del X410 tras horas de pruebas** (de 17:06 a 18:10 sin
   parar).
4. **Cable o antena WiFi de B** tocado al reajustar la antena 5G a las 13:55.
   t11 (14:00) no tiene verdad para comprobarlo.

Plan del 15-10:
- Primero, el análisis de los vectores de calibración (PC5, sin laboratorio).
- Después, la prueba A/B corta con y sin `--freq-sync-5g`.
- Después, t15 (30 min).
- Por último, repetir t14 con vídeo como verdad.

### 10.6.3 Diagnóstico de t13/t14 (2026-10-15): cambio físico en las antenas, no el CFO

- Las fases de calibración cambian en cada arranque del X410 y no sirven
  para comparar. Las amplitudes relativas de cada elemento (vector de rango 1
  del canal estático) son una huella del montaje.
- La huella es estable en t9, t10, t11 y **t12 (con `--freq-sync-5g`)**, y
  cambia en t13/t14 en los dos arrays:
  - RX de B: de [0,72–0,85, 0,84–0,89, 1] a [0,51, 0,53, 1];
  - TX de B: de [0,63–0,76, 0,67–0,70, 1, 0,90–0,96] a [0,26–0,30, 0,26, 1,
    0,58–0,60];
  - RX de A: de [0,71–0,73, 0,67–0,72, 1] a [0,54, 1, 0,93];
  - TX de A: de [0,87–0,92, 0,69–0,78, 0,60–0,65, 1] a [0,57, 0,88, 1, 0,84].
- **Conclusión:** la corrección de frecuencia queda descartada. Algo físico
  cambió en las antenas entre las 14:40 y las 17:47. El 15-10 el usuario
  encuentra una chapa inclinada tocando las antenas, lo que encaja.
- Plan del 15-10:
  - arreglar las chapas (~1–2 cm, sin contacto);
  - comprobación de 2 min de la huella;
  - t16, baldosas con `--freq-sync-5g`, análisis fijado de antemano: 3,36 cm,
    ambos sentidos, criterio ≤ 0,2 m;
  - t17, recorrido con vídeo como verdad;
  - t18, 30 min.
- Pasos posteriores (añadido el 15-10): vigilar la huella al empezar cada
  sesión (prueba de 1 min) y antes de grabar.

### 10.6.4 t15c y t16 (2026-10-15): chapas arregladas, reproducido otro día

- **t15c_check (11:17, 2 min, vacía):** la huella de amplitudes vuelve a la
  de t9–t12. B prácticamente igual; A con diferencias del 10–25 % en algún
  elemento, sin la deformación de t13.
- **t16_tiles (11:37, protocolo de t9, `--freq-sync-5g`).** Análisis fijado de
  antemano: 3,36 cm, ambos sentidos, criterio ≤ 0,2 m.

| | t9 | t10 | t13 (chapa tocando) | **t16** |
|---|---|---|---|---|
| Localización A→B, 3,36 cm | 0,10 m | 0,15 m (fijado) | 0,60 m | **0,16 m** (máx. 0,30; 15/16 a ≤ 0,3 m) |
| Localización ambos sentidos, 3,36 cm | 0,11 m | 0,09 m | 0,52 m | **0,16 m (fijado; criterio cumplido)**, máx. 2,24 m en (1,4) |
| k calibrado con 3 baldosas (A / B) | 1,12 / 1,12 | 1,14 / 1,08 | 0,92 / 0,84 | 1,10 / 1,06 |
| Ventanas de 0,5 s (A→B / ambos) | 0,15 / 0,14 m | 0,16 / 0,11 m | — | 0,18 / 0,16 m |
| Distancia biestática (correlación, error) | 0,95; 0,20 m | 0,95; 0,14 m | 0,95; 0,29 m | 0,94; 0,19 m |
| Delante / detrás sobre el vacío | +20–23 / +0,1–0,5 dB | +22–24 / ≈0 dB | +24–26 / +0,1 dB | +15–24 / +0,1–0,2 dB |
| Sincronización vacía (desviación; lenta) | 8,5–10; 6,5–8,5 ns | 10–10,4; 7,7–7,9 ns | 7,3–8,0; 5,2–6,0 ns | **6,0–8,2; 3,2–5,5 ns** |
| Sincronización con persona (mediana por baldosa) | −39 a +6 ns | −230 a −3 ns (5G de B degradado) | — | **−14 a +20 ns**, dispersión 8–14 ns |
| CFO entre AP | ~350–380 Hz | ~350–380 Hz | ~15 Hz (fs) | **21 / 5 Hz (fs)** |
| Turnos perdidos | ~0,2–5 % | 0,3–7,6 % | ~0,2 % | **~0,2 %** |

- **Conclusión:** t16 reproduce t10 otro día con la corrección de frecuencia
  integrada. La causa de t13 era la chapa.
- **Matiz:** en t16 el suelo Doppler de B→A (recepción en A) está más alto
  (−13 dB frente a −18 a −21), así que B→A falla en (1,4) y arrastra la
  combinación.
- **Propuesta (no aplicada):** ponderar cada sentido por su SNR Doppler al
  combinar, en lugar de sumar los mapas normalizados.

### 10.7 Modelo de escalado con N AP (2026-10-15)

Script `scaling_model.py`, resultados `scaling_results.json` y figura
`scaling_model.png`: copia en PC5 en
`~/sync_backups/x410_two_ap_results_20261014/analysis/scaling/`.
Las entradas son los mejores valores medidos en el banco, igual que los
resultados publicados de 802.11bf suelen ser las mejores ejecuciones:

| Entrada | Valor | Origen |
|---|---|---|
| Burst de sensing | 0,4 ms (8 ZC de 50 µs) y 50 µs de guarda | configuración t9–t16 |
| Periodo | 5 ms (200 Hz por sentido) | configuración |
| Ruido de sincronización S5G | σ = 6,0 ns | t16 |
| Error de un salto en B2 | σ = 4,9 ns | c3 |
| Dispersión de latencia sin calibrar | ±175 ns por salto | c3, primer enganche |
| Primer TX de B2 tras enganchar | 0,34 s | c3 |
| Fases ciegas de B1 | 18 % con 2 AP | c1/c2 |
| MAC 802.11 | SIFS 16 µs, DIFS 34 µs, slot 9 µs, CWmin 15, preámbulo legacy 20 µs, control a 6 Mbps | estándar |
| Propagación | TGax enterprise, 7 dB por pared, salas de 10 m, planta de 60 × 30 m | modelo |

El modelo de 802.11bf TB (B3) cuenta por iniciador: acceso al canal, trigger
de sondeo, CTS, NDPA y NDP. Con informes, añade el trigger de informe y
informes de 200 B.

#### 10.7.1 Resultados

| Ventaja (§10.4) | Resultado del modelo | Fuerza |
|---|---|---|
| Overhead de control nulo, más tiempo para comunicaciones | Con 2 AP a 200 Hz, el tiempo libre para comunicaciones es 80 % con S5G, 72 % con B3 y 56 % con B3 más informes. Con 4 AP es 60 %, 42 % y 0 %. B3 añade 301–488 µs por instancia (N = 2–16) | fuerte |
| Más AP con la misma tasa | AP máximos con el canal al 100 %: a 200 Hz, 10 (S5G) / 6 (B3) / 3 (B3 con informes); a 100 Hz, 20/11/4; a 50 Hz, 40/20/7 | fuerte |
| Sin coordinación hay colisiones (B1) | Bursts perdidos: 18 % con N = 2, 55 % con N = 5, 75 % con N = 8, 86 % con N = 11. S5G: 0 hasta 10 AP a 200 Hz | fuerte (N = 2 medido) |
| Sin negociación | S5G: 0 tramas. B2/802.11bn: 4·N(N−1)/2 tramas al montar la red. B3: 600·N tramas/s a 200 Hz | fuerte |
| Sin líder | B2 pierde la sincronización ≥ 0,54 s por cada caída del líder (0,2 s para detectarla y 0,34 s para reenganchar y transmitir). S5G no tiene líder | fuerte (entradas medidas; la caída no se ha provocado en hardware) |
| Sin tiempos muertos | B2 dedica un 8 % del aire a beacons de sincronización a 200 Hz. S5G no gasta aire | fuerte |
| Saltos de sincronización | p95 de B2 calibrado: 9,4 ns (1 salto), 13,5 ns (2), 27 ns (8). Sin calibrar: 167 → 561 ns. S5G: 11,8 ns, sin depender de N. Con −82 dBm hacen falta ~2 saltos de media | moderada: con calibración en los dos sentidos B2 apenas empeora, y todo queda muy por debajo de guardas de µs |
| AP que no se oyen | Con decodificación estándar (−82 dBm) hay AP que se interfieren (≥ −93 dBm) sin poder coordinarse en un 33 % de los despliegues con N = 4, 14 % con N = 8, 5 % con N = 12, 1,3 % con N = 16 y 0 con N ≥ 24. Con detección por correlación (−95 dBm): 0 en todos los casos | condicional: solo despliegues dispersos; los grupos B2 que no se oyen tampoco suelen molestarse |
| Sesgo propio de S5G | La distinta distancia al gNB da un error fijo acotado por la separación entre AP (3,3 ns/m). Mediana/p95: 19/43 ns para pares a < 15 m (enlaces de sensing típicos), 35/90 ns para pares que se interfieren (≥ −93 dBm) y 42/130 ns para todos los pares de la planta (v2, 2026-10-19). Se calibra una vez en los enlaces de sensing; en pares que solo se interfieren debe caber en la guarda | desventaja honesta de S5G; se mide en el punto 14 de §10.5 |
| Redes y canales distintos, varios operadores, referencia de frecuencia común | El modelo no los cuantifica; S5G no necesita que los AP compartan canal ni red, y B2 sí | cualitativa |
| Encaje con ETSI FBE (equipo con periodo fijo y CCA de 9 µs) | No modelado | por verificar en EN 301 893 |

Corrección respecto a §10.4: el argumento de los AP ocultos es más débil de lo
que se planteó. Dos grupos B2 que no se oyen casi nunca se interfieren. El caso
real es el de AP que se molestan sin oírse lo bastante para decodificar, y solo
pesa en despliegues dispersos con decodificación estándar.

Dashboard del escalado (todas las cifras, comparativa por ventaja, filtros de forma de onda y tasa): https://claude.ai/artifact/AZVs282YnCrfofg2CWvUa8.

**v2 (2026-10-19).** Sesgo del gNB condicionado a los pares relevantes y casos ampliados (NDP HE de 80 µs, tasas de 10–200 Hz, MAPC 802.11bn estimado, guarda S5G reducida a 1 µs). v1 conservada como `*_v1.*` en la copia.

**v3 (2026-10-19, comparación justa; `airtime_v3.py`).**
- **Mismo sondeo en todos los esquemas:** NDP HE de 80 µs (referencia) y burst ZC de 0,4 ms (secundario).
- **S5G con G = 3 µs** (Fase 0, §10.8).
- **802.11bf TB por AP:** control a 24 Mb/s, resultados por el backhaul; variante con informes CSI por aire.
- **Cota del ideal negociado:** una TXOP por periodo para todos los AP.

| NDP HE | S5G 3 µs | S5G 50 µs | Ideal negociado | TB | TB + informes |
|---|---|---|---|---|---|
| Canal ocupado, 100 Hz, 8 AP | 6,9 % | 14,4 % | 10,2 % | 26,6 % | 110 % |
| AP máximos a 200 Hz | 58 | 27 | 48 | 14 | 5 |

- Control de TB por medida: 232–279 µs (N = 2–16).
- B1 con NDP HE: 5 % (N = 2), 19 % (5), 41 % (11).
- Con la guarda de 50 µs, S5G quedaría por detrás del ideal negociado: la guarda pequeña es necesaria para ganar también en eficiencia.

#### 10.7.2 Qué queda demostrado y qué falta para el artículo

| Afirmación | Con datos (2 AP) | Con el modelo | Qué falta |
|---|---|---|---|
| Sincronización OTA sin backhaul, de ns | sí: σ 6,0 ns (t16) y 0,6 % de turnos perdidos | no aplica | prueba larga de 30–60 min (§10.5 punto 4) |
| Numeración común por SFN | sí (§5.1) | no aplica | — |
| Referencia de frecuencia común | sí: CFO de 381 a 15 Hz (t12) | no | medir la ganancia de sensing (§10.5 punto 3) |
| Sensing a ~10–16 cm | sí (t9, t10, t16) | no aplica | caminata con ground truth en vídeo (t17) |
| Sin coordinación se pierden turnos (B1) | sí, con N = 2: 18 % y apagones de ~45 s | sí, crece con N | — |
| B2 iguala a S5G con 2 AP que se oyen | sí: 4,9 ns (c3) | sí | — (se reconoce en §10.4 A) |
| Más aire para comunicaciones y más AP | no: el banco no lleva tráfico ni un 802.11bf real | sí | referencias publicadas del overhead de 802.11bf/bn; opcional: tráfico iperf durante el sensing |
| Sin negociación ni tramas de control | sí, por construcción y en los logs | sí, crece con N | — |
| Sin líder | no | sí | provocar la caída del líder en B2 en hardware (corto) |
| Sin tiempos muertos | no | sí | — |
| Saltos de sincronización | un salto (c3) | sí | un tercer AP en hardware (§10.5 punto 11) |
| AP que no se oyen | no | condicional | prueba con atenuación o paredes: B2 no engancha y S5G sí |
| Redes o canales distintos | no | no | AP B en otro canal WiFi o con otra red; medir el desfase con un receptor de referencia |
| Otra celda u operador; sesgo por distancia al gNB | no | sesgo estimado (p95 43 ns a < 15 m; 90 ns en pares que se interfieren) | §10.5 punto 14 |
| Encaje regulatorio (FBE) | no | no | revisar EN 301 893 |
| Más de 2 AP en hardware | no | sí | tercer X410 o B210 |

### 10.8 Guarda pequeña: Fase 0 con los datos existentes (2026-10-19)

Script `analysis/guard/phase0.py` y resultados `guard_phase0.json` en la copia de PC5.
Se mide el desfase de cada burst recibido respecto a su llegada esperada (después de
centrar en la latencia adquirida), solo con detecciones válidas (calidad ≥ 20; las de
calidad ≈ 1,3 son ventanas sin burst porque el compañero no transmitió).

| Pruebas | Bursts válidos | Desviación máxima | p99,9 | Fuera de 0,1 µs | Fuera de 0,5 µs |
|---|---|---|---|---|---|
| t11–t16 (S5G, 5G bien colocado; 0,47 h) | 680 170 | 70 ns | 64 ns | 0 | 0 |
| t10 (5G de B degradado) | 194 001 | 453 ns (salto lento de 485 ns) | 412 ns | 35 658 | 0 |

- Con 5G en buenas condiciones habría bastado una tolerancia de **0,1 µs**; con el 5G
  degradado de t10, de **0,5 µs**.
- La extracción ya busca el burst en **±500 ns** alrededor de la llegada esperada
  (`--search-ns 500`): la tolerancia efectiva del receptor en todas las pruebas ha sido
  de 0,5 µs y no ha hecho falta más. La ventana de captura de ±50 µs no se usaba.
- **Limitación del script:** la ventana RX se centra en el instante de TX, no en el de
  llegada (latencia ≈ 1,36 µs). La guarda mínima que admite es de unos 2,1 µs. En
  simulación, `--guard-us 1` pierde el 100 % de los bursts y `--guard-us 2,5` ninguno.
  Para probar guardas de 1 µs o menos hay que centrar la ventana en la llegada
  (cambio pequeño, pendiente de autorizar).
- **No probado:** turnos pegados (separación 2G) entre AP; requiere ese cambio y offsets
  juntos (Fase 1).
- Nota para el artículo: t16 usó `--lookahead-ssb 7 --min-margin-ms 8`, no 6 y 15.

### 10.9 Sondeo corto: emulación con 1 y 2 pulsos (2026-10-21)

Mismo análisis fijado de t10 y t16 (3,36 cm, regla de los dos sentidos), usando solo los
primeros pulsos de cada burst. 8 pulsos reproduce los resultados publicados. Script
`analysis/pulse_emu/emu.py`, resultados en `results.jsonl`.

| Prueba | Pulsos (duración) | Mediana A→B / B→A / ambos | Máx. ambos | ≤ 0,3 m (ambos) | Suelo Doppler A→B / B→A | Movimiento sobre el suelo |
|---|---|---|---|---|---|---|
| t10 | 8 (400 µs) | 0,15 / 0,09 / 0,09 m | 0,16 m | 16/16 | −19,8 / −21,0 dB | 9,8 / 10,5 dB |
| t10 | 2 (100 µs) | 0,15 / 0,09 / 0,09 m | 0,22 m | 16/16 | −18,3 / −19,4 dB | 8,4 / 8,9 dB |
| t10 | 1 (50 µs) | 0,15 / 0,09 / 0,09 m | 0,25 m | 16/16 | −16,5 / −17,6 dB | 6,9 / 7,4 dB |
| t16 | 8 (400 µs) | 0,16 / 0,18 / 0,16 m | 2,24 m | 13/16 | −19,5 / −13,3 dB | 9,6 / 6,8 dB |
| t16 | 2 (100 µs) | 0,16 / 0,18 / 0,16 m | 2,24 m | 14/16 | −17,3 / −12,8 dB | 7,7 / 6,5 dB |
| t16 | 1 (50 µs) | 0,16 / 0,18 / 0,16 m | 2,60 m | 13/16 | −15,5 / −12,1 dB | 6,2 / 6,0 dB |

- **La localización no cambia**: medianas idénticas con 400, 100 y 50 µs; solo el máximo
  sube algo con 1 pulso.
- **El suelo Doppler sube 1,5–4 dB** y el contraste del movimiento baja de unos 10 a 6–7 dB
  con 50 µs, menos que los 9 dB teóricos (el suelo no es solo ruido térmico).
- Un sondeo de 80 µs (NDP HE) queda entre 1 y 2 pulsos: se puede afirmar el mismo
  rendimiento de localización con la duración usada en las comparativas.
- El script admite `--n-pulses 2` (100 µs): simulación con guarda de 3 µs, 2000/2000 bursts.

### 10.10 t18_zc80_g3: ZC de 80 µs con guarda de 3 µs en hardware (2026-10-21, 10:39)

Sala vacía, 3 min, mismos parámetros que t16 salvo `--n-pulses 2 --pulse-us 40
--pulse-spacing-us 40 --zc-len 4909 --guard-us 3`. Comparado con t11 (sala vacía,
burst de 400 µs, guarda de 50 µs):

| | t18 (ZC 80 µs, G = 3 µs) | t11 (ZC 400 µs, G = 50 µs) |
|---|---|---|
| Fallos de extracción (`rx_fail`) | 0 (A) | 30 / 61 |
| Desfase por burst: σ / p99,9 / máximo | 5,6 / 17,9 / 20,5 ns | 8,9 / 33 / 42 ns |
| Bursts a más de 0,1 µs de lo esperado | 0 | 0 |
| Desfase entre AP (dos sentidos), σ | 5,6 ns | 8,9 ns |
| Pico/suelo de detección | 600–650 | 900–1050 |
| Suelo Doppler A→B / B→A | −16,5 / −15,6 dB | −19,4 / −20,8 dB |
| Numeración SFN | 765 revalidaciones, 0 discrepancias | — |

- **La guarda de 3 µs se sostiene en hardware:** ningún burst fuera de ±0,1 µs, 0 fallos de
  extracción y sincronización mejor que en t11.
- **El ZC de 80 µs se sostiene:** detección holgada (unos 28 dB) y suelo Doppler unos 3–5 dB
  más alto, como predecía la emulación de §10.9.
- **Incidencia:** un lanzamiento duplicado de B (sin transmitir, con la hora ya pasada)
  sobrescribió en B `summary.json`, `tx_log.jsonl`, `nr_events.jsonl` y el log. Las
  medidas de B (`sense_rx.npz`) y todo lo de A están intactos. Copia en `runs/t18_zc80_g3_*`.

### 10.3 Sincronización en frecuencia con el 5G (`t12_fsync`, preparada el 2026-10-14)

- **Hallazgo:** hasta ahora el 5G no corregía la fase ni la frecuencia WiFi.
  El CFO entre A y B (~350–380 Hz a 5,18 GHz) se estima y elimina en cada
  burst con los propios pulsos.
- Sin embargo, el ppm que cada AP mide contra la celda con el tracker PSS
  predice ese CFO:

  | | Medido A→B | Predicho | Residuo con ppm promediado 10 s |
  |---|---|---|---|
  | t11 | 381 Hz (±6) | 365 Hz | ±7 Hz |
  | t9 | 349 Hz | 334 Hz | ±12 Hz |
  | t10 | — | — | ±38 Hz (5G de B degradado) |

  Queda un sesgo fijo de ~15 Hz, probablemente por diferencias entre los
  sintetizadores TX y RX: A→B mide 381 Hz y B→A −351 Hz.
- **Implementación** (`--freq-sync-5g`, desplegada el 10-14 a las 14:32;
  copias `*_pre_fs.py`):
  - tras el enganche, cada AP promedia 10 s (`--freq-sync-avg-s`) el ppm del
    tracker y, antes de transmitir, resintoniza todos sus canales WiFi a
    f·(1 − ppm·10⁻⁶);
  - cada AP usa solo su propia medida, sin intercambio;
  - se registra en `summary.json` → `results.freq_sync`.
  - Validado en simulación.
- **Prueba t12_fsync:** 3 min a 122,88 Msps con `--nr-workers 4`; 0–60 s sala
  vacía y 60–180 s una persona caminando por la zona.
- **Éxito:** el CFO A↔B medido en cada burst baja de ~380 Hz a unas decenas de
  Hz.
- **Después:** medir la ganancia de integración coherente entre bursts (más
  margen en baldosas lejanas) frente a t11.

**Resultado de `t12_fsync` (2026-10-14, 14:37:00, lanzado por el usuario): FUNCIONA.**

| | t11 (sin corrección) | t12 (`--freq-sync-5g`) |
|---|---|---|
| Corrección aplicada | — | A: −0,1558 ppm → +807,3 Hz; B: −0,2260 ppm → +1170,8 Hz; error de sintonía de 0,00 Hz y LO enganchado |
| CFO por burst A→B | 381,4 Hz (±5,9) | **15,2 Hz** (±5,9) |
| CFO por burst B→A | −351,3 Hz | **15,8 Hz** |
| Estabilidad (medianas por minuto) | 380,7–381,8 Hz | 14,8–15,6 Hz |

- El CFO entre AP baja unas 25 veces, a ~15 Hz (0,003 ppm), sin ningún
  intercambio entre AP. El resto es un sesgo constante, igual en los dos
  sentidos, que se puede calibrar una vez.
- Turnos perdidos: 0,6 %, como en t11.
- Hubo parones de hasta 215 ms, el de la resintonía (antes de transmitir).
- Pendiente: cuantificar qué gana el sensing con esto. Tal como está, la cadena
  ya alinea la fase en cada burst, así que la ventaja directa es no tener que
  estimar el CFO, una referencia de frecuencia común para N AP y la
  posibilidad de procesar de forma coherente entre bursts.

## 11. Inventario de ejecuciones

En `~/syncSense/testmultiband/` de cada servidor; en las ejecuciones de dos AP,
sufijo `_A` en `pc3pc4` y `_B` en `x410srv`. Fechas = fecha del directorio. No
se versionan.

| Carpeta | Fecha | Qué es |
|---|---|---|
| `x410_2x2_5g_20260924_*` | 09-24 | smoke 2×2 tono + 5G en un X410 (solo `pc3pc4`) |
| `run2_nrgain32_antenas_movidas`, `run3_txgain20`, `run4_txgain30` | 09-24 | ajustes de ganancia y antenas del smoke 2×2 (solo `pc3pc4`) |
| `pss_run1`, `pss_run2` | 09-24 | burst anclado al PSS en un X410, 2×1 (solo `pc3pc4`) |
| `pss_4x3_run1` | 10-06 | 4×3 + 5G en un X410 (solo `pc3pc4`) |
| `nr_check_apA` / `nr_check_apB` | 10-08 | comprobación 5G en cada AP |
| `two_ap_run1_*` | 10-08 | coordinación a 10 Hz, AP enfrentados |
| `two_ap_L_run1_*` | 10-08 | coordinación a 10 Hz, en L (base de la fase 0 de BF) |
| `sense_L_empty_*`, `sense_L_walk_*` | 10-08 | tanda 0: sensing a 2,45 GHz y 100 Hz |
| `scan_<host>_*` | 10-09 | barrido de espectro |
| `sense_L_cables_*` | 10-09 | tanda 1: cables fijados (el 1 Hz persiste) |
| `t2_check_*` | 10-09 | tanda 2, fallo: retardo supuesto de 4 µs, compañero no oído |
| `t2_check2_*` | 10-09 | tanda 2, SNR baja con ganancias 26/26; B perdió la RX tras un overflow |
| `t2_check3_*` | 10-09 | tanda 3, comprobación de 3 min válida (ganancias 40/40) |
| `t3_empty_*`, `t3_walk_*` | 10-09 | tanda 3, sala vacía y andando (andando de 35 a 95 s, de pie de 95 a 120 s) |
| `pbch_check_A/B` | 10-13 | solo RX, decodificación PBCH 60 s (sin ancla dispositivo↔host en esta versión) |
| `two_ap_sfn_*` | 10-13 | coordinación de 10 Hz a 5,18 GHz con numeración por SFN |
| `t4_tiles_*` | 10-13 | baldosas conocidas, nueva geometría con chapas (inicio 17:15:00); intentos interrumpidos `*_aborted`, `*_aborted2`, `*_aborted3` |
| `t5_empty122_*`, `t5_empty122ps50_*` | 10-13 | primeras pruebas transmitiendo a 122,88 Msps, sala vacía, con chapas |
| `t15c_check_*`, `t16_tiles_*` | 10-15 | comprobación de la huella tras arreglar las chapas (11:17) y baldosas con `--freq-sync-5g` (11:37) (§10.6.4) |
| `t13_tiles_fs_*`, `t14_track_*` | 10-14 | baldosas con `--freq-sync-5g` (17:47) y recorrido caminando (18:05) (§10.6.1) |
| `c1_b0_*`, `c2_b1_*`, `c3_b2_*` | 10-14 | campaña real de baselines B0/B1/B2 (§10.2.4); `c2_b1_*_crashed` es el intento fallido de las 17:12 |
| `t12_fsync_*` | 10-14 | 3 min con `--freq-sync-5g` (14:37:00): CFO entre AP de 381 → 15 Hz |
| `t11_nrw4_*` | 10-14 | 3 min, sala vacía, `--nr-workers 4`, antena 5G de B reajustada (14:00:30) |
| `t10_tiles_*` | 10-14 | repetición de t9 con el análisis fijado de antemano (inicio 13:40:00): CONFIRMADO |
| `t9_tiles_*` | 10-14 | 15 baldosas a 100 MHz, arrays a 0°, chapas pequeñas (inicio 11:38:00) |
| `t7_smallpanel_*` | 10-14 | 3 min, chapas pequeñas (switches), antenas 5G alejadas, sala vacía (inicio 11:15:00) |
| `t6_nopanel_*` | 10-14 | 3 min a 122,88 Msps, sala vacía, sin chapas (inicio 11:03:00); deriva lenta de 7,5 ns |
| `pbch_ref/` | 10-13 | scripts de MATLAB de referencia (`pbch_ref.m`, `ssb_wave.m`), `.mat` y prueba `e2e/` |
