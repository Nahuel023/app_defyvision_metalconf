@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64
set DISTUTILS_USE_SDK=1
set MSSdk=1
"%~dp0..\\.venv\Scripts\python.exe" "%~dp0cython_setup.py" build_ext
