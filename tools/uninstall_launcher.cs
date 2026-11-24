using System;
using System.Diagnostics;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("GXUST电费助手 卸载程序")]
[assembly: AssemblyDescription("卸载 GXUST电费助手")]
[assembly: AssemblyCompany("smg123")]
[assembly: AssemblyProduct("GXUST电费助手")]
[assembly: AssemblyCopyright("smg123")]
[assembly: AssemblyFileVersion("1.0.0.0")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class UninstallLauncher
{
    private const string ProductCode = "{990BC64E-0CA4-464D-B5B1-AA8A54FC3118}";

    [STAThread]
    private static void Main()
    {
        try
        {
            Process.Start(new ProcessStartInfo("msiexec.exe",
                "/x " + ProductCode) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            MessageBox.Show("卸载程序启动失败：\\n" + ex.Message, "GXUST电费助手",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
