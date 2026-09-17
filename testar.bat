@echo off
REM testar.bat - menu de testes locais do imovel-bot.
REM Nao precisa de Telegram configurado (exceto opcao 6, que usa
REM credenciais fake so pra passar da validacao de config -- dry-run
REM nunca envia mensagem de verdade).
REM Usa o Python do venv em %USERPROFILE%\venvs\imovel-bot direto
REM pelo caminho completo (sem precisar rodar Activate.ps1 antes).

setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set VENV_PY=%USERPROFILE%\venvs\imovel-bot\Scripts\python.exe

cd /d "%~dp0"

if not exist "%VENV_PY%" (
    echo.
    echo [ERRO] Venv nao encontrado em: %VENV_PY%
    echo Crie o venv primeiro:
    echo    python -m venv "%USERPROFILE%\venvs\imovel-bot"
    echo    "%VENV_PY%" -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

:menu
cls
echo ================================================
echo   imovel-bot - testes locais
echo ================================================
echo.
echo   1. Testar scrapers (OLX+ZAP) - Sao Paulo capital
echo   2. Testar scrapers (OLX+ZAP) - Campinas
echo   3. Testar scrapers (OLX+ZAP) - Piracicaba
echo   4. Testar scrapers - todas as regioes (1+2+3)
echo   5. Rodar calibragem (Atlas ITBI + mediana movel)
echo   6. Rodar bot completo em modo dry-run (nao envia Telegram)
echo   7. Sair
echo.
set /p opcao="Escolha uma opcao: "

if "%opcao%"=="1" goto scraper_sp
if "%opcao%"=="2" goto scraper_campinas
if "%opcao%"=="3" goto scraper_piracicaba
if "%opcao%"=="4" goto scraper_todas
if "%opcao%"=="5" goto calibragem
if "%opcao%"=="6" goto dryrun
if "%opcao%"=="7" goto fim
goto menu

:scraper_sp
"%VENV_PY%" testar_scraper.py --regiao sp_capital
goto fim_etapa

:scraper_campinas
"%VENV_PY%" testar_scraper.py --regiao campinas
goto fim_etapa

:scraper_piracicaba
"%VENV_PY%" testar_scraper.py --regiao piracicaba
goto fim_etapa

:scraper_todas
"%VENV_PY%" testar_scraper.py --regiao all
goto fim_etapa

:calibragem
"%VENV_PY%" -m calibragem.calibrar
goto fim_etapa

:dryrun
echo.
echo [INFO] --dry-run nao envia nada pro Telegram, so loga o que
echo seria enviado. Mas main.py exige TELEGRAM_TOKEN e
echo TELEGRAM_CHANNEL_MERCADO configurados pra nem rodar -- se essas
echo variaveis nao estiverem definidas nesta sessao, usamos valores
echo fake so pra passar da validacao.
echo.
if "%TELEGRAM_TOKEN%"=="" set TELEGRAM_TOKEN=123:dry-run-fake-token
if "%TELEGRAM_CHANNEL_MERCADO%"=="" set TELEGRAM_CHANNEL_MERCADO=-1000000000000
"%VENV_PY%" main.py --dry-run
goto fim_etapa

:fim_etapa
echo.
pause
goto menu

:fim
endlocal
exit /b 0
