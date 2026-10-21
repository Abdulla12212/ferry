using System.Reflection;
using System.Text.Json;

namespace Ferry.RoslynTools;

public static class Cli
{
    public const string Usage = """
        usage:
          Ferry.RoslynTools --version
          Ferry.RoslynTools inventory <repo> [--risks <api-risks.json>]
        """;

    public static string Version =>
        typeof(Cli).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
        ?? "unknown";

    /// <summary>
    /// stdout carries only the result (JSON, or later MCP messages). Everything else goes to stderr.
    /// </summary>
    public static int Run(string[] args, TextWriter stdout, TextWriter stderr)
    {
        switch (args)
        {
            case ["--version"]:
                stdout.WriteLine(Version);
                return 0;

            case ["inventory", .. var rest]:
                return RunInventory(rest, stdout, stderr);

            default:
                stderr.WriteLine(Usage);
                return 1;
        }
    }

    private static int RunInventory(string[] args, TextWriter stdout, TextWriter stderr)
    {
        string? repo = null;
        string? risksPath = null;

        for (var i = 0; i < args.Length; i++)
        {
            if (args[i] == "--risks" && i + 1 < args.Length) risksPath = args[++i];
            else if (!args[i].StartsWith("--") && repo is null) repo = args[i];
            else
            {
                stderr.WriteLine(Usage);
                return 1;
            }
        }

        if (repo is null)
        {
            stderr.WriteLine(Usage);
            return 1;
        }

        try
        {
            var risks = risksPath is null ? RiskCatalog.LoadDefault() : RiskCatalog.Load(risksPath);
            var inventory = InventoryBuilder.Build(repo, risks, message => stderr.WriteLine($"warning: {message}"));
            stdout.WriteLine(JsonSerializer.Serialize(inventory, InventoryJson.Options));
            return 0;
        }
        catch (Exception ex) when (ex is IOException or JsonException or InvalidDataException or UnauthorizedAccessException)
        {
            stderr.WriteLine($"error: {ex.Message}");
            return 2;
        }
    }
}
