using System.Xml;
using System.Xml.Linq;

namespace Ferry.RoslynTools;

/// <summary>What we learned from one .csproj file. No MSBuild evaluation: we read the XML as written.</summary>
public sealed record ParsedProject(
    string Name,
    string FullPath,
    bool SdkStyle,
    string? OutputType,
    IReadOnlyList<string> TargetFrameworks,
    IReadOnlyList<PackageInfo> Packages,
    IReadOnlyList<string> ProjectReferenceFullPaths,
    IReadOnlyList<ProjectFinding> Findings);

/// <summary>A line in a project file that points at a risky assembly, package or setting.</summary>
public sealed record ProjectFinding(RiskRule Rule, string Kind, string FullPath, int Line, string Match);

public static class ProjectReader
{
    public static ParsedProject Read(string csprojPath, RiskCatalog risks)
    {
        var fullPath = Path.GetFullPath(csprojPath);
        var doc = XDocument.Load(fullPath, LoadOptions.SetLineInfo);
        var root = doc.Root ?? throw new InvalidDataException($"{csprojPath} is empty.");

        var sdkStyle = root.Attribute("Sdk") is not null || root.Descendants().Any(e => e.Name.LocalName == "Sdk");
        var projectDir = Path.GetDirectoryName(fullPath)!;

        string? outputType = null;
        var frameworks = new List<string>();
        var findings = new List<ProjectFinding>();

        foreach (var prop in root.Elements().Where(e => e.Name.LocalName == "PropertyGroup").SelectMany(e => e.Elements()))
        {
            var name = prop.Name.LocalName;
            var value = prop.Value.Trim();

            switch (name)
            {
                case "OutputType":
                    outputType = value;
                    break;
                case "TargetFramework" or "TargetFrameworks":
                    frameworks.AddRange(value.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries));
                    break;
                case "TargetFrameworkVersion":
                    // Old-style projects: v4.7.2 means net472.
                    frameworks.Add("net" + value.TrimStart('v', 'V').Replace(".", ""));
                    break;
                case "ProjectTypeGuids":
                    // Visual Studio project kinds, like {349c5851-...};{fae04ec0-...}.
                    foreach (var guid in value.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
                    {
                        if (risks.MatchProjectTypeGuid(guid) is { } guidRule)
                            findings.Add(new ProjectFinding(guidRule, "project_type", fullPath, Line(prop), guid));
                    }
                    break;
            }

            if (value.Equals("true", StringComparison.OrdinalIgnoreCase) && risks.MatchProperty(name) is { } propertyRule)
                findings.Add(new ProjectFinding(propertyRule, "property", fullPath, Line(prop), $"{name}={value}"));
        }

        var packages = new List<PackageInfo>();
        var projectRefs = new List<string>();

        foreach (var item in root.Elements().Where(e => e.Name.LocalName == "ItemGroup").SelectMany(e => e.Elements()))
        {
            switch (item.Name.LocalName)
            {
                case "PackageReference":
                    var id = item.Attribute("Include")?.Value ?? item.Attribute("Update")?.Value;
                    if (string.IsNullOrWhiteSpace(id)) break;
                    var version = item.Attribute("Version")?.Value
                                  ?? item.Elements().FirstOrDefault(e => e.Name.LocalName == "Version")?.Value;
                    packages.Add(new PackageInfo(id, version));
                    if (risks.MatchPackage(id) is { } packageRule)
                        findings.Add(new ProjectFinding(packageRule, "package", fullPath, Line(item), id));
                    break;

                case "ProjectReference":
                    var include = item.Attribute("Include")?.Value;
                    if (!string.IsNullOrWhiteSpace(include))
                        projectRefs.Add(Path.GetFullPath(Path.Combine(projectDir, include.Replace('\\', '/'))));
                    break;

                case "Reference":
                    // Old-style: <Reference Include="System.Web, Version=4.0.0.0, ..." />
                    var assembly = item.Attribute("Include")?.Value.Split(',')[0].Trim();
                    if (!string.IsNullOrEmpty(assembly) && risks.MatchAssembly(assembly) is { } assemblyRule)
                        findings.Add(new ProjectFinding(assemblyRule, "assembly", fullPath, Line(item), assembly));
                    break;

                default:
                    // COMReference, EntityDeploy, WebReferenceUrl, and other item kinds.
                    if (risks.MatchItem(item.Name.LocalName) is { } itemRule)
                    {
                        var itemInclude = item.Attribute("Include")?.Value;
                        var match = string.IsNullOrEmpty(itemInclude) ? item.Name.LocalName : $"{item.Name.LocalName} {itemInclude}";
                        findings.Add(new ProjectFinding(itemRule, "item", fullPath, Line(item), match));
                    }
                    break;
            }
        }

        foreach (var import in root.Elements().Where(e => e.Name.LocalName == "Import"))
        {
            var target = import.Attribute("Project")?.Value;
            if (!string.IsNullOrEmpty(target) && risks.MatchImport(target) is { } importRule)
                findings.Add(new ProjectFinding(importRule, "import", fullPath, Line(import), target));
        }

        // Old-style projects keep packages in packages.config next to the project file.
        var packagesConfig = Path.Combine(projectDir, "packages.config");
        if (File.Exists(packagesConfig))
        {
            var configDoc = XDocument.Load(packagesConfig, LoadOptions.SetLineInfo);
            foreach (var p in configDoc.Descendants().Where(e => e.Name.LocalName == "package"))
            {
                var id = p.Attribute("id")?.Value;
                if (string.IsNullOrWhiteSpace(id)) continue;
                packages.Add(new PackageInfo(id, p.Attribute("version")?.Value));
                if (risks.MatchPackage(id) is { } configRule)
                    findings.Add(new ProjectFinding(configRule, "package", packagesConfig, Line(p), id));
            }
        }

        return new ParsedProject(
            Path.GetFileNameWithoutExtension(fullPath),
            fullPath,
            sdkStyle,
            outputType,
            frameworks.Distinct(StringComparer.OrdinalIgnoreCase).Order(StringComparer.OrdinalIgnoreCase).ToList(),
            packages.DistinctBy(p => p.Id, StringComparer.OrdinalIgnoreCase).OrderBy(p => p.Id, StringComparer.OrdinalIgnoreCase).ToList(),
            projectRefs.Distinct().Order(StringComparer.Ordinal).ToList(),
            findings);
    }

    private static int Line(XObject node) => node is IXmlLineInfo info && info.HasLineInfo() ? info.LineNumber : 0;
}
