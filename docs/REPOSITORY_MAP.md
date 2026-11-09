# Mapa del repositorio

## Repositorio padre

El repositorio `SYNC` es el punto de integración de los experimentos. Contiene el primer núcleo seguro y simulado del framework, además de la documentación y las referencias fijadas a los dos repositorios hijos.

```text
SYNC/
├── pyproject.toml              # Paquete Python 3.12 y CLI syncctl
├── constraints/               # Versiones verificadas para PC5 aarch64
├── src/sync_framework/         # Dominio, planificación, estado y orquestación
├── schemas/v1/                # Contratos JSON Schema versionados
├── profiles/                  # Perfiles simulados y smokes hardware acotados
├── config/                    # Inventarios de ejemplo; el real se ignora
├── tools/                     # Workers remotos autónomos Python 3.10+
├── tests/                     # Unitarios e integración sin hardware ni red
├── ProjectDescription.md       # Fuente autoritativa del proyecto
├── README.md                   # Entrada y resumen del repositorio padre
├── docs/
│   ├── REPOSITORY_MAP.md       # Este mapa
│   ├── EXPERIMENT_MATRIX.md    # Casos experimentales y disponibilidad
│   ├── DEVELOPMENT_PLAN.md     # Arquitectura aprobada y roadmap
│   ├── PORTABLE_AI_CONTEXT.md  # Handoff completo, versionado y autocontenido
│   ├── BF_MULTI_AP_SYNC_ARCHITECTURE.md # Diseño B210+X410 por AP
│   └── BF_MULTI_AP_TESTBED_STATUS.md # Estado medido del banco de dos X410 (paper tracker)
├── modulos_rx_tx/              # Submódulo 5G/WiFi RX-TX y sensing
└── rx_sync/                    # Submódulo de pruebas multibanda X410
```

El paquete padre implementa validación de contratos, procesos locales, SSH con capacidades explícitas, estado atómico, supervisión foreground, NFSv4 explícito, publicación, recuperación e inferencia dummy. `nosync_passive` añade publicación raw con eventos USRP locales y asociaciones `nearest-ntp` derivadas e independientes. `wifi_bf_like` integra el BF definitivo HE NDP-like seleccionable a 20/40 MHz como smoke SISO de una sola banda, sin convertirlo todavía en un experimento científico multibanda activo ni en BF X410 4×4. PC5 mantiene `runs/<run_id>` como layout canónico y crea para cada intento nuevo un catálogo secundario navegable por metadata experimental; las entradas son enlaces relativos y no duplican datasets. Todos los perfiles hardware habilitan DSP/RF solo con sus flags de autorización; las pruebas automáticas siguen usando dobles. Los workers distribuidos viven en `tools/` y se ejecutan desde clones Git sin instalar el paquete en los clientes. El despliegue acepta ramas de desarrollo o `detached HEAD` siempre que todos los nodos participantes estén limpios y en el mismo commit exacto que PC5.

Los archivos locales `AGENTS.md` y `.codex/*` existen para mantener continuidad durante el desarrollo, pero están ignorados deliberadamente y no forman parte del producto versionado. `.codex/HANDOFF.md` es el punto de entrada para trasladar el workspace a PC5 y retomarlo sin el chat original.

## Submódulo `modulos_rx_tx`

- Origen: `https://github.com/ammendezuc3m/DT_sensing_fusion_WIFI5G.git`
- Revisión fijada en el snapshot 2026-09-24: `2ac9ee9d561df5afad4a5c66148ce01b28ccd241`

Capacidades observadas:

- Recepción de SSB 5G comercial con USRP B210 y extracción `dataSSB`/`rxGridSSB`.
- Receptor continuo `online_5g_rxgrid_jsonl.py`, con serial obligatorio y filas `rxGridSSB` JSONL cuyo timestamp se clasifica como tiempo operacional de serialización del host.
- Captura de datasets, entrenamiento e inferencia para sensing 5G.
- Generación y transmisión de beacons 802.11a/g mediante USRP.
- Recepción WiFi, seguimiento L-LTF y extracción de CSI.
- Trazas opcionales de latencia operacional WiFi por bloque y frame, sin semántica de timestamp RF.
- Receptor paralelo 5G que conserva el `RXMetadata.time_spec` del primer sample de cada bloque, publica `rxGrid`/HSSB binarios y referencia cada fila con ticks exactos del USRP.
- TX WiFi N310 finito con estrategias temporizada y por lotes, estado de cierre y diagnóstico de eventos asíncronos.
- Módulos BF-like, Doppler, RTC-SI y utilidades de fusión recuperados del trabajo de laboratorio. El sounding HE NDP-like de 20/40 MHz es el BF definitivo y dispone de un smoke SISO del framework; el Golay52 queda como perfil histórico independiente. Doppler, RTC-SI, fusión y el experimento científico activo completo siguen fuera del recorrido y no sustituyen el modelo externo.
- Escritores asíncronos para que JSONL/CF32 y NFS no bloqueen el camino UHD, reutilización de buffers IQ y `num_recv_frames=256` en los receptores B210 integrados.
- Salidas H5, CSV, JSON de estado e integración experimental mediante SCP.
- Material MATLAB histórico o de validación junto al flujo Python recomendado.

Limitaciones relevantes para el proyecto padre:

- El smoke 5G antiguo conserva timestamps de host operacionales; el nuevo receptor paralelo sí publica ticks USRP canónicos locales.
- El receptor WiFi conserva ticks locales del USRP y tiempo de host operacional, pero su época se reinicia de forma independiente a la de 5G.
- Los transmisores y receptores activos BF/BF-like no están todos terminados.

## Submódulo `rx_sync`

- Origen: `https://github.com/DavidRicoCodes/x410Multiband.git`
- Revisión fijada inicialmente: `274afed086ef635e9665cc40b7072c041112e42a`

Capacidades observadas:

- Configuración de dos canales RX del mismo X410 con reloj y tiempo de dispositivo compartidos.
- Inicio temporizado común de la recepción de ambos canales.
- Pruebas iniciales para generar y transmitir una waveform OFDM orientada a sensing.

Limitaciones relevantes:

- La captura X410 es todavía un prototipo continuo y no genera el dataset multibanda integrado requerido por los experimentos.
- No existe todavía adaptación conjunta hacia los procesadores 5G y WiFi del otro submódulo.
- La waveform activa y su integración experimental siguen en desarrollo.

El primer incremento nuevo previsto antes de B210+X410 usará dos X410
independientes. Cada uno combinará WiFi 2×2 TDD y RX 5G en un mismo timekeeper
para validar el algoritmo de sincronización OTA. Este recorrido todavía no
existe en `rx_sync` ni en `testmultiband`.

## Checkout experimental `testmultiband` en PC3PC4

PC3PC4 contiene un checkout Git independiente anidado como
`testmultiband/` dentro de su workspace local. Comparte el origen histórico de
`rx_sync` y su HEAD base es `274afed...`, pero los scripts y resultados X410
nuevos están sin registrar. No aparece como submódulo del padre y el patrón
`/testmultiband/` se ignora deliberadamente.

Capacidades observadas, todavía experimentales:

- TX de tono sincronizado por cuatro canales X410.
- Calibración OTA de amplitud/fase y beam scan 4TX×1RX.
- Sounding de matrices 2×2 y 4×4, MRT, zero forcing y scan.
- Resultados `.npz`/`.png` y herramientas de visualización.

Limitaciones:

- Ancho de banda estrecho y tono, no HE NDP-like 20/40 MHz.
- Un solo X410 y una sola sesión, no dos AP sincronizados por 5G.
- No publica schemas, eventos, manifiestos ni checksums del framework.
- Contiene defaults de laboratorio y artefactos que deben sanearse.
- No demuestra radar Doppler, CSI MIMO científico ni coherencia de fase entre
  AP.

## Fronteras de responsabilidad

- Cada submódulo mantiene su propio historial, dependencias, documentación y política de datos generados.
- El repositorio padre coordina productores simulados, smokes y el `nosync_passive` físico sin copiar ni absorber la lógica DSP de los hijos.
- Cambiar una interfaz o script de un submódulo requiere una necesidad concreta y aprobación previa.
- Datasets, capturas, estados de ejecución, credenciales y contexto de agentes no se versionan en el padre.
- El contexto portable se versiona en `docs/`; el contexto interno `.codex/`
  continúa local e ignorado.
