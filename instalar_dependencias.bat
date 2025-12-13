@echo off
cd /d %~dp0
call venv\Scripts\activate

echo ===============================
echo Iniciando servidor Flask con python app.py ...
echo URL: http://127.0.0.1:5000
echo (Cerrar con CTRL + C)
echo ===============================

python app.py

echo.
echo Servidor detenido. Presiona una tecla para cerrar.
pause >nul

