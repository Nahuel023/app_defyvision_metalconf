# metalconf.spec — PyInstaller build spec for production mode (src.main run)
block_cipher = None

a = Analysis(
    ['run_production.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'pymodbus',
        'pymodbus.client',
        'pymodbus.client.tcp',
        'pymodbus.framer',
        'pymodbus.framer.socket',
        'pymodbus.pdu',
        'PyQt6',
        'PyQt6.QtWidgets',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.sip',
        'cv2',
        'numpy',
        'yaml',
        'pymcprotocol',
        'matplotlib',
        'matplotlib.backends.backend_agg',
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
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
