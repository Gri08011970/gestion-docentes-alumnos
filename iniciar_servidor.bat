@echo off
REM Iniciar servidor Flask del sistema Escuela 91

REM Ir al tp://127.0.0.1:5000directorio donde está este .bat
cd /d %~dp0

REM Activar entorno virtual
call venv\Scripts\activate


REM Variables de entorno para Flask
set FLASK_APP=app.py
set FLASK_ENV=development

echo ===============================
echo Iniciando servidor Flask...
echo URL: ht
echo (Cerrar con CTRL + C)
echo ===============================

REM Levantar servidor
flask run

REM Mantener ventana abierta si flask se cae por error
echo.
echo Servidor detenido. Presiona una tecla para cerrar.
pause >nul
