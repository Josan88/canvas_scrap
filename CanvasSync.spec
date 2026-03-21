# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

opendataloader_datas = collect_data_files('opendataloader_pdf')
docling_metadata = copy_metadata('docling')
docling_core_metadata = copy_metadata('docling-core')
docling_ibm_models_metadata = copy_metadata('docling-ibm-models')
docling_parse_metadata = copy_metadata('docling-parse')
all_datas = opendataloader_datas + docling_metadata + docling_core_metadata + docling_ibm_models_metadata + docling_parse_metadata

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=all_datas,
    hiddenimports=[
        'reportlab',
        'reportlab.lib',
        'reportlab.lib.pagesizes',
        'reportlab.lib.styles',
        'reportlab.lib.units',
        'reportlab.platypus',
        'reportlab.pdfgen',
        'reportlab.pdfgen.canvas',
        'bs4',
        'requests',
        'configparser',
        'opendataloader_pdf.hybrid_server',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CanvasSync',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
