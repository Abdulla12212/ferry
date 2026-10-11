using System.Reflection;

namespace Ferry.RoslynTools;

public static class Cli
{
    public const string Usage = "usage: Ferry.RoslynTools --version";

    public static string Version =>
        typeof(Cli).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
        ?? "unknown";

    public static int Run(string[] args, TextWriter stdout, TextWriter stderr)
    {
        switch (args)
        {
            case ["--version"]:
                stdout.WriteLine(Version);
                return 0;
            default:
                stderr.WriteLine(Usage);
                return 1;
        }
    }
}
