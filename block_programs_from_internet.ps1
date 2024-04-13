# 检查是否以管理员权限运行
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
{
    # 获取脚本的完整路径
    $scriptPath = $MyInvocation.MyCommand.Definition
    # 重新以管理员权限启动脚本
    Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"" -Verb RunAs
    exit
}

# 路径设置为主人想要禁止网络访问的程序所在的文件夹
$FolderPath = "C:\Program Files (x86)\360\360zip"

# 获取该文件夹下所有的.exe文件
$Executables = Get-ChildItem -Path $FolderPath -Filter *.exe -Recurse

$taskName = "360"

# 为每个.exe文件创建防火墙规则
foreach ($exe in $Executables) {
    $ruleNameOut = $taskName + "Block_Outbound_" + $exe.Name
    $ruleNameIn = $taskName + "Block_Inbound_" + $exe.Name

    # 创建防火墙规则禁止程序出站访问
    New-NetFirewallRule -DisplayName $ruleNameOut -Direction Outbound -Program $exe.FullName -Action Block
    # 创建防火墙规则禁止程序入站访问
    New-NetFirewallRule -DisplayName $ruleNameIn -Direction Inbound -Program $exe.FullName -Action Block

    Write-Output "已创建禁止出站和入站的规则，程序：$exe.FullName"
}

Write-Output "所有程序的出站和入站规则都已设置完成，喵～"
# 等待用户按任意键后退出
Write-Output "按任意键退出..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")