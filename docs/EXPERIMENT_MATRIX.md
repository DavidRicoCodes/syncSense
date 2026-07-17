# Matriz de experimentos

Este documento estructura los casos definidos en `ProjectDescription.md`. No constituye todavía una especificación ejecutable ni añade mecanismos de sincronización que no hayan sido aprobados.

## Convenciones

| Elemento | Función |
|---|---|
| PC1 | TX 5G activo cuando no se usa 5G comercial. |
| PC2 | TX WiFi activo o de beacons. |
| PC3 | RX 5G. |
| PC4 | RX WiFi. |
| PC5 | Orquestación, destino de datos y ejecución del modelo externo para fusión/inferencia. |
| X410 | Sustitución física conjunta de los roles RX lógicos PC3 y PC4 en recepción sincronizada. |

## Resumen

| Experimento | Fuente 5G | Fuente WiFi | Recepción | Sincronización prevista | Estado actual |
|---|---|---|---|---|---|
| `nosync_passive` | 5G comercial, SSB | PC2, beacons WiFi | PC3 + PC4 separados | Ninguna; combinación aproximada en PC5 | Smoke conjunto implementado con dos B210 no comparables; validación RF live y timestamps canónicos pendientes |
| `nosync_active` | PC1, BF-like 5G | PC2, BF WiFi | PC3 + PC4 separados | Ninguna; combinación aproximada en PC5 | Señales activas todavía en desarrollo |
| `sync_reception_passive` | 5G comercial, SSB | PC2, beacons WiFi | Dos canales del X410 | Reloj de recepción común y timestamps de dispositivo | Captura X410 prototipo; integración y dataset conjunto pendientes |
| `sync_reception_active` | PC1, BF-like 5G | PC2, BF WiFi | Dos canales del X410 | Reloj de recepción común y timestamps de dispositivo | Depende de RX X410 integrado y señales activas pendientes |
| `sync_all_passive` | 5G comercial, SSB de referencia | PC2, beacons WiFi programados | PC3 + PC4 sincronizados | PCs sincronizados y beacon programado desde llegadas SSB | Concepto definido; scheduling y coordinación pendientes |
| `sync_all_active` | PC1, BF-like 5G | PC2, BF WiFi programado | PC3 + PC4 sincronizados | Sincronización total; posible simplificación inicial por cable | Concepto definido; componentes activos y coordinación pendientes |

## 1. `nosync_passive`

El 5G comercial transmite SSBs y PC3 ejecuta el receptor 5G. PC2 transmite beacons WiFi y PC4 ejecuta el receptor WiFi. Los dos receptores trabajan sin una referencia temporal compartida y transmiten sus resultados a PC5 en modo *best effort*. La fusión futura asumirá de forma aproximada que los datos recibidos juntos corresponden a instantes próximos.

Corrección confirmada: PC2 transmite **beacons WiFi**, no SSBs.

## 2. `nosync_active`

PC1 transmite una waveform BF-like 5G y PC2 transmite la señal BF WiFi. PC3 y PC4 reciben sus bandas respectivas sin sincronización común y remiten los resultados a PC5 siguiendo el mismo criterio aproximado del caso pasivo.

Los módulos activos necesarios están aún en desarrollo y este repositorio padre no completa su implementación en la fase actual.

## 3. `sync_reception_passive`

Se conservan como fuentes el 5G comercial y los beacons WiFi de PC2. Los receptores lógicos PC3 y PC4 se sustituyen físicamente por dos canales de un X410 conectado a un servidor. Ambos canales utilizan el mismo reloj del USRP, lo que permite asociar las capturas a una referencia temporal común antes de enviarlas a PC5.

El código actual demuestra recepción multicanal y timestamps del X410, pero todavía no genera el resultado procesado y conjunto del experimento.

## 4. `sync_reception_active`

La recepción conjunta con X410 se mantiene, sustituyendo las fuentes pasivas por las transmisiones activas de PC1 y PC2. Comparte las dependencias pendientes tanto de la recepción multibanda procesada como de las waveforms activas.

## 5. `sync_all_passive`

PC2, PC3 y PC4 estarán sincronizados temporalmente. El 5G comercial permanece como referencia externa. PC3 detectará al menos dos SSBs, observará sus tiempos y calculará futuros instantes en los que PC2 deberá transmitir beacons WiFi alineados con oportunidades SSB.

La descripción contempla SSBs aproximadamente cada 20 ms y beacons aproximadamente cada 100 ms. También plantea distribuir distintos offsets SSB entre varios emisores, hasta cinco dentro del ciclo de 100 ms. Esta lógica es objetivo futuro y no se implementa en esta fase.

## 6. `sync_all_active`

Repite el principio de sincronización total con transmisiones activas. Como simplificación inicial de laboratorio, los transmisores podrían sincronizarse mediante cable. El funcionamiento objetivo sigue siendo que el receptor 5G determine y comunique el instante de transmisión WiFi.

El roadmap aprobado fija 10 MHz/PPS y un epoch USRP común para la programación RF, manteniendo como métrica principal la alineación observada en el X410. El hardware concreto, el protocolo de avisos y la tolerancia se cerrarán después de caracterizar `sync_reception`; véase `DEVELOPMENT_PLAN.md`.

## Elementos deliberadamente pendientes

- Hardware concreto de distribución 10 MHz/PPS y calibración física.
- Tolerancia final, que se fijará después de caracterizar sesgo y jitter.
- Formato definitivo solicitado por el equipo del modelo más allá del contrato mínimo aprobado.
- Política de ventanas/emparejamiento dentro del futuro módulo de modelo; la captura conservará timelines independientes.
- Protocolo de control entre el receptor 5G y el transmisor WiFi.
- Interfaces necesarias entre el framework padre y los submódulos.
