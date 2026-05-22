Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object {
  $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
  "{0}:{1}  PID={2}  Name={3}  Path={4}" -f $_.LocalAddress, $_.LocalPort, $_.OwningProcess, $proc.ProcessName, $proc.Path
}
