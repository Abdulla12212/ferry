namespace Ferry.RoslynTools.Tests;

public class CliTests
{
    [Fact]
    public void Run_WithVersionFlag_PrintsVersionAndReturnsZero()
    {
        var stdout = new StringWriter();
        var stderr = new StringWriter();

        var exitCode = Cli.Run(["--version"], stdout, stderr);

        Assert.Equal(0, exitCode);
        Assert.StartsWith("0.1.0", stdout.ToString());
        Assert.Empty(stderr.ToString());
    }

    [Fact]
    public void Run_WithNoArguments_PrintsUsageToStderrAndReturnsOne()
    {
        var stdout = new StringWriter();
        var stderr = new StringWriter();

        var exitCode = Cli.Run([], stdout, stderr);

        Assert.Equal(1, exitCode);
        Assert.Empty(stdout.ToString());
        Assert.Contains("usage:", stderr.ToString());
    }
}
