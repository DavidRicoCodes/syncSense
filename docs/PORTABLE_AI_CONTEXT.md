# Contexto portátil completo del proyecto SYNC

## 1. Propósito de este documento

Este documento permite trasladar el proyecto a otra herramienta de IA, otra
máquina o una conversación sin historial. Resume el objetivo científico, la
arquitectura del framework, el estado real del software y del laboratorio, las
evidencias obtenidas y las decisiones pendientes. No sustituye a las fuentes de
verdad, pero evita reconstruir el contexto desde conversaciones anteriores.

Fecha del snapshot: **2026-09-24**.

Orden de lectura recomendado para una nueva IA:

1. `AGENTS.md`, si está presente en el workspace local.
2. `ProjectDescription.md`, descripción científica original que no debe
   reescribirse.
3. Este documento.
4. `docs/BF_MULTI_AP_SYNC_ARCHITECTURE.md`, para la dirección X410+B210, y
   `docs/BF_MULTI_AP_TESTBED_STATUS.md`, para lo medido en el banco de dos X410
   (posterior a este snapshot y con prioridad sobre él en ese tema).
5. `README.md` y `docs/REPOSITORY_MAP.md`, para operar el código existente.
6. `docs/EXPERIMENT_MATRIX.md` y `docs/DEVELOPMENT_PLAN.md`.
7. `.codex/HANDOFF.md`, `.codex/memory.md`, `.codex/context.md`,
   `.codex/decisions.md` y `.codex/proposals.md`, si se ha copiado también el
   contexto local no versionado.

Regla de interpretación: una capacidad descrita como objetivo o diseño no debe
presentarse como implementada. Las propuestas abiertas no son decisiones. Los
resultados de un smoke de integración no equivalen automáticamente a evidencia
científica.

## 2. Pregunta científica y alcance

El proyecto estudia cómo influye la sincronización temporal en sensing
multiestático y multibanda que combina observaciones 5G y WiFi. Las tareas
científicas inicialmente previstas son detección de presencia y localización
por baldosa; tracking y radar Doppler son extensiones activas de investigación,
no funcionalidades cerradas del primer framework.

Las hipótesis que motivan el proyecto son:

- Observaciones 5G y WiFi mejor alineadas temporalmente deberían representar
  con más fidelidad el mismo estado físico de la escena.
- Una referencia 5G OTA puede coordinar AP WiFi sin backhaul de coordinación y
  reducir negociación de turnos o quiet time.
- El beneficio debe medirse separando alineación temporal de adquisición,
  sincronización de frecuencia y coherencia de fase RF; son propiedades
  diferentes.
- Las mejoras del modelo deben compararse con baselines no sincronizados y no
  atribuirse automáticamente al hardware, a la geometría o a cambios de
  waveform.

Otro equipo desarrolla y entrena el modelo científico. PC5 debe alojar y
ejecutar la versión entregada para inferencia, registrar versión, configuración,
entradas, salidas y errores, pero este framework no absorbe ni inventa su lógica
ML. El adaptador `dummy` actual valida únicamente el recorrido técnico.

## 3. Cinco niveles que nunca deben confundirse

### 3.1 Reloj del host

`CLOCK_MONOTONIC`, `CLOCK_REALTIME`, NTP y PTP de Linux sirven para operación,
logs, supervisión y, con incertidumbre explícita, asociaciones aproximadas. No
son por sí solos el reloj de adquisición del USRP.

### 3.2 Frecuencia de referencia del USRP

Una referencia común de 10 MHz reduce deriva relativa de los osciladores de
muestreo y síntesis. No define por sí sola un origen temporal común.

### 3.3 Tiempo y epoch del USRP

PPS y la programación explícita del timekeeper permiten asignar un epoch y
ejecutar comandos temporizados. Compartir 10 MHz sin PPS/epoch no basta;
compartir PPS sin configurar el mismo significado del contador tampoco basta.

### 3.4 Timestamp exacto de trama

El `RXMetadata.time_spec` de UHD corresponde al primer sample de un bloque. El
timestamp canónico de una trama debe incorporar el offset del detector:

```text
frame_start_ticks = block_start_device_ticks + detector_offset_samples
tick_rate_hz      = sample_rate_hz
```

Las referencias canónicas actuales son `ssb_pss_start` para 5G y
`wifi_ppdu_start` para WiFi. Un timestamp tomado al escribir JSON o al terminar
el DSP es operacional, no un timestamp RF.

### 3.5 Coherencia de fase RF

Compartir tiempo, PPS o 10 MHz no garantiza que dos cadenas RF tengan una fase
de portadora absoluta determinista. Los canales X410 necesitan calibración de
amplitud/fase después de sintonizar; dos AP independientes conservan offset de
LO, CFO, phase noise y fase inicial desconocida. La arquitectura seleccionada
alinea slots y timestamps, no promete coherencia RF entre AP.

## 4. Repositorios y responsabilidades

### 4.1 Repositorio padre `SYNC`

Responsable de configuración, planificación, preflight, ejecución distribuida,
estado persistente, recuperación, almacenamiento NFS, validación, checksums,
manifiestos, catálogo, asociaciones derivadas e invocación de inferencia.

Snapshot local:

- Rama: `feat/he-ndp-like-20-40mhz`.
- Commit: `e861abafd77ea776e917287bb80784f9b17288f5`.
- Árbol: limpio antes de esta actualización documental.

### 4.2 Submódulo `modulos_rx_tx`

Contiene DSP y entrada/salida hardware para recepción 5G SSB, transmisión y
recepción WiFi, CSI, HE NDP-like, Doppler y utilidades históricas de modelos.

Snapshot local:

- Rama: `feat/he-ndp-like-20-40mhz`.
- Commit fijado: `2ac9ee9d561df5afad4a5c66148ce01b28ccd241`.
- Árbol: limpio.

No debe confundirse el código de entrenamiento o inferencia que existe en este
submódulo con el modelo externo definitivo que PC5 recibirá de otro equipo.

### 4.3 Submódulo `rx_sync`

Contiene prototipos X410 anteriores: captura multicanal con reloj común y
waveforms activas iniciales. Sigue siendo referencia experimental; no publica
el dataset multibanda completo del framework.

- Rama: `main`.
- Commit fijado: `274afed086ef635e9665cc40b7072c041112e42a`.
- Árbol: limpio.

### 4.4 Banco experimental `testmultiband`

En PC3PC4 existe además un directorio `testmultiband/` anidado en su workspace
local.

Es un checkout independiente del mismo origen histórico de `rx_sync`, con base
`274afed...`, pero sus scripts y resultados nuevos están sin versionar. No es
un tercer submódulo ni parte del producto reproducible. El padre ignora
`/testmultiband/` para evitar que ese checkout anidado bloquee runs o que se
suban capturas, direcciones y resultados por accidente.

Antes de promover algo desde este banco hay que sanear identidad hardware,
añadir tests, separar resultados y código, revisar licencias/autoría y decidir
si pertenece a `rx_sync`, `modulos_rx_tx` o a otro repositorio.

## 5. Roles históricos y topologías especiales

Los roles lógicos originales son:

| Nodo | Rol histórico |
|---|---|
| PC1 | TX 5G activo |
| PC2 | TX WiFi |
| PC3 | RX 5G |
| PC4 | RX WiFi |
| PC5 | Orquestador, NFS, datasets e inferencia externa |

Dos excepciones o despliegues posteriores deben quedar explícitos:

1. `nosync_passive` físico: PC1 RX 5G comercial, PC2 RX WiFi y PC3PC4
   TX WiFi N310. Solo redefine este perfil.
2. Arquitectura BF multi-AP seleccionada: dos AP físicos, cada uno compuesto
   por un B210 para 5G y un X410 para WiFi 4×4. La asignación final de hosts
   PC1-PC4 todavía no está cerrada y no debe inventarse.

## 6. Los seis experimentos originales

| Experimento | Idea | Estado real |
|---|---|---|
| `nosync_passive` | 5G comercial y WiFi controlado con RX separados | Implementado, validado y usado para dataset |
| `nosync_active` | Fuentes 5G/WiFi activas, RX separados | Componentes parciales; perfil científico pendiente |
| `sync_reception_passive` | RX 5G/WiFi en un dominio X410 común | Prototipo X410; integración procesada pendiente |
| `sync_reception_active` | Igual con fuentes activas | Pendiente de RX común y waveforms |
| `sync_all_passive` | Recepción y TX WiFi coordinados con referencia 5G | Concepto; scheduling real pendiente |
| `sync_all_active` | Coordinación total con señales activas | Concepto; componentes y validación pendientes |

La línea BF multi-AP referenciada a 5G da una realización concreta a la parte de
coordinación activa WiFi de `sync_all`, pero todavía no se ha decidido si será
un perfil de uno de esos seis casos o una familia complementaria. No se añade
silenciosamente un séptimo experimento ni se renombra uno existente.

## 7. Framework padre implementado

### 7.1 Tecnologías y contratos

- Python 3.12 en PC5, layout `src/`, `setuptools` y CLI `syncctl`.
- `argparse`, PyYAML y JSON Schema Draft 2020-12.
- Perfiles YAML e inventarios locales; secretos, seriales, IPs y rutas privadas
  permanecen fuera de Git.
- Estado JSON atómico con lock, revisión monotónica, historial y auditoría
  JSONL.
- Adaptadores de proceso local, fake y SSH; autorización separada para
  simulación remota, recepción hardware y transmisión RF.
- NFSv4 administrado explícitamente, no implícitamente.
- Checksums SHA-256, manifiestos de productor y `manifest.json` final con
  estado `COMPLETE`.
- Asociaciones e inferencias como derivados inmutables y reintentables.

### 7.2 Máquina de estados

```text
CREATED -> PREFLIGHT -> ARMED -> RUNNING -> FINALIZING -> COMPLETE
                    \-> FAILED/ABORTED
```

`COMPLETE`, `FAILED` y `ABORTED` son terminales. Un dataset fallido no contiene
manifiesto final. Un fallo de asociación o inferencia no degrada un raw dataset
ya publicado.

### 7.3 Orden de procesos

- Arranque receiver-first: todos los RX deben alcanzar readiness antes del TX.
- Parada transmitter-first: se detienen TX y después RX.
- En terminación normal se permite un drenaje acotado de RX.
- `SIGINT`/`SIGTERM`, timeouts, supervisor obsoleto y procesos que ignoran la
  señal se gestionan con recuperación y escalado controlado.

### 7.4 Layout de datos

```text
<storage-root>/runs/<run_id>/
├── .control/
│   ├── state.json
│   ├── transitions.jsonl
│   ├── plan.json
│   ├── catalog.json
│   └── logs/
├── <producer>/
│   ├── artefactos
│   └── producer-manifest.json
├── associations/<association_id>/
├── inference/<inference_id>/
└── manifest.json
```

La ruta canónica siempre es `runs/<run_id>`. El catálogo crea enlaces relativos
navegables:

```text
catalog/<testbed>/<experiment>/<condition>/<position>/<subject>/<run_id>
```

### 7.5 Comandos públicos principales

```text
syncctl storage bootstrap --dry-run|--apply
syncctl storage verify
syncctl storage teardown --apply
syncctl experiment plan PROFILE --param KEY=VALUE...
syncctl preflight PROFILE --param KEY=VALUE...
syncctl start RUN_ID
syncctl status RUN_ID
syncctl stop RUN_ID
syncctl finalize RUN_ID
syncctl recover RUN_ID
syncctl experiment run PROFILE ...
syncctl association run RUN_ID --adapter nearest-ntp ...
syncctl association status RUN_ID [ASSOCIATION_ID]
syncctl inference run RUN_ID --adapter dummy
syncctl inference status RUN_ID [INFERENCE_ID]
```

Los dry-runs no abren SSH, no escriben, no usan NFS y no controlan USRP.

## 8. Evidencia de infraestructura y `nosync_passive`

La cadena PC5 → SSH → procesos → NFS → manifiestos → inferencia dummy se validó
primero con workers sintéticos. Después se integraron smokes WiFi y 5G y el
perfil físico `nosync_passive`.

Topología validada de `nosync_passive`:

```text
5G comercial -> B210/PC1 (RX 5G)
N310/PC3PC4  -> B210/PC2 (RX WiFi)
PC5          -> SSH + NFS + validación + asociación + inferencia dummy
```

Los B210 de PC1 y PC2 reinician epochs locales independientes. El manifiesto
declara sus dominios `not_comparable`. NTP solo alimenta el derivado
`nearest-ntp`, etiquetado
`approximate_operational_ntp_association_not_acquisition_alignment`.

Resultados representativos ya aceptados:

- Combinado de 200: WiFi 189/200, 5G 823/834, dataset `COMPLETE`.
- Tres runs de 1000: WiFi 935, 943 y 948; 5G aproximadamente 99 % válido;
  datasets, asociaciones e inferencias dummy correctos.
- Dataset posterior de posiciones 4×4: 16 posiciones aceptadas, con intentos
  adicionales fallidos preservados para diagnóstico.

La recepción WiFi presenta ocasionalmente runs catastróficas con muy pocas
decodificaciones aunque TX, NFS y UHD parezcan sanos. Una campaña nocturna de
120 runs de 590 obtuvo 115 completas y cinco fallidas. Los fallos se conservan
como `FAILED`; no se rebaja el umbral de aceptación del 80 %.

En 5G se mitigaron overflows de arranque haciendo warmup completo antes de
readiness, usando un worker DSP y desacoplando escritura mediante una cola
asíncrona. La configuración B210 actual usa `num_recv_frames=256`.

## 9. WiFi BF definitivo actual

El contrato BF vigente en `modulos_rx_tx` es HE NDP-like propietario y no el
Golay52 histórico.

- 20 o 40 MHz seleccionables.
- Ocho HE-LTF por paquete.
- CSI complejo `[8,122]` en HE20 o `[8,242]` en HE40.
- Payload de control ALB validado.
- TX N310 temporizado; RX B210.
- `wifi_bf_like` es un integration smoke SISO, no un experimento MIMO 4×4 ni
  una inferencia científica.

Configuración de campaña que alcanzó alta fiabilidad:

- RX gain 60 dB.
- TX gain 70 dB.
- Amplitud 1.0.
- Threshold 0.90.
- Periodo 100 ms.
- Dos copias RF por contador separadas 20 ms, con deduplicación en RX.

Resultados finales representativos:

- HE20: 586/590, 99,32 %, sin errores de captura.
- HE40: 588/590, 99,66 %, sin errores de captura.

La mejora clave fue eliminar escrituras síncronas a NFS del camino crítico,
reutilizar buffers IQ y ampliar buffers UHD. La doble copia a 20 ms no cabe tal
cual en la futura ventana BF de 2 ms; su política debe rediseñarse sin alterar
el baseline ya validado.

## 10. Qué demuestra `testmultiband`

El banco X410 contiene ensayos a 2,45 GHz con tono estrecho a 1,92 Msps. Sus
scripts nuevos no están todavía versionados.

### 10.1 TX multicanal

`pythonarratx410.py` transmite un tono por hasta cuatro canales con comienzo
UHD temporizado común y pesos de fase por canal. Demuestra streaming
multicanal y comandos temporizados, no beamforming calibrado por sí solo.

### 10.2 Calibración y scan 4TX×1RX

`bf_cal_scan.py` usa cuatro TX como ULA de separación λ/2 y un RX de sonda del
mismo X410. Mide por OTA amplitud/fase de cada cadena y aplica correcciones a
un vector de steering. En el resultado conservado, el máximo no calibrado
apareció aproximadamente a +14° y el calibrado a 0°, con una profundidad de
scan mucho mayor. Es evidencia útil de que la calibración corrige offsets de
cadena durante una sesión sintonizada.

No es un radar 4×4, no usa HE NDP-like, no estima Doppler y no prueba dos AP.

### 10.3 Ensayo 2TX×2RX

`2x2.py` mide una matriz H 2×2 con sounding secuencial, prueba MRT, zero forcing
y scan. El resultado guardado tiene rango 2, condición aproximada 1,90 y picos
de scan cercanos a +1,3° y -49,5°. Las curvas son anchas porque una ULA de dos
elementos tiene poca resolución angular, especialmente cerca de endfire. Esto
no es evidencia de inestabilidad temporal por sí mismo.

### 10.4 Ensayo 4TX×4RX

`4x4.py` hace sounding de una matriz H 4×4, MRT/ZF y scan con cuatro TX y cuatro
RX del mismo X410. El resultado guardado tiene rango 4 y condición aproximada
6,03. Los ángulos estimados fueron 0°, -29,9°, +49,4° y +26,6°; los máximos
medidos del scan fueron aproximadamente 0°, -41,4°, +48,4° y +37,4°. Las
diferencias de algunos receptores indican geometría, multipath, calibración o
modelo de campo lejano imperfectos y requieren caracterización.

Este resultado demuestra acceso simultáneo a las cuatro cadenas, sounding y
pesos espaciales estrechos. Sigue siendo tono estrecho, un solo X410, una
sesión local y sin contrato de datos del framework.

### 10.5 Claim permitido

Puede afirmarse: “se ha demostrado en laboratorio un array lineal de cuatro
elementos con X410, calibración OTA, steering y sounding/MRT/ZF 2×2 y 4×4 con
tono estrecho”.

No puede afirmarse todavía: “se ha construido un radar activo 4×4 distribuido,
un AP WiFi MIMO interoperable, un sistema Doppler sectorial validado o dos AP
sincronizados por 5G”.

## 11. Arquitectura BF multi-AP seleccionada

La dirección confirmada abandona el candidato “un N310 multibanda por AP” como
arquitectura principal y adopta, para cada uno de dos AP:

```text
AP A: B210 RX 5G + X410 WiFi 4×4 TX/RX
AP B: B210 RX 5G + X410 WiFi 4×4 TX/RX
```

Los AP no disponen de backhaul de coordinación durante la ejecución. Ambos
observan la misma celda 5G y reciben previamente una agenda complementaria:

```text
oportunidad 1: AP A transmite BF; AP B escucha
oportunidad 2: AP B transmite BF; AP A escucha
```

Cada pareja B210+X410 debe tener una relación temporal local determinista,
preferentemente 10 MHz, PPS y epoch compartidos o una transformación calibrada
equivalente. Las dos parejas no deben compartir una referencia física común en
el experimento final, porque entonces la coordinación no procedería realmente
del 5G OTA. Un clock común a ambos AP puede usarse solo como baseline o
diagnóstico claramente etiquetado.

Dentro del X410, los cuatro canales WiFi comparten timekeeper y referencia,
pero requieren calibración de amplitud/fase tras sintonizar. Entre AP no se
asume coherencia de fase RF.

La descripción técnica completa está en
`docs/BF_MULTI_AP_SYNC_ARCHITECTURE.md`.

### 11.1 Primer paso obligatorio: validación X410-only 2×2+5G

Antes de desplegar la pareja final B210+X410 se probará el algoritmo con dos
X410 independientes, uno por AP. En cada X410:

- Dos cadenas RF se dedicarán al WiFi 2×2 TDD.
- Al menos otra cadena se dedicará a recibir 5G.
- 5G y WiFi compartirán el mismo timekeeper del X410 local.

Los dos X410 no compartirán 10 MHz, PPS ni otro clock físico. La referencia
entre AP seguirá siendo la misma celda 5G OTA. De esta manera se valida de
extremo a extremo PBCH/MIB, epoch común, heartbeat PSS/SSS, agenda
complementaria, predicción del ciclo siguiente y comandos UHD temporizados, sin
introducir todavía el mapeo temporal entre dos USRP distintos dentro de cada
AP.

Esta fase acepta peor sensing: dos elementos producen menos ganancia y sectores
más anchos que cuatro. Sus criterios principales serán error temporal, ausencia
de late commands/colisiones, roles complementarios y reproducibilidad; no
resolución angular ni rendimiento Doppler definitivo.

La prueba no valida:

- La transformación B210↔X410.
- El clock/PPS local de la arquitectura final.
- HE NDP-like 4×4, CSI 4×4 o sector sweeping final.
- Coherencia de fase RF entre AP.

El `2x2.py` actual de `testmultiband` tampoco implementa directamente esta
topología: usa dos canales solo TX y otros dos solo RX con tono estrecho. Puede
aportar sounding y pesos, pero el nuevo smoke necesitará dos cadenas WiFi TDD,
una cadena 5G separada y el tracker/scheduler 5G.

## 12. Sincronización por ciclos 5G

La agenda no se basa en contar SSB desde que arranca cada AP. Un AP podría
encenderse en t=0 y otro en t=30 ms y obtener contadores locales distintos. El
procedimiento acordado es:

1. Cada AP busca la misma celda y la misma referencia SSB.
2. Decodifica PBCH/MIB para adquirir un epoch común, incluyendo información de
   trama suficiente para identificar el ciclo.
3. Estima el comienzo exacto PSS en ticks del receptor 5G local.
4. En la fase X410-only ese timestamp ya pertenece al timekeeper que ejecutará
   WiFi. En la arquitectura final se convierte desde B210 al dominio X410
   mediante el vínculo temporal local y sus retardos calibrados.
5. PSS/SSS actúa después como heartbeat rápido; PBCH/MIB se revalida
   periódicamente o al perder lock.
6. El scheduler programa siempre el ciclo nominal siguiente completo,
   aproximadamente 100 ms por adelantado.
7. Muestras y comandos UHD se encolan con timestamp absoluto futuro. No se
   ejecuta `sleep(5 ms)` después de detectar un PSS.
8. Si el comando llega tarde o la incertidumbre excede el margen, el AP omite
   el turno y permanece silencioso; no transmite “lo antes posible”.

Baseline de validación, aún parametrizable:

- SSB nominal cada 20 ms y ciclo de cinco oportunidades/100 ms, sujeto a medir
  la celda real.
- `slot_offset` inicial: 5 ms desde el PSS objetivo.
- Ventana TX: 2 ms.
- Diez paquetes HE NDP-like con inicios cada 200 µs, aproximadamente 1,936 ms.
- RX continuo o con guardas conservadoras durante caracterización.

`lookahead`, `slot_offset`, duración TX y guardas RX son magnitudes distintas.

## 13. Sensing, fase y Doppler

La falta de coherencia RF entre AP no impide todo sensing. HE-LTF proporciona
una estimación de canal por paquete. Se pueden explorar features robustas a
fase común desconocida: amplitud, diferencias entre subportadoras/antenas,
eliminación de common phase error, corrección CFO, referencia al camino directo
o estático, PCA y Doppler sobre CSI corregido.

El código Doppler existente elimina componentes de fase comunes y pendiente
temporal, pero aún no está integrado con el array X410 4×4 ni con el scheduler
5G. La coherencia necesaria debe medirse, no asumirse.

Una limitación crítica es el muestreo slow-time. Si un sector solo se visita
cada 100 ms, su tasa es 10 Hz y el Doppler no ambiguo ideal queda limitado a
aproximadamente ±5 Hz. Diez paquetes dentro de 2 ms mejoran el snapshot y
permiten analizar dinámica rápida local, pero no sustituyen una secuencia
coherente larga para resolución Doppler. La cadencia de revisita por sector es
una decisión científica abierta.

Una ULA de cuatro elementos λ/2 produce sectores relativamente anchos y solo
resuelve una dimensión. Cerca de endfire empeora la resolución y existe
ambigüedad frente/espalda con elementos omnidireccionales. Sectorización fina
2D requerirá más apertura, array planar, elementos direccionales o priors de
geometría.

## 14. Riesgos principales

### Temporales y RF

- Timestamp PSS incorrecto si se usa tiempo de host o comienzo de bloque sin
  offset del detector.
- Falta de mapeo estable B210↔X410.
- Comandos UHD tardíos.
- Deriva, CFO y phase noise entre AP.
- Fase de canales X410 diferente tras retune o reinicio.
- Acoplamiento TX→RX, saturación y multipath.
- Periodicidad/identidad SSB distinta de la suposición nominal.

### Datos e infraestructura

- NFS en camino crítico de adquisición; se han mitigado pausas con escritores
  asíncronos, pero una pérdida de NFS debe abortar sin publicar `COMPLETE`.
- Árboles Git remotos sucios impiden runs reproducibles.
- `testmultiband` contiene resultados y código no versionado.
- El enlace de 1 GbE de PC5 puede ser cuello de botella para IQ intensivo.

### PC5 y modelo externo

- PC5 es Ubuntu 24.04, Python 3.12 y `aarch64` con NVIDIA GB10.
- El framework puro Python es compatible; el modelo externo puede depender de
  wheels, CUDA o extensiones que no existan para ARM64/Python 3.12.
- No debe adaptarse silenciosamente el modelo: su equipo debe proporcionar
  entorno, versión, comando, contrato de entrada/salida y criterios.

## 15. Decisiones confirmadas y abiertas

### Confirmadas

- PC5 orquesta, almacena y ejecuta inferencia externa; no entrena el modelo.
- Los datasets se publican solo con manifiesto `COMPLETE` y checksums.
- Los timestamps científicos proceden de USRP y offset de detector.
- NTP/PTP de host no sustituyen sincronización de adquisición.
- La primera coordinación BF usa dos AP sin backhaul y agenda provisionada.
- PBCH/MIB adquiere epoch; PSS/SSS mantiene heartbeat.
- Se programa el ciclo siguiente con timed commands, no sleeps reactivos.
- Arquitectura seleccionada por AP: B210 5G + X410 WiFi 4×4.
- Antes de esa arquitectura, el primer hito hardware será X410-only: dos X410
  independientes, cada uno con WiFi 2×2 y RX 5G en el mismo timekeeper, para
  validar el algoritmo de sincronización aunque el sensing sea peor.
- La sincronización 5G alinea tiempo/slots, no fase RF entre AP.
- HE NDP-like 20/40 MHz es la waveform BF vigente; Golay52 es histórico.

### Abiertas

- Mapeo físico definitivo de AP A/B a PCs.
- Asignación exacta de canales/daughterboards, rates y antenas del smoke
  X410-only 2×2+5G, junto con su caracterización de aislamiento interno.
- Hardware y cableado exactos para 10 MHz/PPS local en cada AP.
- Procedimiento de epoch y calibración de retardo B210↔X410.
- Definición exacta del ciclo desde SFN/índice SSB y revalidación.
- `slot_offset`, guardas, política holdover y umbral de late command.
- Integración HE 4×4, código de calibración y orden de sectores.
- Cadencia slow-time y métrica Doppler.
- Features científicas y necesidad real de coherencia de fase.
- Nombre/mapeo del futuro perfil BF respecto a los seis experimentos.
- Contrato final del modelo externo.

## 16. Reglas para una nueva IA

Antes de actuar:

1. Verificar por separado estado y SHA del padre y ambos submódulos.
2. No limpiar, resetear, aplicar stashes ni actualizar submódulos sin permiso.
3. No interpretar `testmultiband` como código de producción.
4. No versionar seriales, IPs, usuarios, inventarios locales o resultados.
5. No controlar hardware, RF, NFS, sudo o SSH remoto sin autorización expresa.
6. Probar primero con dobles y dry-run.
7. Mantener separados host time, device time, frame timestamp y RF phase.
8. Marcar con precisión qué está implementado, probado en software, probado en
   RF, aceptado científicamente o solo propuesto.
