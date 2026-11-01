param([string]$InputFile, [string]$OutputFile)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$taskRequests = [System.IO.File]::ReadAllText($InputFile) | ConvertFrom-Json
$taskResult = @{}
$taskCulture = [System.Globalization.CultureInfo]::InvariantCulture
$taskBitmap = [System.Drawing.Bitmap]::new(1,1)
$taskGraphics = [System.Drawing.Graphics]::FromImage($taskBitmap)
function Format-Number([single]$n) { return $n.ToString('0.###', $taskCulture) }
foreach ($taskRequest in $taskRequests) {
  $taskFamily = [System.Drawing.FontFamily]::new($taskRequest.font)
  $taskStyle = [int]$taskRequest.style
  $taskPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
  $taskFormat = [System.Drawing.StringFormat]::GenericTypographic.Clone()
  $taskFormat.FormatFlags = $taskFormat.FormatFlags -bor [System.Drawing.StringFormatFlags]::NoWrap -bor [System.Drawing.StringFormatFlags]::MeasureTrailingSpaces
  $taskFont = [System.Drawing.Font]::new($taskFamily,100,[System.Drawing.FontStyle]$taskStyle,[System.Drawing.GraphicsUnit]::Pixel)
  $taskAdvance = $taskGraphics.MeasureString([string]$taskRequest.text,$taskFont,[System.Drawing.SizeF]::new(100000,100000),$taskFormat).Width
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
    advance=$taskAdvance;
    font=$taskRequest.font; style=$taskStyle; text=$taskRequest.text
  }
  $taskFormat.Dispose()
  $taskFont.Dispose()
  $taskPath.Dispose()
  $taskFamily.Dispose()
}
$taskGraphics.Dispose()
$taskBitmap.Dispose()
$taskJson = $taskResult | ConvertTo-Json -Depth 5 -Compress
[System.IO.File]::WriteAllText($OutputFile, $taskJson, [System.Text.UTF8Encoding]::new($false))
Write-Output ('Outlined ' + $taskRequests.Count + ' distinct text strings.')
