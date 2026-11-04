# Estado de la maquina en Google Sheets

La app manda un heartbeat (`src/metrics/status_reporter.py`) a un Web App de
Apps Script, que pinta la casilla de estado:

| Color    | Estado    | Cuando |
|----------|-----------|--------|
| Verde    | `EN_USO`  | algun scanner en RUNNING |
| Amarillo | `REPOSO`  | app abierta, ningun scanner en RUNNING (IDLE/STOPPED/FAULT/ERROR) |
| Rojo     | `APAGADA` | se cerro la app, o pasaron `STALE_MIN` minutos sin heartbeat (PC apagada, corte de luz, sin internet) |

## Instalacion (una sola vez)

1. Abrir la planilla de Google Sheets → **Extensiones → Apps Script**.
2. Pegar el contenido de `estado_maquina.gs` y guardar.
3. (Opcional) Poner un texto en `TOKEN`.
4. Elegir la funcion `setup` en el menu y pulsar **Ejecutar**. Aceptar los permisos.
   Crea la hoja `Estado` y el disparador de cada minuto que marca APAGADA.
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
     machine: "Metalconf"
   ```

7. Reiniciar la app. En unos segundos aparece la fila con el estado.

Si se modifica el script, hay que publicar una **nueva version** de la
implementacion (Implementar → Gestionar implementaciones → Editar → Nueva
version) para que la URL `/exec` use el codigo nuevo.

## Notas

- La PC de planta necesita salida a internet (`script.google.com`).
- Sin internet la app sigue funcionando normal; solo registra un aviso en el log.
- Para mostrar el estado en otra hoja, usar una formula como `=Estado!B2`
  y formato condicional por texto (EN_USO / REPOSO / APAGADA).
