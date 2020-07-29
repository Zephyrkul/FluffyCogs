@echo off

if "%1"=="" goto help

REM This allows us to expand variables at execution
setlocal ENABLEDELAYEDEXPANSION

REM This will set PYFILES as a list of tracked .py files
set PYFILES=
for /F "tokens=* USEBACKQ" %%A in (`git ls-files "*.py"`) do (
    set PYFILES=!PYFILES! %%A
)

REM This will set PYIFILES as a list of tracked .pyi? files
set PYIFILES=
for /F "tokens=* USEBACKQ" %%A in (`git ls-files "*.py" "*.pyi"`) do (
    set PYIFILES=!PYIFILES! %%A
)


goto %1

:lint
flake8 --count --select=E9,F7,F82 --show-source !PYIFILES!
goto :eof

:stylecheck
autoflake --check --imports aiohttp,discord,redbot !PYFILES! || goto :eof
isort --check-only !PYFILES! || goto :eof
black --check !PYIFILES!
goto :eof

:reformat
autoflake --in-place --imports=aiohttp,discord,redbot !PYFILES! || goto :eof
isort !PYFILES! || goto :eof
black !PYIFILES!
goto :eof

:help
echo Usage:
echo   make ^<command^>
echo.
echo Commands:
echo   lint                         Lints .py files using flake8
echo   stylecheck                   Check that all .py files meet style guidelines.
echo   reformat                     Reformat all .py files being tracked by git.
