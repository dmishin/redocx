# Build the importer's test corpus: documents authored by Word itself through COM.
#   powershell -ExecutionPolicy Bypass -File tools/corpus/make-corpus.ps1 -Out tests/corpus
# The documents are ours (generated here), so they can be committed. Styles are taken by
# Word's built-in style numbers, so the script works with any Word language.
#
# Each document is built in a background job with a Word of its own and a time limit: an
# automation Word that hangs (it happened while saving, with no dialog) then costs one
# document, not the run.

param([string]$Out = "tests/corpus", [int]$Timeout = 120, [string[]]$Only = @())

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "../..")
$outDir = [System.IO.Path]::GetFullPath((Join-Path $root $Out))  # Word rejects mixed / and \
New-Item -ItemType Directory -Force $outDir | Out-Null
$logo = [System.IO.Path]::GetFullPath((Join-Path $root "examples/img/logo.png"))
$photo = [System.IO.Path]::GetFullPath((Join-Path $root "examples/thesis/img/speed-field.png"))

# A new document from Documents.Add() can block on a hidden prompt when it is first saved
# (sensitivity labels, for one), so each document starts from a blank one redocx builds.
$blank = Join-Path $env:TEMP "redocx-corpus-blank.docx"
$source = Join-Path $env:TEMP "redocx-corpus-blank.rdx"
Set-Content -Path $source -Value "\document[lang=en-US]" -Encoding utf8
$ErrorActionPreference = "Continue"  # redocx reports on stderr, which 5.1 would treat as an error
& (Join-Path $root ".venv/Scripts/redocx.exe") build $source -o $blank 2>&1 | Out-Null
$ErrorActionPreference = "Stop"
if (-not (Test-Path $blank)) { throw "could not build $blank" }

$header = @'
param($blank, $path, $logo, $photo)
# Built-in style numbers (WdBuiltinStyle)
$Normal = -1; $H1 = -2; $H2 = -3; $H3 = -4; $Title = -63; $Quote = -181
$ListBullet = -49; $ListNumber = -50; $ListBullet2 = -55; $Caption = -35
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($blank, $false, $false)
    $sel = $word.Selection
    function Para([int]$style, [string]$text) {
        $sel.Style = $doc.Styles.Item($style)
        $sel.TypeText($text)
        $sel.TypeParagraph()
    }
'@
$footer = @'
    $doc.SaveAs2($path, 16)  # wdFormatDocumentDefault (.docx)
    "saved"
} catch {
    "line $($_.InvocationInfo.ScriptLineNumber): $($_.Exception.Message)"
    "  " + $_.InvocationInfo.Line.Trim()
} finally {
    foreach ($d in @($word.Documents)) { $d.Close([ref]0) }
    $word.Quit([ref]0)
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}
'@

$documents = [ordered]@{}
$documents['text.docx'] = @'
# text: formatting, lists, notes, links
Para $Title "Corpus: text and formatting"
Para $H1 "Character formatting"
$sel.Style = $doc.Styles.Item($Normal)
$sel.TypeText("Plain, ")
$sel.Font.Bold = 1; $sel.TypeText("bold"); $sel.Font.Bold = 0
$sel.TypeText(", ")
$sel.Font.Italic = 1; $sel.TypeText("italic"); $sel.Font.Italic = 0
$sel.TypeText(", ")
$sel.Font.Underline = 1; $sel.TypeText("underlined"); $sel.Font.Underline = 0
$sel.TypeText(", ")
$sel.Font.StrikeThrough = 1; $sel.TypeText("struck"); $sel.Font.StrikeThrough = 0
$sel.TypeText(", ")
$sel.Font.Color = 0x0000C0; $sel.TypeText("red"); $sel.Font.Color = -16777216
$sel.TypeText(", ")
$sel.Range.HighlightColorIndex = 7; $sel.TypeText("highlighted"); $sel.Range.HighlightColorIndex = 0
$sel.TypeText(", ")
$sel.Font.Size = 16; $sel.TypeText("large"); $sel.Font.Size = 11
$sel.TypeText(", ")
$sel.Font.Name = "Consolas"; $sel.TypeText("monospace"); $sel.Font.Name = "Calibri"
$sel.TypeText(", H")
$sel.Font.Subscript = 1; $sel.TypeText("2"); $sel.Font.Subscript = 0
$sel.TypeText("O and E=mc")
$sel.Font.Superscript = 1; $sel.TypeText("2"); $sel.Font.Superscript = 0
$sel.TypeText(". A footnote")
$doc.Footnotes.Add($sel.Range, [Type]::Missing, "The footnote text.") | Out-Null
$sel.TypeText(" and an endnote")
$doc.Endnotes.Add($sel.Range, [Type]::Missing, "The endnote text.") | Out-Null
$sel.TypeText(". A link to ")
$doc.Hyperlinks.Add($sel.Range, "https://example.com", [Type]::Missing, [Type]::Missing, "example.com") | Out-Null
$sel.TypeText(".")
$sel.TypeParagraph()
Para $H2 "Paragraph formatting"
$sel.Style = $doc.Styles.Item($Normal)
$sel.ParagraphFormat.Alignment = 3  # justify
$sel.ParagraphFormat.LeftIndent = 36
$sel.ParagraphFormat.SpaceBefore = 12
$sel.TypeText("A justified, indented paragraph with space before it. Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore.")
$sel.TypeParagraph()
$sel.ParagraphFormat.Reset()
Para $Quote "A quotation in the Quote style."
Para $H2 "Lists"
Para $ListBullet "First bullet"
Para $ListBullet2 "Nested bullet"
Para $ListBullet "Second bullet"
Para $ListNumber "First numbered"
Para $ListNumber "Second numbered"
$sel.Style = $doc.Styles.Item($Normal)
$sel.TypeText("Tab`there, a line")
$sel.InsertBreak(11)  # wdTextWrappingBreak: a line break
$sel.TypeText("break, and a page break after this.")
$sel.InsertBreak(7)   # wdPageBreak
Para $H1 "After the page break"
Para $Normal "The end."
'@

$documents['tables.docx'] = @'
# tables
Para $H1 "Tables"
$table = $doc.Tables.Add($sel.Range, 4, 3)
$table.Style = -197  # wdStyleTableLightGridAccent1 is old; use the built-in grid table below
try { $table.Style = "Grid Table 4 - Accent 1" } catch { }
$table.Cell(1, 1).Range.Text = "Item"
$table.Cell(1, 2).Range.Text = "Q1"
$table.Cell(1, 3).Range.Text = "Q2"
$table.Cell(2, 1).Range.Text = "Revenue"
$table.Cell(2, 2).Range.Text = "10.5"
$table.Cell(2, 3).Range.Text = "12.25"
$table.Cell(3, 1).Range.Text = "Cost"
$table.Cell(3, 2).Range.Text = "7"
$table.Cell(3, 3).Range.Text = "8"
$table.Cell(3, 2).Shading.BackgroundPatternColor = 0xCCF2FF
$table.Cell(4, 1).Merge($table.Cell(4, 3))
$table.Cell(4, 1).Range.Text = "Total: merged across"
$table.Rows.Item(1).HeadingFormat = -1
$sel.EndKey(6) | Out-Null
$sel.TypeParagraph()
$table2 = $doc.Tables.Add($sel.Range, 3, 2)
$table2.Borders.Enable = 1
$table2.Cell(1, 1).Merge($table2.Cell(3, 1))
$table2.Cell(1, 1).Range.Text = "Merged down"
$table2.Cell(1, 2).Range.Text = "a"
$table2.Cell(2, 2).Range.Text = "b"
$table2.Cell(3, 2).Range.Text = "c"
$table2.Rows.Alignment = 1  # centre
$table2.Columns.Item(1).Width = 120
$sel.EndKey(6) | Out-Null
Para $Normal "After the tables."
'@

$documents['references.docx'] = @'
# references: contents, captions, cross-references, fields
$doc.TablesOfContents.Add($sel.Range, $true, 1, 3) | Out-Null
$sel.EndKey(6) | Out-Null
$sel.TypeParagraph()
Para $H1 "Introduction"
Para $Normal "The figure below shows the logo."
$sel.InlineShapes.AddPicture($logo) | Out-Null
$sel.TypeParagraph()
$sel.InsertCaption(-1, ": The logo", [Type]::Missing, 1)  # wdCaptionFigure, below
$sel.TypeParagraph()
$sel.Style = $doc.Styles.Item($Normal)
$sel.TypeText("See ")
$sel.InsertCrossReference(-1, 3, 1, $true)  # figure, label and number, first item, as a link
$sel.TypeText(" on page ")
$sel.InsertCrossReference(-1, 7, 1, $true)  # page number
$sel.TypeText(", and the section ")
$sel.InsertCrossReference(1, -1, 1, $true)  # wdRefTypeHeading, its text, the first heading
$sel.TypeText(". Today is ")
$sel.Fields.Add($sel.Range, 31, 'DATE \@ "d MMMM yyyy"', $false) | Out-Null  # wdFieldDate
$sel.TypeText(".")
$sel.TypeParagraph()
Para $H1 "Details"
Para $Normal "The details."
$footer = $doc.Sections.Item(1).Footers.Item(1).Range
$footer.Text = "Page "
$footer.Collapse(0)
$doc.Fields.Add($footer, 33) | Out-Null  # wdFieldPage
$doc.TablesOfContents.Item(1).Update()
'@

$documents['sections.docx'] = @'
# sections, headers and footers
Para $Title "Sections"
Para $Normal "The first section has a different first page."
$doc.Sections.Item(1).PageSetup.DifferentFirstPageHeaderFooter = -1
$doc.Sections.Item(1).Headers.Item(1).Range.Text = "Default header, section 1"
$doc.Sections.Item(1).Footers.Item(1).Range.Text = "Footer, section 1"
$sel.EndKey(6) | Out-Null
$sel.InsertBreak(2)  # wdSectionBreakNextPage
$second = $doc.Sections.Item(2)
$second.PageSetup.Orientation = 1  # landscape
$second.Headers.Item(1).LinkToPrevious = $false
$second.Headers.Item(1).Range.Text = "Landscape header, section 2"
$second.Footers.Item(1).PageNumbers.RestartNumberingAtSection = $true
$second.Footers.Item(1).PageNumbers.StartingNumber = 1
$second.Footers.Item(1).PageNumbers.NumberStyle = 2  # lower roman
$sel.Style = $doc.Styles.Item($H1)
$sel.TypeText("A landscape section")
$sel.TypeParagraph()
Para $Normal "With its own header and roman page numbers."
$sel.InsertBreak(3)  # wdSectionBreakContinuous
$third = $doc.Sections.Item(3)
$third.PageSetup.TextColumns.SetCount(2)
$third.PageSetup.TextColumns.LineBetween = -1
Para $Normal "Two columns. Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation."
$third.Borders.Enable = 1
'@

$documents['drawings.docx'] = @'
# drawings: pictures, text boxes, shapes, drop caps
Para $H1 "Drawings"
$inline = $sel.InlineShapes.AddPicture($photo)
$inline.Width = 150; $inline.Height = 75
$inline.PictureFormat.CropLeft = 20
$inline.Line.Visible = -1; $inline.Line.Weight = 1.5
$sel.TypeParagraph()
Para $Normal "Text that flows around a floating picture on the right. Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat."
$anchor = $doc.Paragraphs.Item($doc.Paragraphs.Count - 1).Range
$float = $doc.Shapes.AddPicture($photo, $false, $true, 300, 0, 120, 60, $anchor)
$float.WrapFormat.Type = 0  # square
$box = $doc.Shapes.AddTextbox(1, 72, 300, 200, 60, $anchor)
$box.TextFrame.TextRange.Text = "A text box with some text."
$box.Fill.ForeColor.RGB = 0xCCF2FF
$rect = $doc.Shapes.AddShape(1, 300, 300, 80, 40, $anchor)  # a rectangle
$sel.EndKey(6) | Out-Null
Para $Normal "Drop cap: the first letter of this paragraph is large. Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
$dropped = $doc.Paragraphs.Item($doc.Paragraphs.Count - 1)
$dropped.DropCap.Position = 1
$dropped.DropCap.LinesToDrop = 3
'@

$documents['review.docx'] = @'
# review: comments, tracked changes, content controls, an equation
Para $H1 "Review"
Para $Normal "The notice period is thirty days from the date of the notice."
$range = $doc.Paragraphs.Item(2).Range
$find = $range.Duplicate
$find.Find.Execute("thirty days") | Out-Null
$doc.Comments.Add($find, "Please confirm the commercial position.") | Out-Null
$doc.TrackRevisions = $true
$edit = $doc.Paragraphs.Item(2).Range.Duplicate
$edit.Find.Execute("thirty") | Out-Null
$edit.Text = "sixty"
$doc.TrackRevisions = $false
$sel.EndKey(6) | Out-Null
$sel.TypeText("Name: ")
$name = $doc.ContentControls.Add(1, $sel.Range)  # plain text
$name.Tag = "name"; $name.Title = "Full name"
$sel.EndKey(6) | Out-Null
$sel.TypeParagraph()
$sel.TypeText("Status: ")
$status = $doc.ContentControls.Add(4, $sel.Range)  # dropdown
$status.Tag = "status"
$status.DropdownListEntries.Add("Draft") | Out-Null
$status.DropdownListEntries.Add("Final") | Out-Null
$sel.EndKey(6) | Out-Null
$sel.TypeParagraph()
$check = $doc.ContentControls.Add(8, $sel.Range)  # checkbox
$check.Tag = "agree"
$after = $check.Range.End + 1  # just past the control, not inside it
$sel.SetRange($after, $after)
$sel.TypeText(" I agree.")
$sel.TypeParagraph()
$sel.TypeText("An equation: ")
$math = $sel.Range
$math.Text = "x=(-b" + [char]0x00B1 + "sqrt(b^2-4ac))/2a"
$doc.OMaths.Add($math) | Out-Null
$doc.OMaths.Item(1).BuildUp()
'@

$documents['math.docx'] = @'
# math: equations Word builds from its linear format (UnicodeMath). Characters by code,
# since PowerShell 5.1 reads this file as ANSI.
function C([int]$code) { [string][char]$code }
$sum = C 0x2211; $int = C 0x222B; $inf = C 0x221E; $op = C 0x2592; $sqrt = C 0x221A
$mat = C 0x25A0; $arr = C 0x2588; $box = C 0x25A1; $bar = C 0x2502; $apply = C 0x2061
$below = C 0x252C; $to = C 0x2192; $ge = C 0x2265; $le = C 0x2264; $pi = C 0x03C0
$close = C 0x2524; $hat = C 0x0302; $minus = C 0x2212
function Eq([string]$linear, [int]$type) {
    $sel.Style = $doc.Styles.Item($Normal)
    $range = $sel.Range
    $range.Text = $linear
    $doc.OMaths.Add($range) | Out-Null
    $equation = $range.OMaths.Item(1)
    $equation.BuildUp()
    $equation.Type = $type  # 0 display, 1 inline
    $sel.EndKey(6) | Out-Null
}
Para $H1 "Equations"
$sel.TypeText("Inline math: the area is ")
Eq "${pi}r^2" 1
$sel.TypeText(" and a fraction ")
Eq "a/b" 1
$sel.TypeText(" in text.")
$sel.TypeParagraph()
Eq "x=(${minus}b$(C 0x00B1)${sqrt}(b^2${minus}4ac))/2a" 0; $sel.TypeParagraph()
Eq "${sum}_(i=1)^n${op}i^2=(n(n+1)(2n+1))/6" 0; $sel.TypeParagraph()
Eq "${int}_0^${inf}${op}e^(${minus}x^2) dx=${sqrt}${pi}/2" 0; $sel.TypeParagraph()
Eq "lim${below}(x${to}0)${apply}$(C 0x3016)(sin${apply}x)/x$(C 0x3017)=1" 0; $sel.TypeParagraph()
Eq "A=(${mat}(a&b@c&d))" 0; $sel.TypeParagraph()
Eq "f(x)={${mat}(1&x${ge}0@0&x<0)${close}" 0; $sel.TypeParagraph()
Eq "${arr}(a&=b+c@&=d)" 0; $sel.TypeParagraph()
Eq "x_i^2+y${hat}+${sqrt}(3&x)" 0; $sel.TypeParagraph()
Eq "E=mc^2#(1)" 0; $sel.TypeParagraph()
Eq "${box}(x+y)+(a${bar}b)" 0; $sel.TypeParagraph()
'@

foreach ($name in $documents.Keys) {
    if ($Only -and $Only -notcontains $name) { continue }
    $code = [scriptblock]::Create($header + "`n" + $documents[$name] + "`n" + $footer)  # here-strings drop their last newline
    $path = Join-Path $outDir $name
    $job = Start-Job -ScriptBlock $code -ArgumentList $blank, $path, $logo, $photo
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
