# set_aumid.ps1
# Sets the AppUserModelID property on a .lnk shortcut so Windows groups
# the app under its own taskbar icon instead of merging with python.exe.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File set_aumid.ps1 `
#     -LnkPath "C:\Users\<user>\Desktop\Oracle Tasks Chile.lnk" `
#     -AUMID   "Oracle.OracleTasksChile.1"

param(
    [Parameter(Mandatory=$true)] [string] $LnkPath,
    [Parameter(Mandatory=$true)] [string] $AUMID
)

if (-not (Test-Path -LiteralPath $LnkPath)) {
    Write-Error "Shortcut not found: $LnkPath"
    exit 1
}

# PKEY_AppUserModel_ID = {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, 5
$signature = @'
using System;
using System.Runtime.InteropServices;

public static class ShortcutAUMID
{
    [StructLayout(LayoutKind.Sequential)]
    public struct PROPERTYKEY
    {
        public Guid fmtid;
        public uint pid;
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct PROPVARIANT
    {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr unionPtr;
    }

    [DllImport("ole32.dll")]
    public static extern int CoCreateInstance(
        [In] ref Guid clsid, IntPtr unkOuter, uint dwClsContext,
        [In] ref Guid iid, out IntPtr ppv);

    [DllImport("ole32.dll")]
    public static extern void CoUninitialize();
}
'@

# Use the COM-based PropertyStore approach via InvokeMember on Shell.Application — simpler.
# We rely on the IShellLinkW + IPropertyStore COM interfaces exposed by the Shell.
# Easiest route: use the Windows API Code Pack via PowerShell's built-in COM glue.

try {
    $shellApp = New-Object -ComObject Shell.Application
    $folder   = Split-Path -Parent $LnkPath
    $file     = Split-Path -Leaf   $LnkPath
    $ns       = $shellApp.Namespace($folder)
    $item     = $ns.ParseName($file)
    if ($null -eq $item) {
        Write-Error "Could not bind to shortcut item."
        exit 1
    }

    # PowerShell 5.1 has no direct IPropertyStore wrapper, so we shell out to a
    # tiny C# helper compiled inline.
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
internal class CShellLink { }

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
 Guid("000214F9-0000-0000-C000-000000000046")]
internal interface IShellLinkW
{
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszFile,
        int cch, IntPtr pfd, uint fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszName, int cch);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszDir, int cch);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszArgs, int cch);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int iShowCmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszIconPath, int cch, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
    void Resolve(IntPtr hwnd, uint fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
 Guid("0000010b-0000-0000-C000-000000000046")]
internal interface IPersistFile
{
    void GetClassID(out Guid pClassID);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, [MarshalAs(UnmanagedType.Bool)] bool fRemember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
public struct PROPERTYKEY
{
    public Guid fmtid;
    public uint pid;
}

[StructLayout(LayoutKind.Explicit)]
public struct PROPVARIANT
{
    [FieldOffset(0)] public ushort vt;
    [FieldOffset(2)] public ushort wReserved1;
    [FieldOffset(4)] public ushort wReserved2;
    [FieldOffset(6)] public ushort wReserved3;
    [FieldOffset(8)] public IntPtr p;
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
 Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")]
internal interface IPropertyStore
{
    void GetCount(out uint cProps);
    void GetAt(uint iProp, out PROPERTYKEY pkey);
    void GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
    void SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
    void Commit();
}

public static class AUMID
{
    [DllImport("ole32.dll")]
    public static extern int PropVariantClear(ref PROPVARIANT pvar);
    [DllImport("ole32.dll", CharSet = CharSet.Unicode)]
    public static extern int InitPropVariantFromString([MarshalAs(UnmanagedType.LPWStr)] string psz, out PROPVARIANT ppropvar);

    public static void Set(string lnkPath, string aumid)
    {
        IShellLinkW link = (IShellLinkW)(new CShellLink());
        IPersistFile pf = (IPersistFile)link;
        pf.Load(lnkPath, 2 /* STGM_READWRITE */);

        IPropertyStore ps = (IPropertyStore)link;
        PROPERTYKEY key = new PROPERTYKEY {
            fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
            pid   = 5
        };
        PROPVARIANT pv;
        InitPropVariantFromString(aumid, out pv);
        ps.SetValue(ref key, ref pv);
        ps.Commit();
        PropVariantClear(ref pv);

        pf.Save(lnkPath, true);

        Marshal.ReleaseComObject(ps);
        Marshal.ReleaseComObject(pf);
        Marshal.ReleaseComObject(link);
    }
}
'@ -Language CSharp

    [AUMID]::Set($LnkPath, $AUMID)
    Write-Host "OK: AppUserModelID '$AUMID' set on $LnkPath"
    exit 0
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
