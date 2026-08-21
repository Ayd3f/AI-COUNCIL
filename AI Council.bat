@echo off
rem Запуск десктопного приложения AI Council.
rem Создайте ярлык на этот файл — на рабочем столе или в меню «Пуск».
rem Окно консоли не появляется: используется pythonw.exe.

set "ROOT=%~dp0"

if exist "%ROOT%.venv\Scripts\pythonw.exe" (
    start "AI Council" "%ROOT%.venv\Scripts\pythonw.exe" "%ROOT%ai_council.py"
) else (
    echo Окружение не найдено. Выполните один раз в каталоге проекта:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r desktop\requirements.txt
    pause
)
