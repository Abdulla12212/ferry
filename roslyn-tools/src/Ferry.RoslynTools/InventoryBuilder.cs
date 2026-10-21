namespace Ferry.RoslynTools;

/// <summary>Scans a repo folder and builds the facts: projects, packages, risky APIs.</summary>
public static class InventoryBuilder
{
    private static readonly HashSet<string> SkippedDirectories =
        new(StringComparer.OrdinalIgnoreCase) { ".git", ".vs", "bin", "obj", "node_modules" };

    private static readonly string[] OtherProjectExtensions = [".vbproj", ".fsproj"];

    private static readonly string[] SeverityOrder = ["high", "medium", "low"];

    public static Inventory Build(string repoPath, RiskCatalog risks, Action<string> warn)
    {
        var repo = Path.GetFullPath(repoPath);
        if (!Directory.Exists(repo))
            throw new DirectoryNotFoundException($"Repo folder not found: {repoPath}");

        var projectFiles = new List<string>();
        var otherFiles = new List<string>();
        Walk(repo, repo, projectFiles, otherFiles, warn);

        var parsed = new List<ParsedProject>();
        foreach (var file in projectFiles.Order(StringComparer.Ordinal))
        {
            try
            {
                parsed.Add(ProjectReader.Read(file, risks));
            }
            catch (Exception ex) when (ex is System.Xml.XmlException or InvalidDataException or IOException)
            {
                warn($"skipped {Rel(repo, file)}: {ex.Message}");
            }
        }

        // A file belongs to the project whose folder is the closest parent.
        var filesByProject = parsed.ToDictionary(p => p.FullPath, _ => new List<string>());
        var otherByProject = parsed.ToDictionary(p => p.FullPath, _ => new List<string>());
        foreach (var file in otherFiles)
        {
            var owner = parsed
                .Where(p => IsUnder(Path.GetDirectoryName(p.FullPath)!, file))
                .OrderByDescending(p => Path.GetDirectoryName(p.FullPath)!.Length)
                .FirstOrDefault();
            if (owner is null) continue;
            otherByProject[owner.FullPath].Add(file);
            if (file.EndsWith(".cs", StringComparison.OrdinalIgnoreCase)) filesByProject[owner.FullPath].Add(file);
        }

        var projects = new List<ProjectInfo>();
        var hits = new List<RiskHit>();

        foreach (var project in parsed)
        {
            var projectRel = Rel(repo, project.FullPath);
            var sources = filesByProject[project.FullPath].Order(StringComparer.Ordinal).ToList();

            foreach (var f in project.Findings)
                hits.Add(new RiskHit(f.Rule.Id, f.Kind, projectRel, Rel(repo, f.FullPath), f.Line, f.Match, null));

            // Files like *.aspx or Web.config: one hit per project and pattern, with a count.
            var fileMatches = otherByProject[project.FullPath]
                .SelectMany(file => risks.MatchFile(Path.GetFileName(file)).Select(m => (File: file, m.Rule, m.Pattern)))
                .GroupBy(x => (x.Rule, x.Pattern));
            foreach (var group in fileMatches)
            {
                var first = group.Select(x => x.File).Order(StringComparer.Ordinal).First();
                hits.Add(new RiskHit(group.Key.Rule.Id, "file", projectRel, Rel(repo, first), 1, group.Key.Pattern, null, group.Count()));
            }

            foreach (var source in sources)
            {
                try
                {
                    foreach (var f in SourceScanner.Scan(source, risks))
                        hits.Add(new RiskHit(f.Rule.Id, f.Kind, projectRel, Rel(repo, source), f.Line, f.Match, f.Text));
                }
                catch (IOException ex)
                {
                    warn($"skipped {Rel(repo, source)}: {ex.Message}");
                }
            }

            projects.Add(new ProjectInfo(
                project.Name,
                projectRel,
                project.SdkStyle,
                project.OutputType,
                project.TargetFrameworks,
                project.Packages,
                project.ProjectReferenceFullPaths
                    .Where(parsedPath => File.Exists(parsedPath))
                    .Select(parsedPath => Rel(repo, parsedPath))
                    .ToList(),
                sources.Count));
        }

        var sortedHits = hits
            .OrderBy(h => h.Project, StringComparer.Ordinal)
            .ThenBy(h => h.File, StringComparer.Ordinal)
            .ThenBy(h => h.Line)
            .ThenBy(h => h.RuleId, StringComparer.Ordinal)
            .ToList();

        return new Inventory(Path.GetFileName(repo.TrimEnd(Path.DirectorySeparatorChar)), projects, sortedHits, Summarize(sortedHits, risks));
    }

    private static List<RiskSummary> Summarize(List<RiskHit> hits, RiskCatalog risks) =>
        hits.GroupBy(h => h.RuleId)
            .Select(g => (Rule: risks.ById(g.Key)!, Count: g.Sum(h => h.Count ?? 1)))
            .OrderBy(x => SeverityRank(x.Rule.Severity))
            .ThenByDescending(x => x.Count)
            .ThenBy(x => x.Rule.Id, StringComparer.Ordinal)
            .Select(x => new RiskSummary(x.Rule.Id, x.Rule.Category, x.Rule.Severity, x.Rule.Description, x.Count))
            .ToList();

    private static int SeverityRank(string severity)
    {
        var i = Array.IndexOf(SeverityOrder, severity.ToLowerInvariant());
        return i < 0 ? SeverityOrder.Length : i;
    }

    private static void Walk(string root, string dir, List<string> projects, List<string> others, Action<string> warn)
    {
        foreach (var file in Directory.EnumerateFiles(dir))
        {
            var ext = Path.GetExtension(file);
            if (ext.Equals(".csproj", StringComparison.OrdinalIgnoreCase)) projects.Add(file);
            else
            {
                if (OtherProjectExtensions.Contains(ext, StringComparer.OrdinalIgnoreCase))
                    warn($"skipped {Rel(root, file)}: only C# projects are supported");
                others.Add(file);
            }
        }

        foreach (var sub in Directory.EnumerateDirectories(dir))
        {
            var name = Path.GetFileName(sub);
            if (SkippedDirectories.Contains(name)) continue;
            // Restored NuGet packages from old-style projects live in <repo>/packages.
            if (dir == root && name.Equals("packages", StringComparison.OrdinalIgnoreCase)) continue;
            Walk(root, sub, projects, others, warn);
        }
    }

    private static bool IsUnder(string dir, string file) =>
        file.StartsWith(dir.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.Ordinal);

    private static string Rel(string root, string path) =>
        Path.GetRelativePath(root, path).Replace(Path.DirectorySeparatorChar, '/');
}
