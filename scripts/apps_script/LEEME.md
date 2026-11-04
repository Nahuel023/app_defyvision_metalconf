# Estado de la maquina en Google Sheets

La app manda un heartbeat (`src/metrics/status_reporter.py`) a un Web App de
Apps Script, que pinta la casilla **Semáforo** del equipo **EQ-014 (Metalconf —
Visión Artificial Prensa)** en la hoja **Equipos** de la planilla
`Cartera_PostVenta_Defymotion`:

| Color    | Texto     | Cuando |
|----------|-----------|--------|
| Verde    | `EN USO`  | algun scanner en RUNNING |
| Amarillo | `REPOSO`  | app abierta, ningun scanner en RUNNING (IDLE/STOPPED/FAULT/ERROR) |
| Rojo     | `APAGADA` | se cerro la app, o pasaron `STALE_MIN` minutos sin heartbeat (PC apagada, corte de luz, sin internet) |

La fila se busca por el ID `EQ-014`, asi que se puede ordenar la tabla sin
romper nada. La hora de la ultima senal y el estado de cada scanner quedan en
la **nota** de la celda (pasar el mouse por encima).

> La celda `Semáforo` de EQ-014 tenia la formula de postventa (dias sin
> contacto). Al primer heartbeat se reemplaza por el estado en vivo **solo en
> esa fila**; las demas filas no se tocan.

## Instalacion (una sola vez)

1. Abrir la planilla → **Extensiones → Apps Script**.
2. Pegar el contenido de `estado_maquina.gs` y guardar.
3. (Opcional) Poner un texto en `TOKEN`.
4. Elegir la funcion `setup` en el menu y pulsar **Ejecutar**. Aceptar los permisos.
   Verifica que exista EQ-014 y crea el disparador de cada minuto que marca APAGADA.
5. **Implementar → Nueva implementacion → Aplicacion web**:
   - Ejecutar como: **Yo**
   - Quien tiene acceso: **Cualquier usuario**
   - Copiar la URL que termina en `/exec`.
6. En la PC de planta, editar `config/app.yaml`:

   ```yaml
   status_report:
     enabled: true
     url: "https://script.google.com/macros/s/XXXX/exec"
     token: ""          # mismo valor que TOKEN del script
     machine: "Metalconf"   # debe existir en MACHINES del script
   ```

7. Reiniciar la app. En unos segundos la casilla cambia de color.

Si se modifica el script, hay que publicar una **nueva version** de la
implementacion (Implementar → Gestionar implementaciones → Editar → Nueva
version) para que la URL `/exec` use el codigo nuevo.

## Agregar otro equipo

Sumar una entrada en `MACHINES` del script (`'NombreEnAppYaml': 'EQ-0XX'`) y
poner ese nombre en `status_report.machine` de la app de ese equipo.

## Notas

- La PC de planta necesita salida a internet (`script.google.com`).
- Sin internet la app sigue funcionando normal; solo registra un aviso en el log.
