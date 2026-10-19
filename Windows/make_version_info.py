"""Writes version_info.txt (the Windows exe's Properties → Details tab) from version.py."""
from version import __version__

t = tuple(int(x) for x in __version__.split(".")) + (0,)
open("version_info.txt", "w", encoding="utf-8").write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'HomeLab Management'),
    StringStruct('FileDescription', 'HomeLab Management'),
    StringStruct('FileVersion', '{__version__}'),
    StringStruct('ProductName', 'HomeLab Management'),
    StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""")
