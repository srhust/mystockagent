# 设置 Windows 用户级系统代理绕行白名单 (ProxyOverride)
# 作用: 把行情数据源与国内常见站点加入代理"例外"列表, 这些域名直连不走代理
# 已内置你原有的条目(知乎/京东/ B站/讯飞等)与内网段, 自动去重合并
#
# 用法(在普通 PowerShell 窗口运行, 无需管理员):
#   powershell -ExecutionPolicy Bypass -File scripts\set_proxy_bypass.ps1            # 写入白名单
#   powershell -ExecutionPolicy Bypass -File scripts\set_proxy_bypass.ps1 -Restore   # 回滚到修改前
param([switch]$Restore)

$ErrorActionPreference = 'Stop'
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
$backup = Join-Path $PSScriptRoot '..\output\proxy_override_backup.txt'

if ($Restore) {
    if (-not (Test-Path $backup)) { Write-Host "[错误] 找不到备份文件: $backup"; exit 1 }
    $old = (Get-Content $backup -Encoding UTF8 | Where-Object { $_ -and -not $_.StartsWith('#') }) -join ';'
    Set-ItemProperty -Path $key -Name ProxyOverride -Value $old
    Write-Host "=== 已回滚 ==="; Write-Host $old
    exit 0
}

$cur = (Get-ItemProperty $key).ProxyOverride
Write-Host "=== 修改前 ==="; Write-Host $cur

# 首次运行时刷新备份
Set-Content -Path $backup -Value ("# ProxyOverride 备份 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`r`n$cur") -Encoding UTF8

$newDomains = @(
  '<local>',
  # ---- 本项目行情数据源(akshare 实测) ----
  '*.10jqka.com.cn','10jqka.com.cn',                                    # 同花顺: 板块列表/汇总/日K/基本面
  '*.sinajs.cn','sinajs.cn',                                            # 新浪行情 API(个股/指数/期货/美股)
  '*.sina.com.cn','sina.com.cn','*.sina.cn','sina.cn',                  # 新浪财经历史K线/期货主力/沪深300
  '*.sinaimg.cn','*.sinastorage.com',                                   # 新浪图片/存储 CDN
  '*.baidu.com','*.bdstatic.com','*.bdimg.com','*.baidubce.com','*.bcebos.com',  # 百度股市通 PB/PE
  # ---- 财经数据其他常用源 ----
  '*.eastmoney.com','*.xueqiu.com','*.gtimg.cn','*.qpic.cn','*.idqqimg.com','*.qq.com',
  '*.163.com','*.126.net','*.weibo.com','*.weibo.cn',
  '*.csindex.com.cn','*.sse.com.cn','*.szse.cn','*.bse.cn','*.cninfo.com.cn',
  '*.cffex.com.cn','*.dce.com.cn','*.shfe.com.cn','*.czce.com.cn','*.gfex.com.cn',
  '*.gov.cn','*.tdx.com.cn','*.gw.com.cn','*.hexun.com','*.jrj.com.cn','*.cls.cn',
  '*.stcn.com','*.caixin.com','*.yicai.com','*.21jingji.com',
  # ---- 国内常见站点 ----
  '*.iqiyi.com','*.qy.net','*.youku.com','*.ykimg.com',
  '*.douyin.com','*.douyinpic.com','*.douyinstatic.com','*.toutiao.com','*.ixigua.com',
  '*.snssdk.com','*.bytedance.com','*.kuaishou.com','*.gifshow.com','*.douyu.com','*.huya.com',
  '*.acfun.cn','*.xiaohongshu.com','*.xhscdn.com',
  '*.taobao.com','*.tmall.com','*.tbcdn.cn','*.alicdn.com','*.alibaba.com','*.aliyun.com',
  '*.pinduoduo.com','*.yangkeduo.com','*.suning.com','*.dangdang.com','*.vip.com',
  '*.vmall.com','*.mi.com','*.xiaomi.com',
  '*.meituan.com','*.dianping.com','*.ele.me','*.ctrip.com','*.qunar.com','*.12306.cn','*.amap.com',
  '*.csdn.net','*.juejin.cn','*.oschina.net','*.gitee.com','*.cnblogs.com','*.segmentfault.com',
  '*.jianshu.com','*.douban.com','*.doubanio.com','*.youdao.com',
  '*.wps.cn','*.kingsoft.com','*.dingtalk.com','*.feishu.cn','*.xfyun.cn',
  '*.tuna.tsinghua.edu.cn','*.huaweicloud.com','*.myqcloud.com','*.cloud.tencent.com','*.npmmirror.com'
)

$existing = @($cur -split ';' | Where-Object { $_.Trim() })
$merged = ($existing + $newDomains | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Unique)
$newVal = $merged -join ';'
Set-ItemProperty -Path $key -Name ProxyOverride -Value $newVal

Write-Host "`n=== 修改后 ==="; Write-Host $newVal
Write-Host ("`n条目数: {0} -> {1}" -f $existing.Count, $merged.Count)

# 广播 WinINET 设置变更, 让浏览器等立即生效(无需重启)
Add-Type -Namespace WinINet -Name Native -MemberDefinition @'
[DllImport("wininet.dll", SetLastError = true)]
public static extern bool InternetSetOption(IntPtr hInternet, int dwOption, IntPtr lpBuffer, int dwBufferLength);
'@
$r1 = [WinINet.Native]::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
$r2 = [WinINet.Native]::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0)  # INTERNET_OPTION_REFRESH
Write-Host "WinINET 刷新广播: settings_changed=$r1 refresh=$r2"
Write-Host "`n完成。回滚: powershell -ExecutionPolicy Bypass -File scripts\set_proxy_bypass.ps1 -Restore"
