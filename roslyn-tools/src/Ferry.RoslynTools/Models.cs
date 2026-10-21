using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Ferry.RoslynTools;

/// <summary>Everything `inventory` reports about a repo. Serialized as snake_case JSON.</summary>
public sealed record Inventory(
    string Repo,
    IReadOnlyList<ProjectInfo> Projects,
    IReadOnlyList<RiskHit> Risks,
    IReadOnlyList<RiskSummary> RiskSummary);

public sealed record ProjectInfo(
    string Name,
    string Path,
    bool SdkStyle,
    string? OutputType,
    IReadOnlyList<string> TargetFrameworks,
    IReadOnlyList<PackageInfo> Packages,
    IReadOnlyList<string> ProjectReferences,
    int SourceFileCount);

public sealed record PackageInfo(string Id, string? Version);

/// <summary>One place in the repo that matches a rule from api-risks.json. File rules set Count to the number of files.</summary>
public sealed record RiskHit(
    string RuleId,
    string Kind,
    string Project,
    string File,
    int Line,
    string Match,
    string? Text,
    int? Count = null);

public sealed record RiskSummary(string RuleId, string Category, string Severity, string Description, int Count);

public static class InventoryJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping, // readable quotes in source snippets; the output is not HTML
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };
}
