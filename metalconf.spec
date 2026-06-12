# metalconf.spec
# PyInstaller spec para metalconf.exe
# Ejecutar desde el directorio raiz con:
#   powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['run_production.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('config',           'config'),
        ('data\\patterns',   'data\\patterns'),
    ],
    hiddenimports=[
        'src.main',
        'src.inspection',
        'src.controller.system',
        'src.controller.scanner_controller',
        'src.plc.client',
        'src.plc.io_map',
        'src.vision.camera',
        'src.vision.inspector',
        'src.pipeline.align_edge',
        'src.pipeline.preprocess',
        'src.pipeline.detect_holes',
        'src.pipeline.compare',
        'src.pipeline.annotate',
        'src.pipeline.machine_stop',
        'src.pipeline.event_recorder',
        'src.pipeline.grid_fitting',
        'src.pipeline.edge_centering',
        'src.patterns.pattern_build',
        'src.patterns.pattern_io',
        'src.patterns.roi',
        'src.utils.config',
        'src.utils.logger',
        'src.utils.state',
        'src.utils.model_names',
        'src.utils.camera_config',
        'src.ui.operator',
        'src.ui.service',
        'src.ui.login_dialog',
        'src.ui.tolerance_window',
        'src.ui.metrics_window',
        'src.ui.frame_viewer',
        'src.io.save_results',
        'src.io.load_images',
        'src.metrics.recorder',
        'scipy',
        'scipy.optimize',
        'scipy.optimize._lsap',
        'scipy.optimize._lsap_doc',
        'pymodbus',
        'pymodbus.client',
        'pymodbus.client.tcp',
        'PyQt6',
        'PyQt6.QtWidgets',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'cv2',
        'numpy',
        'yaml',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='metalconf',
    icon='assets\\defyvision_logo.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='metalconf',
)
