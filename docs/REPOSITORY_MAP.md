# Mapa del repositorio

## Repositorio padre

El repositorio `SYNC` es el punto de integración de los experimentos. Contiene el primer núcleo seguro y simulado del framework, además de la documentación y las referencias fijadas a los dos repositorios hijos.

```text
SYNC/
├── pyproject.toml              # Paquete Python 3.12 y CLI syncctl
├── constraints/               # Versiones verificadas para PC5 aarch64
├── src/sync_framework/         # Dominio, planificación, estado y orquestación
├── schemas/v1/                # Contratos JSON Schema versionados
├── profiles/                  # nosync_passive y prueba distributed_dummy
├── config/                    # Inventarios de ejemplo; el real se ignora
├── tools/                     # Worker remoto dummy autónomo Python 3.10+
├── tests/                     # Unitarios e integración sin hardware ni red
├── ProjectDescription.md       # Fuente autoritativa del proyecto
├── README.md                   # Entrada y resumen del repositorio padre
├── docs/
│   ├── REPOSITORY_MAP.md       # Este mapa
│   ├── EXPERIMENT_MATRIX.md    # Casos experimentales y disponibilidad
│   └── DEVELOPMENT_PLAN.md     # Arquitectura aprobada y roadmap
├── modulos_rx_tx/              # Submódulo 5G/WiFi RX-TX y sensing, intacto
└── rx_sync/                    # Submódulo de pruebas multibanda X410
```

El paquete padre implementa validación de contratos, procesos locales y SSH `simulation`, estado atómico, supervisión foreground, NFSv4 explícito, publicación, recuperación e inferencia dummy. La integración DSP/hardware permanece deshabilitada. El worker distribuido vive en `tools/remote_dummy_worker.py` para ejecutarse directamente desde clones Git sin instalar el paquete en los clientes.

Los archivos locales `AGENTS.md` y `.codex/*` existen para mantener continuidad durante el desarrollo, pero están ignorados deliberadamente y no forman parte del producto versionado. `.codex/HANDOFF.md` es el punto de entrada para trasladar el workspace a PC5 y retomarlo sin el chat original.

## Submódulo `modulos_rx_tx`

- Origen: `https://github.com/ammendezuc3m/DT_sensing_fusion_WIFI5G.git`
- Revisión fijada inicialmente: `68daca188e308b65b3cfdf2680532062306c49c5`

Capacidades observadas:

- Recepción de SSB 5G comercial con USRP B210 y extracción `dataSSB`/`rxGridSSB`.
- Captura de datasets, entrenamiento e inferencia para sensing 5G.
- Generación y transmisión de beacons 802.11a/g mediante USRP.
- Recepción WiFi, seguimiento L-LTF y extracción de CSI.
- Salidas H5, CSV, JSON de estado e integración experimental mediante SCP.
- Material MATLAB histórico o de validación junto al flujo Python recomendado.

Limitaciones relevantes para el proyecto padre:

- Los timestamps 5G online actuales representan principalmente el momento de procesamiento/escritura en el host, no una referencia común de llegada entre PCs.
- El receptor WiFi conserva tiempo de host aproximado y tiempo local del USRP, pero ese reloj se reinicia de forma independiente.
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

## Fronteras de responsabilidad

- Cada submódulo mantiene su propio historial, dependencias, documentación y política de datos generados.
- El repositorio padre coordina por ahora productores simulados sin copiar ni absorber la lógica DSP de los hijos.
- Cambiar una interfaz o script de un submódulo requiere una necesidad concreta y aprobación previa.
- Datasets, capturas, estados de ejecución, credenciales y contexto de agentes no se versionan en el padre.
