# Build the RTF reader's test corpus: RTF written by Word and by RichEdit, and Word's own
# reading of each file saved as .docx (the oracle tests/rtf/test_corpus.py compares with).
#   powershell -ExecutionPolicy Bypass -File tools/corpus/make-rtf-corpus.ps1
#
# word-NAME.rtf      Word saves tests/corpus/NAME.docx as RTF.
# richedit-NAME.rtf  RichEdit (.NET's RichTextBox) loads word-NAME.rtf and writes it again:
#                    RichEdit's own way of writing the same tables and pictures.
# richedit-formatting.rtf  Text formatted through RichTextBox, as programs that use it do.
# NAME.docx          Word opens NAME.rtf and saves it as .docx.
# The documents are ours (generated here), so they can be committed.

param([string]$Out = "tests/corpus/rtf", [int]$Timeout = 120)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "../..")
$outDir = [System.IO.Path]::GetFullPath((Join-Path $root $Out))  # Word rejects mixed / and \
$corpus = [System.IO.Path]::GetFullPath((Join-Path $root "tests/corpus"))
New-Item -ItemType Directory -Force $outDir | Out-Null

# Word opens one file and saves it in another format, in a job with a Word of its own and a
# time limit: an automation Word that hangs costs one file, not the run.
$convert = {
    param($in, $out, $format)
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    try {
        # ConfirmConversions, ReadOnly, AddToRecentFiles
        $doc = $word.Documents.Open($in, $false, $true, $false)
        $doc.SaveAs2($out, $format)
        "saved"
    } catch {
        $_.Exception.Message
    } finally {
        foreach ($d in @($word.Documents)) { $d.Close([ref]0) }
        $word.Quit([ref]0)
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
    }
}

function Convert-InWord([string]$in, [string]$out, [int]$format) {
    $job = Start-Job -ScriptBlock $convert -ArgumentList $in, $out, $format
    $name = Split-Path $out -Leaf
    if (Wait-Job $job -Timeout $Timeout) {
        $output = Receive-Job $job -ErrorAction Continue 2>&1
        if ($output -contains "saved") { "wrote $name" } else { "FAILED $name"; $output | Select-Object -Last 3 }
    } else {
        "TIMEOUT $name"
        Get-CimInstance Win32_Process -Filter "Name='WINWORD.EXE'" |
            Where-Object { $_.CommandLine -like '*-Embedding*' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    }
    Remove-Job $job -Force
}

$wdFormatRTF = 6
$wdFormatDocumentDefault = 16

foreach ($name in @("text", "tables", "drawings")) {
    Convert-InWord (Join-Path $corpus "$name.docx") (Join-Path $outDir "word-$name.rtf") $wdFormatRTF
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Save-Rtf([System.Windows.Forms.RichTextBox]$box, [string]$path) {
    [System.IO.File]::WriteAllText($path, $box.Rtf, [System.Text.Encoding]::ASCII)
    "wrote $(Split-Path $path -Leaf)"
}

foreach ($name in @("text", "tables", "drawings")) {
    $source = Join-Path $outDir "word-$name.rtf"
    if (-not (Test-Path $source)) { continue }
    $box = New-Object System.Windows.Forms.RichTextBox
    $box.Rtf = [System.IO.File]::ReadAllText($source, [System.Text.Encoding]::ASCII)
    Save-Rtf $box (Join-Path $outDir "richedit-$name.rtf")
    $box.Dispose()
}

# Formatting set through RichTextBox's selection, as a program that uses it would.
$box = New-Object System.Windows.Forms.RichTextBox
function Add-Text([string]$text, [string]$font = "Arial", [single]$size = 10,
                  [System.Drawing.FontStyle]$style = "Regular", $color = $null, $back = $null,
                  [int]$offset = 0) {
    $start = $box.TextLength
    $box.AppendText($text)
    $box.Select($start, $text.Length)
    $box.SelectionFont = New-Object System.Drawing.Font($font, $size, $style)
    if ($color) { $box.SelectionColor = $color }
    if ($back) { $box.SelectionBackColor = $back }
    if ($offset) { $box.SelectionCharOffset = $offset }
    $box.Select($box.TextLength, 0)
}
function Set-Paragraph([scriptblock]$format) {
    $box.Select($box.TextLength, 0)
    & $format
}
$C = { param([int[]]$codes) -join ($codes | ForEach-Object { [char]$_ }) }

Set-Paragraph { $box.SelectionAlignment = "Left" }
Add-Text "A heading in a larger font`n" -size 16 -style Bold
Add-Text "Plain text, "
Add-Text "bold" -style Bold
Add-Text ", "
Add-Text "italic" -style Italic
Add-Text ", "
Add-Text "underlined" -style Underline
Add-Text ", "
Add-Text "struck" -style Strikeout
Add-Text ", "
Add-Text "red" -color ([System.Drawing.Color]::Red)
Add-Text ", "
Add-Text "highlighted" -back ([System.Drawing.Color]::Yellow)
Add-Text ", "
Add-Text "monospaced" -font "Courier New"
Add-Text " and x"
Add-Text "2" -size 7 -offset 4
Add-Text ".`n"
Add-Text ("Other scripts: " + (& $C 0x41F,0x440,0x438,0x432,0x435,0x442) + ", " +
          (& $C 0x3B1,0x3B2,0x3B3) + ", " + (& $C 0x10D,0x159,0x17E) + ".`n")
Set-Paragraph { $box.SelectionAlignment = "Center" }
Add-Text "A centred paragraph.`n"
Set-Paragraph { $box.SelectionAlignment = "Right" }
Add-Text "A right-aligned paragraph.`n"
Set-Paragraph { $box.SelectionAlignment = "Left"; $box.SelectionIndent = 40; $box.SelectionRightIndent = 40 }
Add-Text "An indented paragraph, long enough to wrap onto a second line in most window widths.`n"
Set-Paragraph { $box.SelectionIndent = 0; $box.SelectionRightIndent = 0; $box.SelectionHangingIndent = 30 }
Add-Text "A paragraph with a hanging indent, long enough to wrap onto a second line in most widths.`n"
Set-Paragraph { $box.SelectionHangingIndent = 0; $box.SelectionBullet = $true }
Add-Text "First bullet`n"
Add-Text "Second bullet`n"
Set-Paragraph { $box.SelectionBullet = $false; $box.SelectionTabs = [int[]]@(100, 200) }
Add-Text "Name`tCode`tState`n"
Add-Text "One`tA1`tactive`n"
Save-Rtf $box (Join-Path $outDir "richedit-formatting.rtf")
$box.Dispose()

Get-ChildItem $outDir -Filter *.rtf | ForEach-Object {
    Convert-InWord $_.FullName (Join-Path $outDir ($_.BaseName + ".docx")) $wdFormatDocumentDefault
}
