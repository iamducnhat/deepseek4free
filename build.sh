#!/usr/bin/env bash
set -e

echo "======================================"
echo " Transparent Build Script"
echo "======================================"
echo "This script compiles the Python wrapper into a standalone binary."

rm -rf build_venv
python3 -m venv build_venv
source build_venv/bin/activate

pip install pyinstaller curl-cffi==0.8.1b9 wasmtime numpy nodriver drissionpage setuptools macholib

pyinstaller --onefile --collect-all wasmtime --collect-all curl_cffi --add-data "dsk:dsk" ask_deepseek.py
mkdir -p bin
cp dist/ask_deepseek bin/

rm -rf build_venv build dist *.spec
echo "Done! Binary is in bin/ask_deepseek"
