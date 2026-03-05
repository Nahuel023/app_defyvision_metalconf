# app_defyvision_metalconf

MVP (CLI) para control de calidad por visión en punzonado: detecta agujeros en imágenes y valida si el patrón coincide con el esperado para cada modelo (OK / NOK).

## Estado del proyecto (Marzo 2026)

- Estado general: `MVP funcional en consola`.
- Flujo implementado:
  - Carga de imágenes (`src/io/load_images.py`).
  - Construcción y lectura de patrones por modelo (`src/patterns/*`).
  - Pipeline de preprocesamiento, detección, comparación y anotación (`src/pipeline/*`).
  - Guardado de resultados y evidencias (`src/io/save_results.py`).
- Ejecución principal: `python -m src.main`.
- Pendiente para próximas etapas:
  - Captura desde cámara en vivo.
  - Integración PLC/alarma.
  - Interfaz gráfica (UI).

## Estructura del proyecto

```text
app_defyvision_metalconf/
├─ config/
│  ├─ app.yaml
│  └─ tolerancias.yaml
├─ data/
│  ├─ input/                  # imágenes de entrada para pruebas
│  ├─ output/                 # resultados generados por el sistema
│  │  ├─ ok/
│  │  ├─ nok/
│  │  └─ debug/
│  └─ patterns/               # patrones por modelo (A/B/C)
├─ scripts/                   # atajos CLI (wrappers)
│  ├─ build_pattern.py
│  ├─ run_folder.py
│  └─ run_image.py
├─ src/                       # paquete Python principal (core)
│  ├─ main.py                 # entrypoint CLI (python -m src.main ...)
│  ├─ io/
│  ├─ patterns/
│  ├─ pipeline/
│  └─ utils/
├─ requirements.txt
├─ .gitignore
└─ README.md
```

## Inicialización del entorno (Windows / PowerShell)

1. Crear entorno virtual:

```powershell
python -m venv .venv
```

2. Habilitar scripts en la sesión y activar el entorno:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

3. Instalar dependencias:

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

## Instalación desde GitHub

1. Clonar el repositorio:

```powershell
git clone https://github.com/Nahuel023/app_defyvision_metalconf.git
cd <repositorio>
```

2. Crear y activar entorno virtual:

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

3. Instalar dependencias:

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

## Ejecución rápida

Procesar una carpeta de imágenes:

```powershell
python scripts/run_folder.py --help
```

Procesar una imagen puntual:

```powershell
python scripts/run_image.py --help
```

Construir patrón de modelo:

```powershell
python scripts/build_pattern.py --help
```
