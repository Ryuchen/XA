param([string]$InputFile, [string]$OutputFile)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$taskRequests = Get-Content -LiteralPath $InputFile -Raw -Encoding UTF8 | ConvertFrom-Json
$taskResult = @{}
$taskCulture = [System.Globalization.CultureInfo]::InvariantCulture
function Format-Number([single]$n) { return $n.ToString('0.###', $taskCulture) }
foreach ($taskRequest in $taskRequests) {
  $taskFamily = [System.Drawing.FontFamily]::new($taskRequest.font)
  $taskStyle = [int]$taskRequest.style
  $taskPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
  $taskFormat = [System.Drawing.StringFormat]::GenericTypographic.Clone()
  $taskFormat.FormatFlags = $taskFormat.FormatFlags -bor [System.Drawing.StringFormatFlags]::NoWrap
  $taskPath.AddString([string]$taskRequest.text, $taskFamily, $taskStyle, 100, [System.Drawing.PointF]::new(0,0), $taskFormat)
  $taskBounds = $taskPath.GetBounds()
  $taskPoints = $taskPath.PathPoints
  $taskTypes = $taskPath.PathTypes
  $taskBuilder = [System.Text.StringBuilder]::new()
  for ($taskIndex=0; $taskIndex -lt $taskPoints.Length; $taskIndex++) {
    $taskType = $taskTypes[$taskIndex] -band 7
    $taskPoint = $taskPoints[$taskIndex]
    if ($taskType -eq 0) {
      [void]$taskBuilder.Append('M' + (Format-Number $taskPoint.X) + ' ' + (Format-Number $taskPoint.Y))
    } elseif ($taskType -eq 1) {
      [void]$taskBuilder.Append('L' + (Format-Number $taskPoint.X) + ' ' + (Format-Number $taskPoint.Y))
    } elseif ($taskType -eq 3) {
      $taskPoint2 = $taskPoints[$taskIndex+1]
      $taskPoint3 = $taskPoints[$taskIndex+2]
      [void]$taskBuilder.Append('C' + (Format-Number $taskPoint.X) + ' ' + (Format-Number $taskPoint.Y) + ' ' + (Format-Number $taskPoint2.X) + ' ' + (Format-Number $taskPoint2.Y) + ' ' + (Format-Number $taskPoint3.X) + ' ' + (Format-Number $taskPoint3.Y))
      $taskIndex += 2
    }
    if (($taskTypes[$taskIndex] -band 128) -ne 0) { [void]$taskBuilder.Append('Z') }
  }
  $taskResult[[string]$taskRequest.id] = @{
    d=$taskBuilder.ToString(); x=$taskBounds.X; y=$taskBounds.Y; width=$taskBounds.Width; height=$taskBounds.Height;
    ascent=100*$taskFamily.GetCellAscent($taskStyle)/$taskFamily.GetEmHeight($taskStyle);
    font=$taskRequest.font; style=$taskStyle; text=$taskRequest.text
  }
  $taskFormat.Dispose()
  $taskPath.Dispose()
  $taskFamily.Dispose()
}
$taskJson = $taskResult | ConvertTo-Json -Depth 5 -Compress
[System.IO.File]::WriteAllText($OutputFile, $taskJson, [System.Text.UTF8Encoding]::new($false))
Write-Output ('Outlined ' + $taskRequests.Count + ' distinct text strings.')
