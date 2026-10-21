namespace Ferry.RoslynTools;

public static class Program
{
    public static Task<int> Main(string[] args) =>
        args is ["mcp"]
            ? McpServer.RunAsync(args)
            : Task.FromResult(Cli.Run(args, Console.Out, Console.Error));
}
