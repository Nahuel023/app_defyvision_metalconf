"""
Compila los módulos críticos de src/ a extensiones nativas .pyd con Cython.
Ejecutar desde el directorio raíz del proyecto (lo llama build_exe.ps1).
"""
from setuptools import setup
from Cython.Build import cythonize

PROTECTED = [
    "src/utils/license.py",
]

if __name__ == "__main__":
    setup(
        ext_modules=cythonize(
            PROTECTED,
            compiler_directives={"language_level": "3"},
            nthreads=0,
        )
    )
