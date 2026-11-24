using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("GXUST电费助手 安装程序")]
[assembly: AssemblyDescription("安装 GXUST电费助手")]
[assembly: AssemblyCompany("smg123")]
[assembly: AssemblyProduct("GXUST电费助手")]
[assembly: AssemblyCopyright("smg123")]
[assembly: AssemblyFileVersion("1.0.1.0")]
[assembly: AssemblyVersion("1.0.1.0")]

internal static class SetupLauncher
{
    [STAThread]
    private static void Main()
    {
        try
        {
            string msi = ExtractMsi();
            Process.Start(new ProcessStartInfo("msiexec.exe", "/i \"" + msi + "\"")
            {
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            MessageBox.Show("安装程序启动失败：\\n" + ex.Message, "GXUST电费助手",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private static string ExtractMsi()
    {
        Exception last = null;
        string[] dirs =
        {
            Path.Combine(Path.GetTempPath(), "GXUSTPowerSetup"),
            AppDomain.CurrentDomain.BaseDirectory
        };
        foreach (string dir in dirs)
        {
            try
            {
                Directory.CreateDirectory(dir);
                string msi = Path.Combine(dir,
                    "GXUSTPower-" + Guid.NewGuid().ToString("N") + ".msi");
                using (Stream stream = Assembly.GetExecutingAssembly()
                           .GetManifestResourceStream("GXUSTPower.msi"))
                {
                    if (stream == null) throw new InvalidOperationException("安装包资源缺失");
                    using (FileStream file = File.Create(msi))
                    {
                        stream.CopyTo(file);
                    }
                }
                return msi;
            }
            catch (Exception ex)
            {
                last = ex;
            }
        }
        throw new InvalidOperationException("无法释放安装包", last);
    }
}
