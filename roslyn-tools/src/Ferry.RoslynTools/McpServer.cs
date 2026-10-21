using System.ComponentModel;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using ModelContextProtocol;
using ModelContextProtocol.Server;

namespace Ferry.RoslynTools;

/// <summary>
/// Runs the reader as an MCP server over stdio. stdout belongs to MCP messages only,
/// so all logs go to stderr.
/// </summary>
public static class McpServer
{
    public static async Task<int> RunAsync(string[] args)
    {
        var builder = Host.CreateApplicationBuilder(args);

        builder.Logging.ClearProviders();
        builder.Logging.AddConsole(options => options.LogToStandardErrorThreshold = LogLevel.Trace);

        builder.Services
            .AddMcpServer()
            .WithStdioServerTransport()
            .WithTools<InventoryTool>();

        await builder.Build().RunAsync();
        return 0;
    }
}

[McpServerToolType]
public sealed class InventoryTool
{
    /// <summary>The tool body, separate from the MCP attributes so tests can call it directly.</summary>
    public static string BuildInventoryJson(string repoPath, string? risksPath, Action<string> warn)
    {
        var risks = string.IsNullOrWhiteSpace(risksPath) ? RiskCatalog.LoadDefault() : RiskCatalog.Load(risksPath);
        var inventory = InventoryBuilder.Build(repoPath, risks, warn);
        return JsonSerializer.Serialize(inventory, InventoryJson.Options);
    }

    [McpServerTool(Name = "inventory", ReadOnly = true, Idempotent = true, Destructive = false, OpenWorld = false)]
    [Description("Scan an old .NET repo (no AI involved). Returns JSON with its projects and target frameworks, " +
                 "NuGet packages, and risky APIs for a .NET 10 migration (System.Web, WCF, BinaryFormatter, ...) with file and line.")]
    public static string Inventory(
        [Description("Absolute path to the repo folder on this machine.")] string repoPath,
        [Description("Optional path to a custom api-risks.json. Leave empty to use the built-in list.")] string? risksPath = null)
    {
        try
        {
            return BuildInventoryJson(repoPath, risksPath, message => Console.Error.WriteLine($"warning: {message}"));
        }
        catch (Exception ex) when (ex is IOException or JsonException or InvalidDataException or UnauthorizedAccessException)
        {
            // Surfaces to the caller as a tool error (isError = true) instead of crashing the server.
            throw new McpException(ex.Message, ex);
        }
    }
}
