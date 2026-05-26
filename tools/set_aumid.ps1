param(
    [Parameter(Mandatory=$true)][string]$LnkPath,
    [Parameter(Mandatory=$true)][string]$AUMID
)

# Sets System.AppUserModel.ID on a .lnk so Windows taskbar pinning groups
# the app under its own identity (and keeps the shortcut's icon) instead
# of falling back to the target exe (pythonw.exe).

try {
    $LnkPath = (Resolve-Path -LiteralPath $LnkPath -ErrorAction Stop).Path
} catch {
    Write-Host "[WARN] Shortcut not found: $LnkPath"
    exit 1
}

$code = @'
using System;
using System.Runtime.InteropServices;

namespace LnkAumid {
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PROPERTYKEY {
        public Guid fmtid;
        public uint pid;
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct PROPVARIANT {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(2)] public ushort r1;
        [FieldOffset(4)] public ushort r2;
        [FieldOffset(6)] public ushort r3;
        [FieldOffset(8)] public IntPtr p;
        [FieldOffset(16)] public int p2;
    }

    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        void GetCount(out uint cProps);
        void GetAt(uint iProp, out PROPERTYKEY pkey);
        void GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
        void SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
        void Commit();
    }

    [ComImport, Guid("0000010b-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPersistFile {
        void GetClassID(out Guid pClassID);
        [PreserveSig] int IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName,
                  [MarshalAs(UnmanagedType.Bool)] bool fRemember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
        void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
    }

    public static class Api {
        [DllImport("ole32.dll")]
        public static extern int CoCreateInstance(
            ref Guid rclsid, IntPtr pUnkOuter, uint dwClsContext,
            ref Guid riid, out IntPtr ppv);

        [DllImport("ole32.dll")]
        public static extern int PropVariantClear(ref PROPVARIANT pvar);

        public static void SetShortcutAumid(string lnk, string aumid) {
            Guid CLSID_ShellLink = new Guid("00021401-0000-0000-C000-000000000046");
            Guid IID_IPersistFile = new Guid("0000010b-0000-0000-C000-000000000046");

            IntPtr pUnk;
            int hr = CoCreateInstance(ref CLSID_ShellLink, IntPtr.Zero, 1,
                                      ref IID_IPersistFile, out pUnk);
            if (hr != 0) throw new COMException("CoCreateInstance failed", hr);

            IPersistFile pf = (IPersistFile)Marshal.GetObjectForIUnknown(pUnk);
            pf.Load(lnk, 2); // STGM_READWRITE

            IPropertyStore store = (IPropertyStore)pf;
            PROPERTYKEY key = new PROPERTYKEY {
                fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
                pid = 5
            };
            PROPVARIANT pv = new PROPVARIANT {
                vt = 31, // VT_LPWSTR
                p = Marshal.StringToCoTaskMemUni(aumid)
            };
            try {
                store.SetValue(ref key, ref pv);
                store.Commit();
                pf.Save(lnk, true);
            } finally {
                PropVariantClear(ref pv);
                Marshal.ReleaseComObject(store);
                Marshal.Release(pUnk);
            }
        }
    }
}
'@

Add-Type -TypeDefinition $code -Language CSharp | Out-Null

try {
    [LnkAumid.Api]::SetShortcutAumid($LnkPath, $AUMID)
    Write-Host "[OK] AUMID '$AUMID' set on $LnkPath"
    exit 0
} catch {
    Write-Host "[WARN] Failed to set AUMID: $($_.Exception.Message)"
    exit 1
}
