Set WshShell = CreateObject("WScript.Shell")

' Adicionando o aviso com quebra de linha
MsgBox "Estamos Atualizando os Mapeamentos!" & vbCrLf & "Aguarde 2 minutos para sincronismo completo!", vbInformation, "Aviso"

WshShell.Run "taskkill /f /im msedge.exe", 0, True
WshShell.Run "taskkill /f /im msedgedriver.exe", 0, True
WshShell.Run "taskkill /f /im powershell.exe", 0, True
Const HKEY_CURRENT_USER = &H80000001
Set objRegistry = GetObject("winmgmts:\\.\root\default:StdRegProv")
objRegistry.SetDWORDValue HKEY_CURRENT_USER, "Software\Microsoft\Windows\CurrentVersion\Internet Settings\Zones\1", "1A00", 0
objRegistry.SetDWORDValue HKEY_CURRENT_USER, "Software\Microsoft\Windows\CurrentVersion\Internet Settings\Zones\2", "1A00", 0
WshShell.Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\Webdav\OneDriveMapper.ps1", 0, True
Set WshShell = Nothing
