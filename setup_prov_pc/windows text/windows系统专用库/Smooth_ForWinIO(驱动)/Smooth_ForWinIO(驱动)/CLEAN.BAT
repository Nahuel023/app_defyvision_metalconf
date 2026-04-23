@echo off
chcp 65001 >nul 2>nul
@echo off
%1 mshta vbscript:CreateObject("Shell.Application").ShellExecute("cmd.exe","/c %~s0 ::","","runas",1)(window.close)&&exit
echo 当前程序版本:1.0.0.0

sc delete smooth

echo 清理程序运行完成,请按任意键退出......

pause
exit
