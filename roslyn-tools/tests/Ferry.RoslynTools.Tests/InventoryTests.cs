using System.Text.Json;

namespace Ferry.RoslynTools.Tests;

public sealed class InventoryTests : IDisposable
{
    private readonly string _temp = Path.Combine(Path.GetTempPath(), "ferry-tests-" + Guid.NewGuid().ToString("N"));
    private readonly List<string> _warnings = [];

    public void Dispose()
    {
        if (Directory.Exists(_temp)) Directory.Delete(_temp, recursive: true);
    }

    /// <summary>Copies a fixture to a temp folder, so tests can add files (bin/obj are git-ignored in this repo).</summary>
    private string Fixture(string name)
    {
        var source = Path.Combine(AppContext.BaseDirectory, "Fixtures", name);
        var target = Path.Combine(_temp, name);
        foreach (var file in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
        {
            var dest = Path.Combine(target, Path.GetRelativePath(source, file));
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            File.Copy(file, dest);
        }
        return target;
    }

    private Inventory Build(string repo) =>
        InventoryBuilder.Build(repo, RiskCatalog.LoadDefault(), _warnings.Add);

    [Fact]
    public void SdkStyleProject_ReportsFrameworksPackagesAndRisks()
    {
        var inventory = Build(Fixture("sdk-lib"));

        var project = Assert.Single(inventory.Projects);
        Assert.Equal("Lib", project.Name);
        Assert.Equal("Lib.csproj", project.Path);
        Assert.True(project.SdkStyle);
        Assert.Equal(["net472", "netstandard2.0"], project.TargetFrameworks);
        Assert.Equal(["EntityFramework", "Newtonsoft.Json"], project.Packages.Select(p => p.Id));
        Assert.Equal("13.0.3", project.Packages.Single(p => p.Id == "Newtonsoft.Json").Version);
        Assert.Equal("6.4.4", project.Packages.Single(p => p.Id == "EntityFramework").Version);
        Assert.Equal(1, project.SourceFileCount);

        // BinaryFormatter is on line 3 (using) and line 13 (code). Comments and strings must not count.
        Assert.DoesNotContain(inventory.Risks, r => r.RuleId == "system-web");
        Assert.Equal([3, 14], inventory.Risks.Where(r => r.RuleId == "binary-formatter").Select(r => r.Line));
        Assert.All(inventory.Risks.Where(r => r.RuleId == "binary-formatter"), r => Assert.Equal("src/Store.cs", r.File));
        Assert.Contains(inventory.Risks, r => r is { RuleId: "entity-framework-6", Kind: "package", File: "Lib.csproj" });
    }

    [Fact]
    public void OldStyleProject_ReadsFrameworkAssembliesAndPackagesConfig()
    {
        var inventory = Build(Fixture("legacy-web"));

        var project = Assert.Single(inventory.Projects);
        Assert.False(project.SdkStyle);
        Assert.Equal("Library", project.OutputType);
        Assert.Equal(["net472"], project.TargetFrameworks);
        Assert.Equal(["log4net", "Microsoft.AspNet.Mvc"], project.Packages.Select(p => p.Id));
        Assert.Equal(2, project.SourceFileCount); // packages/Foo/Ignored.cs is skipped

        var risks = inventory.Risks;
        Assert.Contains(risks, r => r is { RuleId: "system-web", Kind: "assembly", File: "Legacy.Web.csproj", Line: 9 });
        Assert.Contains(risks, r => r is { RuleId: "aspnet-mvc", Kind: "package", File: "packages.config", Line: 3 });
        Assert.Contains(risks, r => r is { RuleId: "aspnet-mvc", Kind: "using", File: "Controllers/HomeController.cs", Line: 2, Match: "System.Web.Mvc" });
        Assert.Contains(risks, r => r is { RuleId: "system-web", Kind: "qualified_name", Line: 11, Match: "System.Web.HttpContext.Current.User" });
        Assert.Contains(risks, r => r is { RuleId: "appdomain", Kind: "type", Line: 12 });
        Assert.Contains(risks, r => r is { RuleId: "windows-registry", Kind: "using", Line: 3 });
        Assert.DoesNotContain(risks, r => r.File.StartsWith("packages/"));
    }

    [Fact]
    public void OldStyleProject_FindsProjectLevelAndFileRules()
    {
        var risks = Build(Fixture("legacy-web")).Risks;

        // Lines in the .csproj
        Assert.Contains(risks, r => r is { RuleId: "aspnet-web-application-project", Kind: "project_type", File: "Legacy.Web.csproj", Line: 15 });
        Assert.Contains(risks, r => r is { RuleId: "aspnet-web-application-project", Kind: "import", Line: 20 });
        Assert.Contains(risks, r => r is { RuleId: "com-interop", Kind: "item", Line: 18, Match: "COMReference Scripting" });

        // File rules give one hit per project and pattern, with a count.
        var views = Assert.Single(risks, r => r is { RuleId: "aspnet-mvc", Kind: "file" });
        Assert.Equal("*.cshtml", views.Match);
        Assert.Equal(2, views.Count);
        Assert.Equal("Views/Home/About.cshtml", views.File);

        Assert.Contains(risks, r => r is { RuleId: "aspnet-web-application-project", Kind: "file", Match: "Global.asax", Count: 1 });
        // Web.*.config matches Web.Debug.config but not Web.config, so each file is counted once.
        Assert.Contains(risks, r => r is { RuleId: "web-config", Match: "Web.config", Count: 1 });
        Assert.Contains(risks, r => r is { RuleId: "web-config", Match: "Web.*.config", Count: 1 });
        Assert.Contains(risks, r => r is { RuleId: "packages-config", Kind: "file", File: "packages.config" });
        Assert.Contains(risks, r => r is { RuleId: "assemblyinfo-file", Kind: "file", File: "Properties/AssemblyInfo.cs" });
    }

    [Fact]
    public void MultiProjectRepo_SplitsFilesByProjectAndSkipsBuildFolders()
    {
        var repo = Fixture("multi");
        // bin/ and obj/ are git-ignored, so create them here.
        foreach (var dir in new[] { "bin/Release", "obj" })
        {
            Directory.CreateDirectory(Path.Combine(repo, "Core", dir));
            File.WriteAllText(Path.Combine(repo, "Core", dir, "Generated.cs"), "class G { System.Web.HttpContext c; }");
        }

        var inventory = Build(repo);

        Assert.Equal(["Core", "Desktop"], inventory.Projects.Select(p => p.Name));
        var core = inventory.Projects[0];
        var desktop = inventory.Projects[1];
        Assert.Equal(1, core.SourceFileCount);
        Assert.Equal(["Core/Core.csproj"], desktop.ProjectReferences);
        Assert.Equal(["net8.0-windows"], desktop.TargetFrameworks);
        Assert.Equal("WinExe", desktop.OutputType);

        Assert.All(inventory.Risks, r => Assert.Equal("Desktop/Desktop.csproj", r.Project));
        Assert.Contains(inventory.Risks, r => r is { RuleId: "wpf", Kind: "property", File: "Desktop/Desktop.csproj" });
        // The longest prefix wins: System.Windows.Forms is WinForms, not WPF.
        Assert.Contains(inventory.Risks, r => r is { RuleId: "wpf", Kind: "using", Line: 1 });
        Assert.Contains(inventory.Risks, r => r is { RuleId: "winforms", Kind: "using", Line: 2 });
        Assert.DoesNotContain(inventory.Risks, r => r is { RuleId: "wpf", Line: 2, Kind: "using" });
    }

    [Fact]
    public void Summary_CountsHitsPerRuleAndPutsHighSeverityFirst()
    {
        var inventory = Build(Fixture("legacy-web"));

        Assert.Equal("high", inventory.RiskSummary[0].Severity);
        var web = inventory.RiskSummary.Single(s => s.RuleId == "system-web");
        Assert.Equal(inventory.Risks.Count(r => r.RuleId == "system-web"), web.Count);
        // File hits count every file they matched.
        Assert.Equal(inventory.Risks.Sum(r => r.Count ?? 1), inventory.RiskSummary.Sum(s => s.Count));
    }

    [Fact]
    public void Output_IsTheSameEveryTime()
    {
        var repo = Fixture("legacy-web");
        var first = JsonSerializer.Serialize(Build(repo), InventoryJson.Options);
        var second = JsonSerializer.Serialize(Build(repo), InventoryJson.Options);
        Assert.Equal(first, second);
    }

    [Fact]
    public void Cli_PrintsSnakeCaseJsonToStdoutAndKeepsStderrForWarnings()
    {
        var repo = Fixture("sdk-lib");
        File.WriteAllText(Path.Combine(repo, "Old.vbproj"), "<Project />");
        var stdout = new StringWriter();
        var stderr = new StringWriter();

        var exitCode = Cli.Run(["inventory", repo], stdout, stderr);

        Assert.Equal(0, exitCode);
        using var doc = JsonDocument.Parse(stdout.ToString()); // stdout must be pure JSON
        var project = doc.RootElement.GetProperty("projects")[0];
        Assert.Equal("Lib", project.GetProperty("name").GetString());
        Assert.Equal(2, project.GetProperty("target_frameworks").GetArrayLength());
        Assert.True(doc.RootElement.TryGetProperty("risk_summary", out _));
        Assert.Contains("Old.vbproj", stderr.ToString());
    }

    [Fact]
    public void Cli_WithMissingFolder_FailsWithMessageOnStderr()
    {
        var stdout = new StringWriter();
        var stderr = new StringWriter();

        var exitCode = Cli.Run(["inventory", Path.Combine(_temp, "nope")], stdout, stderr);

        Assert.Equal(2, exitCode);
        Assert.Empty(stdout.ToString());
        Assert.Contains("not found", stderr.ToString());
    }

    [Fact]
    public void Cli_InventoryWithoutRepo_PrintsUsage()
    {
        var stdout = new StringWriter();
        var stderr = new StringWriter();

        Assert.Equal(1, Cli.Run(["inventory"], stdout, stderr));
        Assert.Contains("usage:", stderr.ToString());
    }

    [Fact]
    public void CustomRiskFile_ReplacesTheDefaultList()
    {
        var repo = Fixture("sdk-lib");
        var risks = Path.Combine(_temp, "risks.json");
        Directory.CreateDirectory(_temp);
        File.WriteAllText(risks, """{ "rules": [ { "id": "only-this", "types": ["BinaryFormatter"] } ] }""");
        var stdout = new StringWriter();

        Assert.Equal(0, Cli.Run(["inventory", repo, "--risks", risks], stdout, new StringWriter()));

        using var doc = JsonDocument.Parse(stdout.ToString());
        var summary = doc.RootElement.GetProperty("risk_summary");
        Assert.Equal(1, summary.GetArrayLength());
        Assert.Equal("only-this", summary[0].GetProperty("rule_id").GetString());
    }

    [Fact]
    public void RiskCatalog_RejectsAThingListedInTwoRules()
    {
        var ex = Assert.Throws<InvalidDataException>(() => new RiskCatalog(
        [
            new RiskRule { Id = "a", Types = ["Foo"] },
            new RiskRule { Id = "b", Types = ["Foo"] },
        ]));
        Assert.Contains("'Foo'", ex.Message);
    }

    [Fact]
    public void RiskCatalog_RejectsUnknownSeverity()
    {
        Assert.Throws<InvalidDataException>(() => new RiskCatalog([new RiskRule { Id = "a", Severity = "huge" }]));
    }

    [Fact]
    public void RiskCatalog_MatchesFilesWithWildcardsIgnoringCase()
    {
        var catalog = RiskCatalog.LoadDefault();
        Assert.Contains(catalog.MatchFile("Default.ASPX"), m => m.Rule.Id == "aspnet-webforms");
        Assert.Contains(catalog.MatchFile("web.config"), m => m.Rule.Id == "web-config");
        Assert.Empty(catalog.MatchFile("Web.cs"));
    }

    [Fact]
    public void RiskCatalog_RejectsDuplicateIds()
    {
        Assert.Throws<InvalidDataException>(() =>
            new RiskCatalog([new RiskRule { Id = "a" }, new RiskRule { Id = "a" }]));
    }

    [Theory]
    [InlineData("System.Web", "system-web")]
    [InlineData("System.Web.HttpContext.Current", "system-web")]
    [InlineData("System.Web.Mvc", "aspnet-mvc")]
    [InlineData("System.Web.Http.ApiController", "aspnet-webapi")]
    [InlineData("System.Web.Http.OData", "aspnet-odata")]
    [InlineData("System.Web.UI.WebControls", "aspnet-webforms")]
    [InlineData("System.Web.UI.DataVisualization.Charting", "chart-controls")]
    [InlineData("System.Web.Security", "aspnet-membership")]
    [InlineData("System.Web.Services.Protocols", "asmx-web-services")]
    [InlineData("System.ServiceModel.Activities", "workflow-foundation")]
    [InlineData("System.ServiceModel.Channels", "wcf")]
    [InlineData("System.Configuration.Install", "installer-classes")]
    [InlineData("System.Configuration", "system-configuration")]
    [InlineData("System.Windows.Browser", "silverlight")]
    [InlineData("System.Drawing.Imaging", "system-drawing")]
    [InlineData("System.Drawing.Design", "winforms")]
    [InlineData("Microsoft.Office.Interop.Excel", "office-interop")]
    [InlineData("Microsoft.Office.Tools.Excel", "vsto")]
    [InlineData("Microsoft.AspNetCore.Http", null)]
    [InlineData("Microsoft.AspNet.Identity", "aspnet-identity")]
    [InlineData("System.Windows.Forms.Form", "winforms")]
    [InlineData("System.Windows.Controls", "wpf")]
    [InlineData("System.WebSockets", null)]
    [InlineData("System.Text.Json", null)]
    public void RiskCatalog_MatchesNamespacesOnDotBoundariesWithLongestPrefix(string name, string? expected)
    {
        Assert.Equal(expected, RiskCatalog.LoadDefault().MatchNamespace(name)?.Id);
    }

    [Fact]
    public void DefaultRiskFile_HasUniqueIdsAndEveryRuleMatchesSomething()
    {
        var catalog = RiskCatalog.LoadDefault();
        Assert.NotEmpty(catalog.Rules);
        Assert.All(catalog.Rules, rule =>
        {
            var count = rule.Namespaces.Count + rule.Types.Count + rule.Assemblies.Count + rule.Packages.Count + rule.Properties.Count
                        + rule.Items.Count + rule.Imports.Count + rule.ProjectTypeGuids.Count + rule.Files.Count;
            Assert.True(count > 0, $"rule '{rule.Id}' has nothing to match");
            Assert.NotEmpty(rule.Description);
        });
    }
}
