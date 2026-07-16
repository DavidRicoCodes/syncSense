# Plan de desarrollo del framework experimental

## Propósito y alcance inmediato

Este documento fija el orden de desarrollo y las decisiones confirmadas para el framework experimental del repositorio padre. El primer incremento seguro del núcleo ya está implementado: paquete Python, schemas v1, perfil `nosync_passive`, máquina de estados, ejecución local simulada, publicación y recuperación. Todavía no existen infraestructura NFS, SSH real, integración DSP, control hardware ni mecanismos de sincronización.

### Estado del primer incremento seguro

Completado y validado exclusivamente con simulación local:

- CLI `syncctl` y empaquetado Python 3.12.
- Inventario/perfil versionados, planificación y *dry-run* no mutante.
- Estados atómicos, auditoría, supervisión foreground y recuperación.
- Orden receiver-first/transmitter-first, manifiestos y SHA-256.
- Dos dominios `nosync_passive` independientes y `not_comparable`.
- Contrato batch del modelo externo, sin adaptador ejecutable ni inferencia.
- Tests unitarios e integración con procesos locales y SSH falsos.

Se ha añadido un segundo incremento de infraestructura con SSH y NFS reales, pero solo para workers y datos sintéticos. Incluye el perfil `distributed_dummy`, verificación de clones Git, receipts por productor e inferencia dummy en PC5. Esto valida el camino de control y publicación, no equivale a captura científica ni a la primera aceptación hardware. Siguen pendientes scripts DSP reales, UHD/USRP/RF, `sync_reception` y el modelo entregado por el otro equipo.

### Incremento distribuido de infraestructura

- Los workers se ejecutan directamente desde clones Git verificados en `main`, sin instalar el framework en clientes ni inicializar submódulos.
- El adaptador OpenSSH usa host keys estrictas, `BatchMode`, timeouts, keepalive e identidad local/remota PID + start-time.
- NFSv4 se provisiona únicamente con `storage bootstrap --apply`; no modifica `fstab`, usa `root_squash`/`all_squash` y monta con `nosuid,nodev,noexec`.
- `distributed_dummy` arranca RX antes que TX y para TX antes que RX sobre tres nodos lógicos, conservando dos clocks sintéticos `not_comparable`.
- PC5 valida receipts y checksums, publica `manifest.json` y puede ejecutar `DummyBatchModelAdapter`. El resultado dummy solo prueba el contrato; no implementa inferencia científica.

El desarrollo se realizará en este orden:

1. Orquestación y experimentos sin sincronización.
2. Recepción sincronizada mediante X410.
3. Experimentos con sincronización total.

La primera entrega ejecutable comprenderá la orquestación `no-sync` y la recepción sincronizada. Su aceptación hardware inicial se realizará con los experimentos pasivos. Los perfiles activos se prepararán y validarán estructuralmente, pero su ejecución dependerá de que sus módulos TX estén disponibles.

## Arquitectura acordada

### Orquestación y nodos

- PC5 ejecutará el orquestador central mediante Python y perfiles YAML.
- La plataforma soportada inicialmente será Ubuntu homogéneo.
- Los nodos se controlarán por SSH sin introducir inicialmente un servicio residente.
- El orquestador tendrá validación de configuración, estado persistente, *dry-run*, manejo de señales y parada ordenada.
- Cada ejecución tendrá un `run_id` único y recorrerá los estados `CREATED`, `PREFLIGHT`, `ARMED`, `RUNNING`, `FINALIZING`, `COMPLETE`, `FAILED` o `ABORTED`.
- Ningún comando que transmita RF, controle un USRP o modifique un equipo remoto se ejecutará sin autorización específica.

La interfaz prevista del launcher incluirá las operaciones:

- `storage bootstrap`
- `experiment plan`
- `preflight`
- `start`
- `status`
- `stop`
- `finalize`
- `recover`

Las IPs, usuarios SSH, seriales, rutas específicas y secretos se mantendrán en un inventario local no versionado. Los perfiles de los seis experimentos sí serán versionados.

### Almacenamiento y publicación en PC5

- PC5 alojará una exportación NFSv4 configurable.
- La exportación predeterminada será `/srv/sync-experiments`.
- El montaje predeterminado en los demás nodos será `/mnt/sync-experiments`.
- El framework provisionará y verificará NFS únicamente mediante una acción explícita que requiera privilegios.
- El preflight comprobará montaje, permisos, escritura y espacio disponible desde cada nodo.
- Una pérdida del montaje durante una captura abortará de forma segura transmisores y receptores. La sesión quedará incompleta y no se publicará.

Cada productor escribirá artefactos diferentes dentro de `runs/<run_id>/<producer>/`; dos procesos nunca escribirán simultáneamente el mismo HDF5. PC5 publicará al final un manifiesto versionado que incluya la configuración efectiva, revisiones Git, roles, dominios de reloj, etiquetas, artefactos y checksums. Una sesión solo será consumible cuando exista su manifiesto final con estado `COMPLETE`.

El desarrollo y entrenamiento del modelo de sensing quedan fuera de este framework, pero PC5 alojará y ejecutará el modelo proporcionado por su equipo. La primera interfaz será el dataset publicado y un adaptador batch configurable que registre versión, configuración, entrada, salida y estado de la inferencia. Una API o inferencia en vivo no forma parte de la primera entrega.

### Datos y timestamps

PC5 recibirá features y artefactos procesados:

- 5G: `rxGridSSB`, `dataSSB`, métricas y eventos.
- WiFi: CSI, métricas y eventos.
- El IQ crudo estará desactivado por defecto y solo se conservará localmente en capturas diagnósticas solicitadas expresamente.

El contrato de datos incluirá un manifiesto JSON de sesión, manifiestos por productor y un índice versionado de eventos. Cada evento contendrá al menos:

- `run_id`, `event_id`, modalidad y tipo de trama.
- `clock_domain_id`.
- Tiempo exacto expresado como ticks enteros y frecuencia del contador.
- Punto de referencia temporal de la trama.
- Offset del detector dentro del bloque, expresado en muestras.
- Incertidumbre estimada y discontinuidad de captura.
- Referencia al artefacto y a la fila correspondiente.

Las referencias temporales canónicas serán:

- 5G: comienzo del SSB/PSS.
- WiFi: comienzo del PPDU, calculado desde el punto detectado del preámbulo.

PTP entre el servidor X410 y PC5 no será un requisito para la sincronización científica. Los tiempos de host se usarán para operación y logs; los timestamps USRP representarán los tiempos de adquisición.

## Primera entrega: no-sync y recepción sincronizada

### Núcleo del orquestador

1. Implementar el controlador en PC5, los perfiles YAML, la validación, la máquina de estados y los adaptadores SSH.
2. Añadir *dry-run*, comprobaciones previas, observación de procesos y parada segura.
3. Provisionar NFS en PC5 de forma idempotente y validar el acceso desde los nodos.
4. Implementar el contrato de sesión, artefactos y publicación mediante manifiesto final.

### `nosync_passive`

Este será el primer recorrido experimental completo:

1. PC5 crea la sesión y valida todos los nodos.
2. Arranca PC3 y PC4 y espera a que ambos receptores estén preparados.
3. Arranca PC2 para transmitir beacons WiFi.
4. La ejecución no usa un comienzo temporizado ni considera comparables los relojes de PC3 y PC4.
5. La parada detiene primero la transmisión y después los receptores para conservar las colas de recepción.
6. PC5 valida los artefactos y publica el manifiesto final.

El dataset resultante mantendrá dos dominios de reloj explícitamente independientes. El framework no realizará emparejamiento temporal preciso. La ejecución del modelo se añadirá mediante el adaptador externo cuando su equipo entregue el contrato correspondiente.

#### Incremento previo de integración WiFi

Antes de incorporar RX 5G se valida el enlace PC2 → PC3PC4 → NFS → PC5 mediante `wifi_link_smoke`. El usuario elige entre 1 y 600 beacons; PC5 arranca el RX WiFi, espera una tasa observada de al menos 19 Msps, lanza el TX finito y drena el receptor hasta 2 s de silencio o 10 s como máximo. La publicación requiere al menos el 80 % de contadores, cierre JSONL/CF32 y ausencia de errores UHD/TX.

Este recorrido se clasifica expresamente como `integration_smoke`: conserva los timestamps nativos del receptor solo como campos no verificados, no crea eventos temporales canónicos y no afirma alineación 5G/WiFi. La inferencia posterior sigue siendo dummy y solo resume solicitados, recibidos, perdidos y ratio.

### Perfiles activos iniciales

Se prepararán los perfiles de `nosync_active` y `sync_reception_active`, pero solo se exigirá que validen estructuralmente. Su aceptación hardware se pospondrá hasta que estén disponibles los transmisores activos.

### `sync_reception_passive`

La implementación podrá modificar el repositorio padre y `rx_sync`. `modulos_rx_tx` se reutilizará sin cambios mientras sus interfaces actuales lo permitan.

1. Un único streamer multicanal del X410 capturará simultáneamente 5G y WiFi.
2. Cada detector devolverá el offset de la trama dentro del bloque UHD recibido.
3. El timestamp del evento se calculará sumando al tiempo del primer sample del bloque el offset del detector dividido por la frecuencia de muestreo.
4. Las capturas producirán dos timelines independientes, 5G y WiFi, dentro del mismo `clock_domain_id`.
5. No se forzará durante la captura el emparejamiento de cada SSB con un beacon. PC5 o el modelo podrán construir posteriormente ventanas temporales sin perder eventos.
6. Se registrarán overflows, discontinuidades, incertidumbre del detector y referencias a los artefactos procesados.
7. Se caracterizarán el sesgo y el jitter de ambos detectores antes de definir una tolerancia para `sync_all`.

## Segunda entrega: experimentos activos

- Integrar y validar `nosync_active` y `sync_reception_active` cuando estén disponibles sus transmisores.
- Mantener el mismo contrato de sesión, eventos y artefactos que en los experimentos pasivos.
- No modificar `modulos_rx_tx` salvo aprobación posterior si se demuestra que la reutilización sin cambios es técnicamente insuficiente.

## Tercera entrega: sincronización total

### Referencia temporal

- Distribuir 10 MHz y PPS entre los USRPs implicados e inicializar un epoch común.
- No depender de PTP para programar RF; los comandos TX utilizarán tiempos futuros del USRP.
- El criterio principal de sincronización será alinear la llegada detectada del SSB y del beacon en el X410.
- Se compensarán mediante calibración los retardos fijos de detector, RF, cables y procesamiento.

### `sync_all_passive`

1. Detectar varios SSBs y estimar su periodo, fase temporal, jitter y confianza.
2. Predecir una llegada futura de SSB.
3. Comunicar a PC2, con margen suficiente, el tiempo USRP en el que debe transmitir el beacon.
4. Ejecutar la transmisión mediante un comando UHD temporizado.
5. Medir en el X410 el error residual entre la llegada del SSB y la del beacon.
6. Registrar jitter, comandos tardíos, pérdidas y cualquier discontinuidad.

### `sync_all_active`

- Programar las dos transmisiones controladas desde el mismo dominio temporal.
- Mantener la llegada al X410 como criterio primario de validación.
- Registrar también la época de transmisión cuando pueda observarse en los emisores controlados.

No se fijará anticipadamente una tolerancia arbitraria. El umbral final se decidirá después de caracterizar los detectores y el hardware durante `sync_reception`.

## Verificación y aceptación

### Software e infraestructura

- Tests unitarios de perfiles, manifiestos, estados, cálculo de timestamps y transiciones de error.
- Tests de integración con SSH simulado y procesos falsos, incluyendo fallos de arranque y parada.
- *Dry-run* que muestre nodos, comandos, orden de arranque y rutas sin controlar hardware.
- Pruebas NFS de permisos, concurrencia, falta de espacio y desaparición del montaje.
- Las sesiones fallidas no podrán contener un manifiesto final `COMPLETE`.

### Experimentos

`nosync_passive` deberá producir una sesión reproducible, con artefactos separados y dos dominios de reloj marcados explícitamente como no comparables.

`sync_reception_passive` deberá:

- Producir eventos 5G y WiFi monotónicos dentro de un único dominio de reloj.
- Detectar y registrar overflows y discontinuidades.
- Verificar con señales sintéticas el cálculo `tiempo del bloque + offset del detector`.
- Medir con hardware el sesgo y jitter sin imponer todavía un umbral arbitrario.

Antes y después de cada fase se comprobará por separado el estado Git del repositorio padre y de ambos submódulos. Las pruebas RF o acciones remotas requerirán autorización específica.

## Decisiones y límites confirmados

- Plataforma inicial: Ubuntu homogéneo.
- Stack: Python y YAML.
- Controlador y servidor NFS: PC5.
- Dataset reproducible antes que inferencia en vivo.
- Features en PC5; IQ crudo local y opcional.
- Publicación mediante manifiesto final.
- Alineación RX mediante dos timelines independientes.
- Primera validación hardware con los experimentos pasivos.
- El primer desarrollo podrá modificar el padre y `rx_sync`, pero no `modulos_rx_tx`.
- La implementación, entrenamiento y métricas internas del modelo de presence detection, localization o tracking quedan fuera del framework. PC5 sí ejecutará ese modelo mediante un adaptador trazable.
- Cualquier cambio de hardware, coherencia de fase RF, tecnología de distribución de reloj o formato solicitado por el equipo de ML requerirá validación del usuario.
