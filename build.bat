@echo off
REM Builds Yoink into dist\Yoink\Yoink.exe.
REM yt-dlp.exe and ffmpeg are NOT baked into the exe. They go in dist\Yoink\bin
REM so yt-dlp can update itself from inside the app.
REM Run this from the folder holding yoink.py.

setlocal
cd /d "%~dp0"

echo.
echo ===============================================
echo   Yoink build
echo ===============================================
echo.

if not exist "yoink.py" (
    echo yoink.py is not in this folder.
    echo Put build.bat next to yoink.py and run it from there.
    goto :fail
)

if not exist "bin" mkdir "bin"

REM --- ffmpeg ---------------------------------------------------------------
REM First try VideoCut's copy next door, then ffmpeg on PATH.

if exist "bin\ffmpeg.exe" if exist "bin\ffprobe.exe" goto :have_ffmpeg
call :grab_ffmpeg

:have_ffmpeg
if not exist "bin\ffmpeg.exe" goto :no_ffmpeg
if not exist "bin\ffprobe.exe" goto :no_ffmpeg

"bin\ffmpeg.exe" -version >nul 2>&1
if errorlevel 1 (
    echo bin\ffmpeg.exe is there but will not run.
    echo Delete it and copy ffmpeg.exe and ffprobe.exe into bin by hand.
    goto :fail
)
echo ffmpeg: ready

REM --- yt-dlp ---------------------------------------------------------------

if exist "bin\yt-dlp.exe" goto :update_ytdlp
echo Downloading yt-dlp.exe...
curl -L --fail -o "bin\yt-dlp.exe" "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
if errorlevel 1 goto :no_ytdlp
goto :have_ytdlp

:update_ytdlp
echo Checking yt-dlp for updates...
"bin\yt-dlp.exe" -U

:have_ytdlp
echo yt-dlp: ready
goto :python

:no_ytdlp
if exist "bin\yt-dlp.exe" del "bin\yt-dlp.exe"
echo.
echo Could not download yt-dlp.exe. Building anyway - Yoink will offer
echo to download it the first time it starts.
echo.

REM --- python ---------------------------------------------------------------
:python
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 goto :fail
)

python -c "import qrcode" 2>nul
if errorlevel 1 (
    echo Installing qrcode (for the phone pairing QR code^)...
    python -m pip install qrcode
    if errorlevel 1 goto :fail
)

echo.
echo Packaging. This takes a minute.
echo.

python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --name "Yoink" ^
    yoink.py

if errorlevel 1 goto :fail

echo.
echo Copying bin folder next to the exe...
if not exist "dist\Yoink\bin" mkdir "dist\Yoink\bin"
copy /y "bin\ffmpeg.exe" "dist\Yoink\bin\" >nul
copy /y "bin\ffprobe.exe" "dist\Yoink\bin\" >nul
if exist "bin\yt-dlp.exe" copy /y "bin\yt-dlp.exe" "dist\Yoink\bin\" >nul

echo.
echo ===============================================
echo   Done: dist\Yoink\Yoink.exe
echo ===============================================
echo.
echo Move the whole Yoink folder wherever you like. It needs the
echo _internal and bin folders beside the exe.
echo.
goto :end


:grab_ffmpeg
if exist "..\VideoCut\bin\ffmpeg.exe" if exist "..\VideoCut\bin\ffprobe.exe" (
    echo Copying ffmpeg from VideoCut\bin...
    copy /y "..\VideoCut\bin\ffmpeg.exe" "bin\ffmpeg.exe" >nul
    copy /y "..\VideoCut\bin\ffprobe.exe" "bin\ffprobe.exe" >nul
    exit /b 0
)
set "FOUND_FFMPEG="
set "FOUND_FFPROBE="
for /f "delims=" %%i in ('where ffmpeg 2^>nul') do set "FOUND_FFMPEG=%%i"
for /f "delims=" %%i in ('where ffprobe 2^>nul') do set "FOUND_FFPROBE=%%i"
if not defined FOUND_FFMPEG exit /b 1
if not defined FOUND_FFPROBE exit /b 1
echo Found ffmpeg on PATH, copying it into bin\
copy /y "%FOUND_FFMPEG%" "bin\ffmpeg.exe" >nul
copy /y "%FOUND_FFPROBE%" "bin\ffprobe.exe" >nul
exit /b 0


:no_ffmpeg
echo.
echo Cannot find ffmpeg.
echo.
echo Easiest fix, then run this script again:
echo     winget install Gyan.FFmpeg
echo.
echo PATH only refreshes in a NEW terminal, so close this window and
echo open a fresh one after installing.
echo.
echo Or copy ffmpeg.exe and ffprobe.exe into the bin folder by hand.
goto :fail


:fail
echo.
echo Build stopped.

:end
echo.
pause
endlocal
