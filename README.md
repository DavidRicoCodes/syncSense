# SYNC — sensing multibanda sincronizado

Este repositorio padre organiza el trabajo experimental para estudiar cómo afecta la sincronización temporal al *sensing* multiestático y multibanda con señales 5G y WiFi.

La descripción autoritativa del objetivo científico y de los experimentos es [`ProjectDescription.md`](ProjectDescription.md). Este documento la resume para orientar el repositorio, pero no la sustituye.

## Estado actual

El repositorio contiene dos proyectos independientes como submódulos:

- `modulos_rx_tx`: módulos de transmisión, recepción, captura, procesado e inferencia para 5G SSB y beacons WiFi con USRP.
- `rx_sync`: prototipos de recepción simultánea multibanda con USRP X410 y pruebas iniciales de waveforms activas.

El framework padre está implementado como paquete Python `sync-framework`, con CLI `syncctl`. Además de la prueba sintética `distributed_dummy` y los smokes hardware previos, `nosync_passive` ya describe el escenario físico separado: PC1 recibe 5G comercial, PC2 recibe WiFi y PC3PC4 transmite beacons con un N310. El software publica timestamps canónicos locales por receptor y mantiene la asociación NTP como derivado reintentable; la aceptación hardware completa de este nuevo recorrido se realiza por etapas.

## Roles de los equipos

| Equipo | Responsabilidad prevista |
|---|---|
| PC1 | Transmisor 5G en experimentos activos. No se utiliza cuando la fuente es el 5G comercial. |
| PC2 | Transmisor WiFi, tanto para beacons como para la señal activa correspondiente. |
| PC3 | Receptor 5G, activo o pasivo. |
| PC4 | Receptor WiFi, activo o pasivo. |
| PC5 | Orquestador, servidor de datasets y ejecución del modelo externo para fusión/inferencia conjunta. |

En los experimentos de recepción sincronizada, las funciones lógicas de PC3 y PC4 pueden realizarse con un único servidor conectado a un X410. Sus dos canales comparten el reloj del dispositivo y permiten capturar simultáneamente las bandas WiFi y 5G.

La topología física actual de `nosync_passive` es una excepción explícita a esos roles históricos: PC1 aloja RX 5G, PC2 aloja RX WiFi y el equipo denominado PC3PC4 aloja el TX WiFi N310. Este cambio no redefine los otros cinco experimentos.

## Experimentos previstos

1. `nosync_passive`: el 5G comercial emite SSBs; PC1 los recibe, PC3PC4 transmite beacons WiFi con N310 y PC2 los recibe. Los dos B210 RX conservan relojes independientes.
2. `nosync_active`: PC1 transmite la waveform 5G BF-like y PC2 transmite la señal BF WiFi; PC3 y PC4 reciben y PC5 combina los resultados sin sincronización común.
3. `sync_reception_passive`: se mantienen las fuentes pasivas, pero las recepciones WiFi y 5G se capturan con el X410 y un reloj de recepción común.
4. `sync_reception_active`: equivalente al caso anterior utilizando las fuentes activas de PC1 y PC2.
5. `sync_all_passive`: PC2, PC3 y PC4 comparten una referencia temporal. La llegada de SSBs comerciales sirve para calcular cuándo debe transmitirse el siguiente beacon WiFi.
6. `sync_all_active`: versión activa del experimento anterior. Inicialmente puede simplificarse la sincronización de transmisores mediante cable, manteniendo como objetivo final el aviso desde el receptor 5G al transmisor WiFi.

La matriz ampliada y el estado real de cada componente están en [`docs/EXPERIMENT_MATRIX.md`](docs/EXPERIMENT_MATRIX.md).

## Clonado

Para obtener también los dos proyectos hijos:

```bash
git clone --recurse-submodules <url-del-repositorio-padre>
```

En un clon ya existente:

```bash
git submodule update --init --recursive
```

## Núcleo y prueba distribuida disponibles

- Paquete puro Python 3.12 con layout `src/`, schemas JSON Schema v1 y perfil YAML `nosync_passive`.
- Máquina de estados persistente, auditoría JSONL, `run_id`, checksums y publicación mediante `manifest.json` con estado `COMPLETE`.
- Catálogo navegable por testbed, experimento, condición, posición, sujeto y fecha, manteniendo `runs/<run_id>` como ubicación canónica.
- Arranque receiver-first, parada transmitter-first, *dry-run*, procesos locales seguros, dobles de proceso/SSH y recuperación.
- Dos dominios temporales RX explícitamente no comparables. El dataset raw no contiene emparejamiento temporal 5G/WiFi; `nearest-ntp` puede producir después una asociación operacional aproximada sin modificarlo.
- Contrato batch para el futuro modelo externo y adaptador dummy determinista, limitado a validar la integración posterior a una sesión `COMPLETE`.
- Worker remoto autónomo Python 3.10+, routing SSH con autorización explícita, identidad PID/start-time y logs en PC5.
- Provisionado NFSv4 explícito e idempotente, con verificación mediante sentinels y teardown limitado a recursos gestionados.

Para inspeccionar el plan sin mutar el sistema:

```bash
PYTHONPATH=src python3 -m sync_framework.cli \
  --inventory config/inventory.example.yaml \
  experiment plan profiles/nosync_passive.yaml \
  --param label=example --param position=R5C6 --param num_beacons=200
```

Los tests se ejecutan con `PYTHONPATH=src pytest`. `distributed_dummy` sigue siendo exclusivamente sintético. El smoke WiFi requiere dos autorizaciones independientes y usa una inferencia dummy únicamente como comprobación de cierre del recorrido.

La prueba distribuida utiliza un inventario local ignorado. El ejemplo sin datos del laboratorio está en `config/inventory.distributed.example.yaml`:

```bash
PYTHONPATH=src python3 -m sync_framework.cli \
  --inventory config/inventory.local.yaml storage bootstrap --apply

PYTHONPATH=src python3 -m sync_framework.cli \
  --inventory config/inventory.local.yaml experiment run profiles/distributed_dummy.yaml \
  --param label=lab-ssh-nfs --param duration_s=10 \
  --inference dummy --allow-remote-simulation
```

El smoke WiFi usa el inventario local ignorado (seriales y rutas nunca se versionan):

```bash
PYTHONPATH=src python3 -m sync_framework.cli \
  --inventory config/inventory.local.yaml experiment run profiles/wifi_link_smoke.yaml \
  --param label=wifi-smoke-50 --param num_beacons=50 \
  --inference dummy --allow-hardware-receive --allow-rf-transmit
```

PC5 arranca primero el RX, exige un `STATUS` de al menos 19 Msps y solo entonces arranca el TX. Tras finalizar el TX, detiene el RX después de 2 s sin crecimiento del JSONL o, como máximo, 10 s de drenaje. La publicación exige al menos `ceil(0.8 × num_beacons)`, 52 complejos por fila, cierre exacto JSONL/CF32, cero errores UHD y `Zero sends: 0`.

El parámetro opcional `detector_threshold` conserva `0.85` como valor predeterminado y permite campañas controladas con otros umbrales. Cuando se ejecuta este smoke, el RX añade `frame-timings.jsonl` y `block-timings.jsonl` con latencias operacionales del host. Estas medidas incluyen incertidumbre de entrega USB/host y no son timestamps RF. El runner parametrizable y el formato de resultados se documentan en [`docs/WIFI_THRESHOLD_CAMPAIGNS.md`](docs/WIFI_THRESHOLD_CAMPAIGNS.md).

El smoke 5G pasivo usa `config/inventory.local.yaml`, donde el serial permanece ignorado:

```bash
PYTHONPATH=src python3 -m sync_framework.cli \
  --inventory config/inventory.local.yaml --format json \
  experiment run profiles/ssb_rx_smoke.yaml \
  --param label=ssb-smoke-10s --param duration_s=10 \
  --param min_valid_ssb_rate_hz=10 \
  --inference dummy --allow-hardware-receive
```

PC5 espera a que finalicen la configuración UHD y el warmup CFO (`=== Online loop ===`), mide `duration_s` desde ese momento y solicita una parada limpia mediante `SIGINT`. El receptor difiere esa señal hasta la frontera de la iteración en curso para cerrar JSONL y contadores de forma coherente. El comando 5G declara en el inventario su `cwd` y `PYTHONPATH`; el preflight y el worker aplican exactamente ese contexto sin depender de `.bashrc`. La publicación exige JSONL válido de grids `[240,4]`, cierre de estadísticas, ratio válido ≥80 %, al menos `ceil(duration_s × min_valid_ssb_rate_hz)` grids y ausencia de `UHD RX error`. `rx_timestamp_ns` es tiempo de serialización del host para operación: no es el timestamp de llegada del PSS.

El smoke conjunto reutiliza ambos contratos:

```bash
PYTHONPATH=src python3 -m sync_framework.cli \
  --inventory config/inventory.local.yaml --format json \
  experiment run profiles/nosync_passive_hardware_smoke.yaml \
  --param label=nosync-hw-50 --param num_beacons=50 \
  --param detector_threshold=0.85 --param min_valid_ssb_rate_hz=10 \
  --inference dummy --allow-hardware-receive --allow-rf-transmit
```

PC5 arranca `rx_5g` y `rx_wifi` en PC3PC4, espera readiness de ambos y solo después inicia `tx_wifi` en PC2. Al terminar TX, drena WiFi y solicita en paralelo la parada de los dos RX. El manifiesto conserva dos dominios B210 `not_comparable` y una frontera operacional del supervisor PC5 que no representa tiempo común de adquisición. La inferencia dummy resume ambas bandas con `fusion_performed: false`.

El recorrido físico actual usa el inventario local ignorado:

```bash
PYTHONPATH=src python3 -m sync_framework.cli \
  --inventory config/inventory.local.yaml --format json \
  experiment run profiles/nosync_passive.yaml \
  --param label=nosync-passive-r5c6 --param position=R5C6 \
  --param condition=occupied_static --param num_beacons=200 \
  --association nearest-ntp --inference dummy \
  --allow-hardware-receive --allow-rf-transmit
```

PC5 inicia RX 5G en PC1 y RX WiFi en PC2, espera readiness, aplica el guard y lanza el N310 de PC3PC4. Cada B210 reinicia de forma independiente su época UHD. Los eventos canónicos son ticks enteros del USRP local calculados como `block_start_ticks + detector_offset_samples`; nunca se comparan ticks entre receptores. Los anchors de reloj de host y NTP solo permiten al adaptador derivado `nearest-ntp` estimar una asociación UTC aproximada, etiquetada expresamente como no equivalente a alineación de adquisición.

La asociación también puede repetirse sobre un dataset ya publicado:

```bash
PYTHONPATH=src python3 -m sync_framework.cli \
  --inventory config/inventory.local.yaml association run RUN_ID \
  --adapter nearest-ntp --maximum-delta-ms 30
```

## Catálogo de ejecuciones

Cada `preflight` real conserva la sesión en su ubicación canónica
`runs/<run_id>` y crea además un enlace relativo navegable:

```text
catalog/
└── testbed=testbed1/
    └── experiment=nosync_passive/
        └── condition=occupied_static/
            └── position=R5C6/
                └── subject=anonymous/
                    └── 2026/07/27/<run_id> -> runs/<run_id>
```

La metadata que permite comprobar o reconstruir esa vista se guarda en
`runs/<run_id>/.control/catalog.json`. El estado vigente continúa siendo
`.control/state.json`; el catálogo no lo duplica ni cambia las rutas usadas por
NFS, publicación, asociación o inferencia. También se catalogan los intentos
que posteriormente terminen en `FAILED` o `ABORTED`. Los perfiles sin alguna
de estas dimensiones usan `unspecified`.

Las runs anteriores a esta funcionalidad permanecen intactas bajo `runs/` y no
se reorganizan automáticamente.

## Límites de esta fase

- SSH real admite simulaciones con `--allow-remote-simulation`. El smoke 5G exige solo `--allow-hardware-receive`; el smoke WiFi exige además `--allow-rf-transmit`. NFS solo cambia mediante `--apply`.
- El perfil distribuido produce datos y eventos marcados como sintéticos. No valida captura científica ni sincronización de adquisición.
- El software del nuevo `nosync_passive` raw está implementado; su aceptación hardware escalonada permanece separada de la validación software.
- NTP no sincroniza la adquisición. La asociación aproximada es un derivado reintentable y no forma parte del manifiesto raw inmutable.
- `sync_reception`, los restantes perfiles experimentales y la inferencia externa siguen pendientes.
- `modulos_rx_tx` conserva su historial y contiene el receptor JSONL 5G incorporado en el commit fijado; `rx_sync` permanece intacto.
- Cualquier ampliación no contenida en la descripción se propone primero y requiere validación expresa.

## Continuación del desarrollo

El desarrollo continúa en la máquina Ubuntu que actúa como PC5 siguiendo [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md). NFS, conectividad real, integración DSP, `sync_reception` y hardware requieren incrementos y autorizaciones posteriores.
