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

Para `nosync_passive` solamente, el despliegue físico actual reasigna esos roles: PC1 es RX 5G, PC2 es RX WiFi y PC3PC4 es TX WiFi N310. Los otros experimentos conservan las convenciones anteriores.

## Resumen

| Experimento | Fuente 5G | Fuente WiFi | Recepción | Sincronización prevista | Estado actual |
|---|---|---|---|---|---|
| `nosync_passive` | 5G comercial, SSB | PC3PC4, beacons WiFi N310 | PC1 RX 5G + PC2 RX WiFi | Ninguna; asociación NTP operacional opcional en PC5 | Implementado, validado y usado para datasets; fallos RF se preservan como intentos fallidos |
| `nosync_active` | PC1, BF-like 5G | PC2, BF WiFi | PC3 + PC4 separados | Ninguna; combinación aproximada en PC5 | Señales activas todavía en desarrollo |
| `sync_reception_passive` | 5G comercial, SSB | PC2, beacons WiFi | Dos canales del X410 | Reloj de recepción común y timestamps de dispositivo | Captura X410 prototipo; integración y dataset conjunto pendientes |
| `sync_reception_active` | PC1, BF-like 5G | PC2, BF WiFi | Dos canales del X410 | Reloj de recepción común y timestamps de dispositivo | Depende de RX X410 integrado y señales activas pendientes |
| `sync_all_passive` | 5G comercial, SSB de referencia | PC2, beacons WiFi programados | PC3 + PC4 sincronizados | PCs sincronizados y beacon programado desde llegadas SSB | Concepto definido; scheduling y coordinación pendientes |
| `sync_all_active` | PC1, BF-like 5G | PC2, BF WiFi programado | PC3 + PC4 sincronizados | Sincronización total; posible simplificación inicial por cable | Concepto definido; componentes activos y coordinación pendientes |

## 1. `nosync_passive`

El 5G comercial transmite SSBs y PC1 ejecuta el receptor 5G. PC3PC4 transmite beacons WiFi con un N310 y PC2 ejecuta el receptor WiFi. Los B210 de PC1 y PC2 reinician épocas UHD locales independientes y no comparten PPS, 10 MHz, tiempo ni coherencia de fase.

Cada evento conserva los ticks exactos de su USRP local: comienzo del bloque más offset del detector. PC5 publica primero un dataset raw sin pares. Un adaptador `nearest-ntp` separado puede proyectar observaciones a UTC desde anchors monotónicos/realtime y telemetría NTP, pero el resultado se etiqueta como asociación operacional aproximada y nunca como alineación de adquisición.

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

## Dirección BF multi-AP referenciada a 5G

Esta línea no añade automáticamente un séptimo experimento ni cambia la
definición original de los seis. Es la arquitectura seleccionada para concretar
la coordinación WiFi BF de las fases `sync_all`; queda pendiente decidir el
nombre del perfil y su mapeo científico exacto.

Se usarán dos AP sin backhaul de coordinación. Cada AP estará compuesto por:

- B210 para recepción de la celda 5G comercial.
- X410 para WiFi 4×4 TX/RX.
- Referencia 10 MHz/PPS y epoch local determinista entre su B210 y X410, o una
  transformación temporal calibrada equivalente.

AP A y AP B no compartirán referencia física en el experimento final. Ambos
adquirirán la misma identidad de ciclo mediante PBCH/MIB y mantendrán el
heartbeat mediante PSS/SSS. Una agenda provisionada asignará roles
complementarios, por ejemplo A-TX/B-RX y después A-RX/B-TX. Las acciones se
armarán para el ciclo completo siguiente con comandos UHD temporizados.

La referencia inicial de laboratorio —5 ms de offset, 2 ms de ventana, diez
paquetes y 200 µs entre comienzos— es parametrizable. El objetivo es alinear
slots y timestamps; no se presupone coherencia de fase RF entre AP.

El banco `testmultiband` demuestra calibración/steering y matrices H 2×2/4×4
con tono estrecho en un X410. El perfil `wifi_bf_like` demuestra HE NDP-like
SISO 20/40 MHz. Todavía falta unir ambas capacidades, mapear B210↔X410,
coordinar dos AP y validar sensing/Doppler. Véase
`BF_MULTI_AP_SYNC_ARCHITECTURE.md`.

El primer hito no usará todavía B210. Dos X410 independientes —uno por AP—
dedicarán dos cadenas a WiFi 2×2 TDD y otra a RX 5G. Esta configuración comparte
timekeeper solo dentro de cada AP y permite comprobar el algoritmo OTA de
epoch, heartbeat y scheduling antes de añadir la transformación B210↔X410. Se
acepta que el sensing 2×2 sea inferior al 4×4, porque el criterio de esta fase es
temporal. Tras validarlo se migrará a la arquitectura final B210+X410.
