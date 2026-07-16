# SYNC — sensing multibanda sincronizado

Este repositorio padre organiza el trabajo experimental para estudiar cómo afecta la sincronización temporal al *sensing* multiestático y multibanda con señales 5G y WiFi.

La descripción autoritativa del objetivo científico y de los experimentos es [`ProjectDescription.md`](ProjectDescription.md). Este documento la resume para orientar el repositorio, pero no la sustituye.

## Estado actual

El repositorio contiene dos proyectos independientes como submódulos:

- `modulos_rx_tx`: módulos de transmisión, recepción, captura, procesado e inferencia para 5G SSB y beacons WiFi con USRP.
- `rx_sync`: prototipos de recepción simultánea multibanda con USRP X410 y pruebas iniciales de waveforms activas.

El framework padre está implementado como paquete Python `sync-framework`, con CLI `syncctl`. Además del recorrido local `nosync_passive`, incluye una prueba de infraestructura `distributed_dummy`: PC5 coordina workers sintéticos por SSH, comparte el dataset por NFSv4, publica una sesión verificable y ejecuta una inferencia dummy local. Este recorrido no ejecuta DSP, UHD, USRPs ni RF.

## Roles de los equipos

| Equipo | Responsabilidad prevista |
|---|---|
| PC1 | Transmisor 5G en experimentos activos. No se utiliza cuando la fuente es el 5G comercial. |
| PC2 | Transmisor WiFi, tanto para beacons como para la señal activa correspondiente. |
| PC3 | Receptor 5G, activo o pasivo. |
| PC4 | Receptor WiFi, activo o pasivo. |
| PC5 | Orquestador, servidor de datasets y ejecución del modelo externo para fusión/inferencia conjunta. |

En los experimentos de recepción sincronizada, las funciones lógicas de PC3 y PC4 pueden realizarse con un único servidor conectado a un X410. Sus dos canales comparten el reloj del dispositivo y permiten capturar simultáneamente las bandas WiFi y 5G.

## Experimentos previstos

1. `nosync_passive`: el 5G comercial emite SSBs, PC3 los recibe, PC2 transmite beacons WiFi y PC4 los recibe. PC3 y PC4 envían sus resultados a PC5 sin sincronización común.
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
- Arranque receiver-first, parada transmitter-first, *dry-run*, procesos locales seguros, dobles de proceso/SSH y recuperación.
- Dos dominios temporales RX explícitamente no comparables; no existe emparejamiento temporal 5G/WiFi.
- Contrato batch para el futuro modelo externo y adaptador dummy determinista, limitado a validar la integración posterior a una sesión `COMPLETE`.
- Worker remoto autónomo Python 3.10+, routing SSH con autorización explícita, identidad PID/start-time y logs en PC5.
- Provisionado NFSv4 explícito e idempotente, con verificación mediante sentinels y teardown limitado a recursos gestionados.

Para inspeccionar el plan sin mutar el sistema:

```bash
PYTHONPATH=src python3 -m sync_framework.cli \
  --inventory config/inventory.example.yaml \
  experiment plan profiles/nosync_passive.yaml \
  --param label=example --param duration_s=1
```

Los tests se ejecutan con `PYTHONPATH=src pytest`. La captura y la inferencia de este incremento siguen siendo exclusivamente sintéticas, aunque el recorrido de infraestructura use SSH y NFS reales.

La prueba distribuida utiliza un inventario local ignorado. El ejemplo sin datos del laboratorio está en `config/inventory.distributed.example.yaml`:

```bash
PYTHONPATH=src python3 -m sync_framework.cli \
  --inventory config/inventory.local.yaml storage bootstrap --apply

PYTHONPATH=src python3 -m sync_framework.cli \
  --inventory config/inventory.local.yaml experiment run profiles/distributed_dummy.yaml \
  --param label=lab-ssh-nfs --param duration_s=10 \
  --inference dummy --allow-remote-simulation
```

## Límites de esta fase

- SSH real solo admite comandos `simulation` y exige `--allow-remote-simulation`; NFS solo cambia mediante `--apply`.
- El perfil distribuido produce datos y eventos marcados como sintéticos. No valida captura científica ni sincronización de adquisición.
- No hay control de hardware ni ejecución DSP.
- `sync_reception`, los restantes perfiles experimentales y la inferencia externa siguen pendientes.
- Los submódulos conservan su código e historial y no se han modificado.
- Cualquier ampliación no contenida en la descripción se propone primero y requiere validación expresa.

## Continuación del desarrollo

El desarrollo continúa en la máquina Ubuntu que actúa como PC5 siguiendo [`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md). NFS, conectividad real, integración DSP, `sync_reception` y hardware requieren incrementos y autorizaciones posteriores.
