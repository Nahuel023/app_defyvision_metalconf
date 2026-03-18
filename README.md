# app_defyvision_metalconf

Sistema de inspeccion visual para chapa punzonada. Detecta agujeros, compara contra un patron por modelo y decide `OK/NOK`.

## Estado del proyecto

- Backend de inspeccion funcional para imagenes y secuencias de frames.
- Patron configurable por modelo en `data/patterns/<modelo>/holes.json`.
- Tolerancias configurables en `config/tolerancias.yaml`.
- Modo servicio/desarrollo en `tkinter`.
- Modo operador en `PyQt6`.
- Decision temporal implementada: declara `NOK` solo si aparece en `N` frames consecutivos.

## Requisitos

- Windows + PowerShell
- Python con launcher `py`
- Git
- FFmpeg para trabajar con video

## Instalacion rapida en Windows

Desde la raiz del proyecto:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

Ese script hace esto:

1. Crea `.venv` si no existe.
2. Instala dependencias Python desde `requirements.txt`.
3. Instala `ffmpeg` con `winget` si no esta disponible.
4. Refresca el `PATH` de la sesion actual.

Tambien hay wrapper `.bat`:

```powershell
.\scripts\setup_windows.bat
```

## Actualizar el proyecto en otra PC

Cuando subas cambios desde esta PC, en la otra PC alcanza con correr:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1
```

Ese script:

1. Hace `git pull --ff-only`.
2. Reinstala dependencias Python si cambiaron.
3. Verifica `ffmpeg`.

Tambien hay wrapper `.bat`:

```powershell
.\scripts\update_windows.bat
```

## Nota sobre `python was not found`

En algunas instalaciones de Windows, `python` puede fallar por los aliases de Microsoft Store.
Por eso los scripts usan:

- `py` para crear el entorno virtual
- `.\.venv\Scripts\python.exe` para ejecutar la aplicacion

## Primer arranque en otra PC

1. Clonar el repositorio:

```powershell
git clone https://github.com/Nahuel023/app_defyvision_metalconf.git
cd app_defyvision_metalconf
```

2. Ejecutar setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

3. Abrir el proyecto en VS Code:

```powershell
code .
```

4. Seleccionar el interprete:

```text
.\.venv\Scripts\python.exe
```

5. Revisar `config/tolerancias.yaml` si cambia camara, lente o iluminacion.

6. Regenerar patron si el setup optico de la nueva PC no coincide con el original:

```powershell
.\.venv\Scripts\python.exe -m src.main build-pattern --model modelo_A --img "data/input/modeloA_OK.jpg"
```

## Modos de uso

### Operador

Interfaz final para operacion basica:

```powershell
.\.venv\Scripts\python.exe -m src.main operator-ui
```

Flujo esperado:

1. Elegir modelo.
2. Elegir carpeta de frames o secuencia preparada.
3. Ejecutar `Analizar`.
4. Usar `Play` / `Stop`.
5. Visualizar estado `OK/NOK` y resultados frame a frame.

### Servicio / Desarrollo

Interfaz de calibracion y pruebas:

```powershell
.\.venv\Scripts\python.exe -m src.main gui
```

Permite:

1. Ajustar tolerancias.
2. Generar patron.
3. Analizar imagen.
4. Analizar carpeta.
5. Extraer frames desde video.

### Consola

Generar patron:

```powershell
.\.venv\Scripts\python.exe -m src.main build-pattern --model modelo_A --img "data/input/modeloA_OK.jpg"
```

Analizar una imagen:

```powershell
.\.venv\Scripts\python.exe -m src.main run-image --model modelo_A --img "data/input/modeloA_OK.jpg" --save
```

Analizar una carpeta con decision temporal:

```powershell
.\.venv\Scripts\python.exe -m src.main run-folder --model modelo_A --input "data/frames" --fps 5 --save
```

## Video

Extraer frames desde un video con FFmpeg:

```powershell
New-Item -ItemType Directory -Force -Path "data/frames" | Out-Null
ffmpeg -i "data/videos/video.mp4" -vf fps=2 "data/frames/frame_%04d.jpg"
```

## Portabilidad

Para mover el proyecto a otra PC sin problemas:

- No dependas del `.venv` actual: siempre recrealo.
- No subas `data/output/`, `data/frames/` ni videos de prueba pesados.
- Conserva en el repo solo codigo, configuracion y patrones base necesarios.
- Si cambia la camara o iluminacion, recalibra y regenera el patron.

## Archivos importantes

- `src/main.py`: entrada principal.
- `src/inspection.py`: backend de inspeccion.
- `src/gui_app.py`: modo servicio.
- `src/qt_operator_app.py`: modo operador.
- `config/tolerancias.yaml`: parametros de deteccion y decision temporal.
- `data/patterns/`: patrones por modelo.
