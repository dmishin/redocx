// Validates WordprocessingML packages against the Office 2019 schemas and semantic rules.
// Usage: ooxml-validate <file.docx> [more.docx ...]
// Exit codes: 0 = all valid, 1 = validation errors, 2 = usage error or unreadable package.

using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Validation;

if (args.Length == 0)
{
    Console.Error.WriteLine("usage: ooxml-validate <file.docx> [more.docx ...]");
    return 2;
}

var validator = new OpenXmlValidator(FileFormatVersions.Office2019);
var exitCode = 0;

foreach (var path in args)
{
    try
    {
        using var document = WordprocessingDocument.Open(path, isEditable: false);
        var errors = validator.Validate(document).ToList();
        foreach (var error in errors)
        {
            Console.WriteLine(
                $"{path}: {error.Part?.Uri} {error.Path?.XPath}: [{error.ErrorType}] {error.Description}");
        }
        Console.WriteLine(errors.Count == 0 ? $"{path}: OK" : $"{path}: {errors.Count} error(s)");
        if (errors.Count > 0)
        {
            exitCode = Math.Max(exitCode, 1);
        }
    }
    catch (Exception e) when (e is OpenXmlPackageException or IOException or InvalidDataException)
    {
        Console.WriteLine($"{path}: cannot open package: {e.Message}");
        exitCode = 2;
    }
}

return exitCode;
