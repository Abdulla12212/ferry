using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace Ferry.RoslynTools;

/// <summary>A match inside one C# file, before it is tied to a project.</summary>
public sealed record SourceFinding(RiskRule Rule, string Kind, int Line, string Match, string Text);

/// <summary>
/// Parses a C# file with Roslyn and finds risky APIs. It reads syntax only (no compilation),
/// so it needs no restore or build, and it ignores comments and strings.
/// </summary>
public static class SourceScanner
{
    public static IReadOnlyList<SourceFinding> Scan(string path, RiskCatalog risks)
    {
        var text = File.ReadAllText(path);
        var tree = CSharpSyntaxTree.ParseText(text, path: path);
        var root = tree.GetRoot();
        var lines = tree.GetText().Lines;

        var findings = new List<SourceFinding>();
        // One finding per rule per line is enough; five uses of BinaryFormatter on one line are one problem.
        var seen = new HashSet<(string RuleId, int Line)>();

        void Add(RiskRule rule, string kind, SyntaxNode node, string match)
        {
            var line = node.GetLocation().GetLineSpan().StartLinePosition.Line;
            if (!seen.Add((rule.Id, line))) return;
            findings.Add(new SourceFinding(rule, kind, line + 1, match, lines[line].ToString().Trim()));
        }

        foreach (var node in root.DescendantNodes())
        {
            switch (node)
            {
                case UsingDirectiveSyntax { Name: { } name }:
                    var usingName = Compact(name);
                    if (risks.MatchNamespace(usingName) is { } usingRule)
                        Add(usingRule, "using", node, usingName);
                    break;

                // System.Web.HttpContext.Current: only look at the outermost dotted name.
                case QualifiedNameSyntax or MemberAccessExpressionSyntax when IsOutermostDottedName(node) && !InsideUsing(node):
                    var dotted = Compact(node);
                    if (risks.MatchNamespace(dotted) is { } dottedRule)
                        Add(dottedRule, "qualified_name", node, dotted);
                    break;

                case IdentifierNameSyntax id when !InsideUsing(id):
                    if (risks.MatchType(id.Identifier.ValueText) is { } typeRule)
                        Add(typeRule, "type", node, id.Identifier.ValueText);
                    break;
            }
        }

        return findings;
    }

    // A.B.C is parsed as ((A.B).C). Only the outermost node, A.B.C, is worth matching.
    private static bool IsOutermostDottedName(SyntaxNode node) => node.Parent switch
    {
        MemberAccessExpressionSyntax access => access.Expression != node,
        QualifiedNameSyntax qualified => qualified.Left != node,
        _ => true,
    };

    private static bool InsideUsing(SyntaxNode node) => node.Ancestors().Any(a => a is UsingDirectiveSyntax);

    private static string Compact(SyntaxNode node) => string.Concat(node.ToString().Where(c => !char.IsWhiteSpace(c)));
}
