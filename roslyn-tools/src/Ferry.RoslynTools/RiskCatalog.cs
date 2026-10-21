using System.IO.Enumeration;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Ferry.RoslynTools;

/// <summary>One entry in api-risks.json: something in an old .NET Framework repo that needs attention on the way to .NET 10.</summary>
public sealed class RiskRule
{
    public string Id { get; set; } = "";
    public string Category { get; set; } = "";
    public string Severity { get; set; } = "medium";
    public string Description { get; set; } = "";

    /// <summary>Namespace prefixes, matched on dot boundaries (System.Web matches System.Web.Mvc, not System.WebX).</summary>
    public List<string> Namespaces { get; set; } = [];

    /// <summary>Simple type names, for example BinaryFormatter. Matched without semantic analysis, so keep them distinctive.</summary>
    public List<string> Types { get; set; } = [];

    /// <summary>Assembly names from old-style project files: &lt;Reference Include="System.Web" /&gt;.</summary>
    public List<string> Assemblies { get; set; } = [];

    /// <summary>NuGet package ids.</summary>
    public List<string> Packages { get; set; } = [];

    /// <summary>MSBuild properties that mean "yes" when set to true, for example UseWPF.</summary>
    public List<string> Properties { get; set; } = [];

    /// <summary>Item names in a project file, for example COMReference or EntityDeploy.</summary>
    public List<string> Items { get; set; } = [];

    /// <summary>Text that appears in an Import path in a project file, for example Microsoft.WebApplication.targets.</summary>
    public List<string> Imports { get; set; } = [];

    /// <summary>Project type GUIDs from the ProjectTypeGuids property (with or without braces).</summary>
    public List<string> ProjectTypeGuids { get; set; } = [];

    /// <summary>File names, with * wildcards, for example *.aspx or Web.config. Reported once per project, with a count.</summary>
    public List<string> Files { get; set; } = [];
}

public sealed class RiskCatalog
{
    public const string FileName = "api-risks.json";

    private static readonly string[] Severities = ["high", "medium", "low"];

    private static readonly JsonSerializerOptions ReadOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
    };

    private readonly Dictionary<string, RiskRule> _byId = [];
    private readonly List<(string Prefix, RiskRule Rule)> _namespaces = [];
    private readonly Dictionary<string, RiskRule> _types = new(StringComparer.Ordinal);
    private readonly Dictionary<string, RiskRule> _assemblies = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, RiskRule> _packages = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, RiskRule> _properties = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, RiskRule> _items = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, RiskRule> _guids = new(StringComparer.OrdinalIgnoreCase);
    private readonly List<(string Fragment, RiskRule Rule)> _imports = [];
    private readonly List<(string Pattern, RiskRule Rule)> _files = [];

    public RiskCatalog(IEnumerable<RiskRule> rules)
    {
        Rules = rules.ToList();

        foreach (var rule in Rules)
        {
            if (string.IsNullOrWhiteSpace(rule.Id))
                throw new InvalidDataException("Every rule in api-risks.json needs an id.");
            if (!_byId.TryAdd(rule.Id, rule))
                throw new InvalidDataException($"Duplicate rule id '{rule.Id}' in api-risks.json.");
            if (!Severities.Contains(rule.Severity, StringComparer.OrdinalIgnoreCase))
                throw new InvalidDataException($"Rule '{rule.Id}' has severity '{rule.Severity}'. Use high, medium or low.");

            foreach (var ns in rule.Namespaces) AddNamespace(ns, rule);
            foreach (var t in rule.Types) Register(_types, "type", t, rule);
            foreach (var a in rule.Assemblies) Register(_assemblies, "assembly", a, rule);
            foreach (var p in rule.Packages) Register(_packages, "package", p, rule);
            foreach (var p in rule.Properties) Register(_properties, "property", p, rule);
            foreach (var i in rule.Items) Register(_items, "item", i, rule);
            foreach (var g in rule.ProjectTypeGuids) Register(_guids, "project type guid", NormalizeGuid(g), rule);
            foreach (var i in rule.Imports) _imports.Add((i, rule));
            foreach (var f in rule.Files) _files.Add((f, rule));
        }

        // Longest prefix wins, so System.Windows.Forms is WinForms and not WPF.
        _namespaces.Sort((a, b) => b.Prefix.Length.CompareTo(a.Prefix.Length));
    }

    public IReadOnlyList<RiskRule> Rules { get; }

    public RiskRule? ById(string id) => _byId.GetValueOrDefault(id);

    public static RiskCatalog Load(string path)
    {
        using var stream = File.OpenRead(path);
        var file = JsonSerializer.Deserialize<RiskFile>(stream, ReadOptions)
                   ?? throw new InvalidDataException($"{path} is empty.");
        return new RiskCatalog(file.Rules);
    }

    /// <summary>Loads api-risks.json from next to the built tool.</summary>
    public static RiskCatalog LoadDefault() => Load(Path.Combine(AppContext.BaseDirectory, FileName));

    /// <summary>Match a dotted name such as System.Web.Mvc or System.Web.HttpContext.Current.</summary>
    public RiskRule? MatchNamespace(string dottedName)
    {
        foreach (var (prefix, rule) in _namespaces)
        {
            if (dottedName.StartsWith(prefix, StringComparison.Ordinal)
                && (dottedName.Length == prefix.Length || dottedName[prefix.Length] == '.'))
                return rule;
        }
        return null;
    }

    public RiskRule? MatchType(string identifier)
    {
        if (_types.TryGetValue(identifier, out var rule)) return rule;
        // Attributes can be written without the suffix: list "ServiceContract", and both spellings match.
        if (identifier.EndsWith("Attribute", StringComparison.Ordinal)
            && _types.TryGetValue(identifier[..^"Attribute".Length], out rule))
            return rule;
        return null;
    }

    public RiskRule? MatchAssembly(string assemblyName) => _assemblies.GetValueOrDefault(assemblyName);

    public RiskRule? MatchPackage(string packageId) => _packages.GetValueOrDefault(packageId);

    public RiskRule? MatchProperty(string name) => _properties.GetValueOrDefault(name);

    public RiskRule? MatchItem(string name) => _items.GetValueOrDefault(name);

    public RiskRule? MatchProjectTypeGuid(string guid) => _guids.GetValueOrDefault(NormalizeGuid(guid));

    /// <summary>First rule whose fragment appears in the import path.</summary>
    public RiskRule? MatchImport(string importPath)
    {
        foreach (var (fragment, rule) in _imports)
        {
            if (importPath.Contains(fragment, StringComparison.OrdinalIgnoreCase)) return rule;
        }
        return null;
    }

    /// <summary>Every (rule, pattern) whose file pattern matches this file name.</summary>
    public IEnumerable<(RiskRule Rule, string Pattern)> MatchFile(string fileName)
    {
        foreach (var (pattern, rule) in _files)
        {
            if (FileSystemName.MatchesSimpleExpression(pattern, fileName, ignoreCase: true))
                yield return (rule, pattern);
        }
    }

    private void AddNamespace(string ns, RiskRule rule)
    {
        var owner = _namespaces.FirstOrDefault(n => n.Prefix == ns).Rule;
        if (owner is not null && owner != rule)
            throw new InvalidDataException($"namespace '{ns}' is listed in both '{owner.Id}' and '{rule.Id}'.");
        _namespaces.Add((ns, rule));
    }

    private static void Register(Dictionary<string, RiskRule> map, string what, string key, RiskRule rule)
    {
        if (map.TryGetValue(key, out var owner) && owner != rule)
            throw new InvalidDataException($"{what} '{key}' is listed in both '{owner.Id}' and '{rule.Id}'.");
        map[key] = rule;
    }

    private static string NormalizeGuid(string guid) => guid.Trim().Trim('{', '}').ToUpperInvariant();

    private sealed class RiskFile
    {
        [JsonPropertyName("rules")]
        public List<RiskRule> Rules { get; set; } = [];
    }
}
