param([Parameter(Mandatory=$true)][string]$Path)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Data.Pdf.PdfDocument,Windows.Data.Pdf,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
function Await-Result($Operation,[Type]$ResultType){
 $method=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'} | Select-Object -First 1
 $task=$method.MakeGenericMethod($ResultType).Invoke($null,@($Operation));$task.Wait();$task.Result
}
function Await-Action($Operation){
 $method=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'} | Select-Object -First 1
 $task=$method.Invoke($null,@($Operation));$task.Wait()
}
function Read-Image($Stream){
 $decoder=Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($Stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
 $bitmap=Await-Result ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
 try{$result=Await-Result ($script:engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult]);$result.Text}finally{$bitmap.Dispose()}
}
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if(-not $engine){throw 'Windows text recognition language is unavailable'}
$file=Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$parts=@();$limited=$false
if([IO.Path]::GetExtension($Path) -eq '.pdf'){
 $document=Await-Result ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
 $limit=[Math]::Min(4,$document.PageCount);$limited=$document.PageCount -gt $limit
 for($index=0;$index -lt $limit;$index++){
  $page=$document.GetPage($index);$stream=New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
  try{Await-Action ($page.RenderToStreamAsync($stream));$stream.Seek(0);$parts+=Read-Image $stream}finally{$stream.Dispose();$page.Dispose()}
 }
}else{
 $stream=Await-Result ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
 try{$parts+=Read-Image $stream}finally{$stream.Dispose()}
}
@{text=($parts -join "`n");limited=$limited;method='Windows OCR'} | ConvertTo-Json -Compress
