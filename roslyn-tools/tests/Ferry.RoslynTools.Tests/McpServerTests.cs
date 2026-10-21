using System.Text.Json;
using ModelContextProtocol.Client;
using ModelContextProtocol.Protocol;

namespace Ferry.RoslynTools.Tests;

/// <summary>Starts the real tool as a child process and talks to it over MCP, like Python will.</summary>
public class McpServerTests
{
    private static string Fixture(string name) => Path.Combine(AppContext.BaseDirectory, "Fixtures", name);

    private static Task<McpClient> StartAsync()
    {
        var dll = Path.Combine(AppContext.BaseDirectory, "Ferry.RoslynTools.dll");
        var transport = new StdioClientTransport(new StdioClientTransportOptions
        {
            Name = "ferry-roslyn-tools",
            Command = "dotnet",
            Arguments = [dll, "mcp"],
        });
        return McpClient.CreateAsync(transport);
    }

    [Fact]
    public async Task ListTools_ShowsInventory()
    {
        await using var client = await StartAsync();

        var tools = await client.ListToolsAsync();

        var tool = Assert.Single(tools);
        Assert.Equal("inventory", tool.Name);
    }

    [Fact]
    public async Task CallInventory_ReturnsSameFactsAsTheCli()
    {
        await using var client = await StartAsync();

        var result = await client.CallToolAsync(
            "inventory",
            new Dictionary<string, object?> { ["repoPath"] = Fixture("multi") });

        Assert.NotEqual(true, result.IsError);
        var text = Assert.Single(result.Content.OfType<TextContentBlock>()).Text;
        using var doc = JsonDocument.Parse(text);
        var names = doc.RootElement.GetProperty("projects").EnumerateArray()
            .Select(p => p.GetProperty("name").GetString()!)
            .Order()
            .ToArray();
        Assert.Equal(["Core", "Desktop"], names);

        var stdout = new StringWriter();
        Cli.Run(["inventory", Fixture("multi")], stdout, new StringWriter());
        Assert.Equal(stdout.ToString().Trim(), text.Trim());
    }

    [Fact]
    public async Task CallInventory_WithMissingFolder_ReturnsToolError()
    {
        await using var client = await StartAsync();

        var result = await client.CallToolAsync(
            "inventory",
            new Dictionary<string, object?> { ["repoPath"] = Path.Combine(Path.GetTempPath(), "ferry-no-such-folder") });

        Assert.True(result.IsError);
    }
}
