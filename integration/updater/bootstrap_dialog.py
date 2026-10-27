"""Native first-download dialog using the package's existing GTK3 dependency."""

import ctypes as c
import os
import select
import sys
from pathlib import Path


def desktop_id() -> str:
    return (os.environ.get("CHROME_DESKTOP") or "discord-canary.desktop").removesuffix(".desktop")


def icon_path() -> Path:
    return (
        Path(os.environ.get("DPE_APP_ROOT") or Path(__file__).resolve().parents[2]) / "discord.png"
    )


def zenity_command(*arguments: str) -> list[str]:
    # GTK uses argv[0] as its program name / Wayland app_id. Unlike --name and
    # --class, this works with both GTK3 and GTK4 Zenity. Execute "zenity" while
    # retaining our identity as argv[0]. GTK4 accepts but ignores --window-icon;
    # Wayland resolves the icon from the matching installed desktop entry.
    return [desktop_id(), "--window-icon=" + str(icon_path()), *arguments]


def show(*, preview: bool = False) -> int:
    # ctypes is part of Python; this needs neither Tkinter nor PyGObject.
    gtk = c.CDLL("libgtk-3.so.0")
    gdk = c.CDLL("libgdk-3.so.0")
    pixbuf = c.CDLL("libgdk_pixbuf-2.0.so.0")
    glib = c.CDLL("libglib-2.0.so.0")
    gobject = c.CDLL("libgobject-2.0.so.0")
    pointer, integer = c.c_void_p, c.c_int

    def bind(library, name, result, *arguments):
        function = getattr(library, name)
        function.restype, function.argtypes = result, list(arguments)
        return function

    # Set the Wayland app_id and both X11 WM_CLASS components before GTK creates
    # any windows. A window icon alone cannot identify an app on Wayland.
    identity = desktop_id().encode()
    bind(glib, "g_set_prgname", None, c.c_char_p)(identity)
    bind(glib, "g_set_application_name", None, c.c_char_p)(b"Discord Patched Electron")
    init = bind(gtk, "gtk_init_check", integer, pointer, pointer)
    if not init(None, None):
        raise RuntimeError("Cannot open a GTK display")
    # GTK initialization capitalizes its fallback class; override it afterward.
    bind(gdk, "gdk_set_program_class", None, c.c_char_p)(identity)
    bind(gtk, "gtk_window_set_default_icon_name", None, c.c_char_p)(identity)
    # GDK silently discards icons exceeding the X11 selection size limit. The
    # packaged artwork is 1024px; load a small copy without changing that asset.
    icon = bind(
        pixbuf,
        "gdk_pixbuf_new_from_file_at_scale",
        pointer,
        c.c_char_p,
        integer,
        integer,
        integer,
        pointer,
    )(os.fsencode(icon_path()), 128, 128, 1, None)
    if icon:
        bind(gtk, "gtk_window_set_default_icon", None, pointer)(icon)
        bind(gobject, "g_object_unref", None, pointer)(icon)
    builder_new = bind(gtk, "gtk_builder_new", pointer)
    builder_add = bind(
        gtk, "gtk_builder_add_from_string", c.c_uint, pointer, c.c_char_p, c.c_ssize_t, pointer
    )
    get = bind(gtk, "gtk_builder_get_object", pointer, pointer, c.c_char_p)
    builder = builder_new()
    layout = Path(__file__).with_name("bootstrap-dialog.ui").read_bytes()
    if not builder_add(builder, layout, len(layout), None):
        raise RuntimeError("Cannot load the download dialog")
    dialog, progress = get(builder, b"dialog"), get(builder, b"progress")
    respond = bind(gtk, "gtk_dialog_response", None, pointer, integer)
    pulse = bind(gtk, "gtk_progress_bar_pulse", None, pointer)
    fraction = bind(gtk, "gtk_progress_bar_set_fraction", None, pointer, c.c_double)
    settings = bind(gtk, "gtk_settings_get_default", pointer)()
    get_setting = bind(gobject, "g_object_get", None, pointer, c.c_char_p)
    callback_type = c.CFUNCTYPE(integer, pointer)

    @callback_type
    def tick(_data):
        if not preview and select.select([sys.stdin], [], [], 0)[0]:
            if not os.read(sys.stdin.fileno(), 4096):
                respond(dialog, 0)
                return 0
        animations = integer()
        get_setting(settings, b"gtk-enable-animations", c.byref(animations), None)
        if animations.value:
            pulse(progress)
        else:
            # The status label still communicates the loading state without motion.
            fraction(progress, 0)
        return 1

    timeout = bind(glib, "g_timeout_add", c.c_uint, c.c_uint, callback_type, pointer)
    source = timeout(100, tick, None)
    bind(gtk, "gtk_widget_show_all", None, pointer)(dialog)
    response = bind(gtk, "gtk_dialog_run", integer, pointer)(dialog)
    if response != 0:
        bind(glib, "g_source_remove", integer, c.c_uint)(source)
    bind(gtk, "gtk_widget_destroy", None, pointer)(dialog)
    bind(gobject, "g_object_unref", None, pointer)(builder)
    return 0 if response == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(show(preview="--preview" in sys.argv[1:]))
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        # Portable installs may lack GTK3. Keep a native fallback when available.
        try:
            os.execvp(
                "zenity",
                zenity_command(
                    "--progress",
                    "--pulsate",
                    "--auto-close",
                    "--width=420",
                    "--title=Discord Patched Electron",
                    "--text=Downloading Electron for the first time...",
                ),
            )
        except OSError:
            raise SystemExit(2) from None
