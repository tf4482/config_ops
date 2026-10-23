# PyInstaller build: uv run pyinstaller config_ops.spec
a = Analysis(["main.py"], pathex=["src"])
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="config_ops",
    console=True,
    upx=False,
)
