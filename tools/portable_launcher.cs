using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("GXUST电费助手 便携版")]
[assembly: AssemblyDescription("GXUST电费助手 单文件便携版")]
[assembly: AssemblyCompany("smg123")]
[assembly: AssemblyProduct("GXUST电费助手")]
[assembly: AssemblyCopyright("smg123")]
[assembly: AssemblyFileVersion("1.0.0.0")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class PortableLauncher
{
    [STAThread]
    private static void Main()
    {
        try
        {
            string dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "GXUSTPowerPortable");
            Directory.CreateDirectory(dir);
            using (Stream stream = Assembly.GetExecutingAssembly()
                       .GetManifestResourceStream("GXUSTPower.zip"))
            {
                if (stream == null) throw new InvalidOperationException("便携包资源缺失");
                using (var archive = new ZipArchive(stream, ZipArchiveMode.Read))
                {
                    foreach (ZipArchiveEntry entry in archive.Entries)
                    {
                        if (string.IsNullOrEmpty(entry.Name)) continue;
                        string target = Path.Combine(dir,
                            entry.FullName.Replace('/', Path.DirectorySeparatorChar));
                        Directory.CreateDirectory(Path.GetDirectoryName(target));
                        entry.ExtractToFile(target, true);
                    }
                }
            }
            Process.Start(new ProcessStartInfo(Path.Combine(dir, "GXUSTPower.exe"))
            {
                WorkingDirectory = dir,
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            MessageBox.Show("便携版启动失败：\n" + ex.Message, "GXUST电费助手",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
