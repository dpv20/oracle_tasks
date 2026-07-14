"""Privacy-conscious diagnostics for Classic and New Outlook automation."""
from __future__ import annotations

import os
import platform
import shutil
import struct
import sys
import threading
from datetime import datetime
from importlib import metadata
from pathlib import Path


_OUTLOOK_PROCESS_NAMES = {
    "outlook.exe",
    "olk.exe",
    "hxoutlook.exe",
    "microsoft.outlookforwindows.exe",
}


def log_outlook_environment(logger, *, stage: str) -> None:
    """Log enough installation/runtime context to diagnose Outlook differences."""
    try:
        logger.info(
            "outlook_diag: stage=%s pid=%s thread=%r cwd=%s python=%s executable=%s "
            "architecture=%s os=%s machine=%s",
            stage,
            os.getpid(),
            threading.current_thread().name,
            Path.cwd(),
            platform.python_version(),
            sys.executable,
            struct.calcsize("P") * 8,
            platform.platform(),
            platform.node() or "<unknown>",
        )
        logger.info(
            "outlook_diag: COM coinit_flags=%r main_thread=%s",
            getattr(sys, "coinit_flags", "<unset>"),
            threading.current_thread() is threading.main_thread(),
        )
        _log_dependency_versions(logger)
        logger.info(
            "outlook_diag: environment LOCALAPPDATA=%s ProgramFiles=%s ProgramFiles(x86)=%s",
            os.environ.get("LOCALAPPDATA", "<unset>"),
            os.environ.get("ProgramFiles", "<unset>"),
            os.environ.get("ProgramFiles(x86)", "<unset>"),
        )
        _log_mail_registry(logger)
        _log_outlook_executables(logger)
        log_outlook_processes(logger, stage=stage)
    except Exception:
        logger.warning("outlook_diag: environment collection failed stage=%s", stage, exc_info=True)


def log_outlook_processes(logger, *, stage: str) -> None:
    try:
        import psutil
    except ImportError:
        logger.warning("outlook_diag: psutil unavailable; process inventory skipped stage=%s", stage)
        return

    snapshots: list[str] = []
    try:
        processes = psutil.process_iter(["pid", "name", "exe", "status", "create_time"])
        for process in processes:
            try:
                info = process.info
                name = str(info.get("name") or "")
                if name.casefold() not in _OUTLOOK_PROCESS_NAMES:
                    continue
                created = info.get("create_time")
                created_text = (
                    datetime.fromtimestamp(float(created)).isoformat(timespec="seconds")
                    if created
                    else "<unknown>"
                )
                snapshots.append(
                    f"pid={info.get('pid')} name={name!r} status={info.get('status')!r} "
                    f"created={created_text} exe={info.get('exe') or '<unavailable>'}"
                )
            except (OSError, psutil.Error):
                continue
    except Exception:
        logger.warning("outlook_diag: process inventory failed stage=%s", stage, exc_info=True)
        return

    if snapshots:
        for snapshot in snapshots:
            logger.info("outlook_diag: process stage=%s %s", stage, snapshot)
    else:
        logger.info("outlook_diag: process stage=%s <none>", stage)


def describe_com_object(value) -> str:
    if value is None:
        return "<none>"
    details = [
        f"python_type={type(value).__module__}.{type(value).__name__}",
        f"com_name={_safe_attr(value, '_username_') or '<unknown>'}",
    ]
    for attribute in ("Name", "Version", "ProductCode"):
        result = _safe_attr(value, attribute)
        if result:
            details.append(f"{attribute}={result!r}")
    return " ".join(details)


def describe_mapi_namespace(namespace, *, include_collections: bool = False) -> str:
    if namespace is None:
        return "<none>"
    details = [f"python_type={type(namespace).__module__}.{type(namespace).__name__}"]
    for attribute in (
        "Name",
        "CurrentProfileName",
        "ExchangeConnectionMode",
        "Offline",
    ):
        result = _safe_attr(namespace, attribute)
        if result != "":
            details.append(f"{attribute}={result!r}")
    if include_collections:
        for attribute in ("Accounts", "Stores"):
            collection = _safe_object_attr(namespace, attribute)
            count = _safe_attr(collection, "Count") if collection is not None else ""
            if count != "":
                details.append(f"{attribute}.Count={count!r}")
    return " ".join(details)


def log_outlook_uia_windows(logger, desktop, *, stage: str) -> None:
    """Log only Outlook/profile top-level windows to avoid collecting unrelated titles."""
    try:
        windows = desktop.windows()
    except Exception:
        logger.warning("outlook_diag: UIA window inventory failed stage=%s", stage, exc_info=True)
        return

    matched = 0
    for window in windows:
        try:
            title = str(window.window_text() or "").strip()
            class_name = str(window.element_info.class_name or "")
            searchable = f"{title} {class_name}".casefold()
            if not any(
                token in searchable
                for token in ("outlook", "outlook host", "choose profile", "elegir perfil", "seleccionar perfil")
            ) and title.casefold() != "microsoft":
                continue
            matched += 1
            descendants = window.descendants()
            control_types: dict[str, int] = {}
            for control in descendants:
                control_type = str(control.element_info.control_type or "<unknown>")
                control_types[control_type] = control_types.get(control_type, 0) + 1
            logger.info(
                "outlook_diag: window stage=%s handle=%s title=%r class=%r visible=%r "
                "enabled=%r controls=%s control_types=%s",
                stage,
                getattr(window, "handle", "<unknown>"),
                title,
                class_name,
                _safe_call(window, "is_visible"),
                _safe_call(window, "is_enabled"),
                len(descendants),
                control_types,
            )
        except Exception:
            logger.debug("outlook_diag: one UIA window changed during inventory", exc_info=True)
    if not matched:
        logger.info("outlook_diag: window stage=%s <no Outlook/profile windows>", stage)


def _log_mail_registry(logger) -> None:
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        logger.warning("outlook_diag: winreg unavailable")
        return

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Clients\Mail") as key:
            default_mail = winreg.QueryValue(key, None)
    except OSError:
        default_mail = "<missing>"
    logger.info("outlook_diag: registry default_mail_client=%r", default_mail)

    app_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE"
    for root_name, root in (
        ("HKCU", winreg.HKEY_CURRENT_USER),
        ("HKLM", winreg.HKEY_LOCAL_MACHINE),
    ):
        try:
            with winreg.OpenKey(root, app_path) as key:
                value = winreg.QueryValue(key, None)
        except OSError:
            value = "<missing>"
        logger.info("outlook_diag: registry %s AppPaths/OUTLOOK.EXE=%r", root_name, value)

    for view_name, view_flag in (
        ("64-bit", getattr(winreg, "KEY_WOW64_64KEY", 0)),
        ("32-bit", getattr(winreg, "KEY_WOW64_32KEY", 0)),
    ):
        access = winreg.KEY_READ | view_flag
        try:
            with winreg.OpenKey(
                winreg.HKEY_CLASSES_ROOT,
                r"Outlook.Application\CLSID",
                0,
                access,
            ) as key:
                clsid = winreg.QueryValue(key, None)
        except OSError:
            clsid = ""
        local_server = "<missing>"
        if clsid:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CLASSES_ROOT,
                    rf"CLSID\{clsid}\LocalServer32",
                    0,
                    access,
                ) as key:
                    local_server = winreg.QueryValue(key, None)
            except OSError:
                pass
        logger.info(
            "outlook_diag: registry COM view=%s Outlook.Application.CLSID=%r LocalServer32=%r",
            view_name,
            clsid or "<missing>",
            local_server,
        )


def _log_dependency_versions(logger) -> None:
    versions: list[str] = []
    for package in ("pywin32", "pywinauto", "comtypes", "psutil", "Pillow"):
        try:
            value = metadata.version(package)
        except metadata.PackageNotFoundError:
            value = "<missing>"
        except Exception as exc:
            value = f"<error:{type(exc).__name__}>"
        versions.append(f"{package}={value}")
    logger.info("outlook_diag: dependencies %s", " ".join(versions))


def _log_outlook_executables(logger) -> None:
    candidates: list[tuple[str, Path | None]] = [
        ("PATH/OUTLOOK.EXE", _path_or_none(shutil.which("OUTLOOK.EXE"))),
        ("PATH/olk.exe", _path_or_none(shutil.which("olk.exe"))),
    ]
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if not base:
            continue
        for office in ("Office16", "Office15"):
            candidates.append(
                (
                    f"{variable}/{office}",
                    Path(base) / "Microsoft Office" / "root" / office / "OUTLOOK.EXE",
                )
            )
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            (
                "LOCALAPPDATA/WindowsApps/olk.exe",
                Path(local_app_data) / "Microsoft" / "WindowsApps" / "olk.exe",
            )
        )

    seen: set[str] = set()
    for source, path in candidates:
        normalized = str(path).casefold() if path else f"<none>:{source}"
        if normalized in seen:
            continue
        seen.add(normalized)
        logger.info("outlook_diag: executable source=%s %s", source, _describe_path(path))


def _describe_path(path: Path | None) -> str:
    if path is None:
        return "path=<not found>"
    try:
        exists = path.exists()
        is_file = path.is_file()
        stat = path.stat() if exists else None
        version = _windows_file_version(path) if is_file else ""
        return (
            f"path={path} exists={exists} is_file={is_file} "
            f"size={stat.st_size if stat else '<unknown>'} "
            f"modified={datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds') if stat else '<unknown>'} "
            f"version={version or '<unknown>'}"
        )
    except OSError as exc:
        return f"path={path} inspection_error={exc!r}"


def _windows_file_version(path: Path) -> str:
    if os.name != "nt":
        return ""
    try:
        import win32api

        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms = int(info["FileVersionMS"])
        ls = int(info["FileVersionLS"])
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return ""


def _path_or_none(value: str | None) -> Path | None:
    return Path(value) if value else None


def _safe_object_attr(value, attribute: str):
    try:
        return getattr(value, attribute)
    except Exception:
        return None


def _safe_attr(value, attribute: str) -> str:
    result = _safe_object_attr(value, attribute)
    if result is None:
        return ""
    try:
        return str(result)
    except Exception:
        return "<unprintable>"


def _safe_call(value, method_name: str):
    try:
        return getattr(value, method_name)()
    except Exception:
        return "<unavailable>"
