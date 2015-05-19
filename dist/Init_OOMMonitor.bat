@echo off

pushd "%CD%"
CD /D "%~dp0"

cls

::管理员账户
:: whoami /user |find "-500" >nul 2>nul
::if not errorlevel 1 (goto AdministratorAccount) else goto OtherAccount


::管理员权限运行
set TempFile_Name=%SystemRoot%\System32\BatTestUACin_SysRt%Random%.batemp
echo %TempFile_Name% 1>nul

( echo "BAT Test UAC in Temp" >%TempFile_Name% ) 1>nul 2>nul

 if exist %TempFile_Name% (
 echo use administrator privilege
 del /f /q %TempFile_Name% 
 goto main
) else (
 echo not use administrator privilege
 goto OtherAccount
)



:OtherAccount
%1 %2
ver|find "5.">nul&&goto :st
mshta vbscript:createobject("shell.application").shellexecute("%~s0","goto :st","","runas",1)(window.close)&goto :eof

:st
copy "%~0" "%windir%\system32\"
goto :main



:main
sc query OOMMonitor state=all >nul 2>nul
:: Is service OOMMonitor exist ? exits:notexist
if not errorlevel 1 (goto exist) else goto notexist

:exist
echo Exist OOMMonitor Service.

echo :%~dp0
::Is running? Y:N
sc query OOMMonitor >nul 2>nul
::IF ERRORLEVEL 0 ECHO SUCCESS
if not errorlevel 1 (
    ::OOMMonitor didn't run
    echo OOMMonitor Didn't Run
    ) else (
    echo OOMMonitor Is Running, Try To Stop It.
    net stop OOMMonitor
    echo OOMMonitor Stop Success.
) 

echo Reinstall OOMMonitor...
OOMMonitor.exe -remove
OOMMonitor.exe -install -auto
echo Reinstall OOMMonitor Success.
goto :ScheduleTask


:notexist
echo NotExist OOMMonitor Service .
OOMMonitor.exe -install -auto
echo OOMMonitor Service Install Success .


:ScheduleTask

::echo %~dp0Start_OOMMonitor.bat

SCHTASKS /Create /RU "System" /SC DAILY /TN OOMMonitor /TR %~dp0Start_OOMMonitor.bat /RI 60 /DU 24:00 /K /F /RL HIGHEST

pause...
goto :eof
