// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (C) 2026 korxspyre
/* Makes Flet's window program a proper KDE window on NanoBorealis. /usr/bin/nanoborealis-app loads
 * it with LD_PRELOAD. It needs no headers or libraries, so the OS build compiles it with gcc alone:
 *   gcc -O2 -shared -fPIC -nostdlib -o libnanoborealis-window.so nanoborealis-window.c */

#define RTLD_NEXT ((void *) -1l)
extern void *dlsym(void *handle, const char *symbol);
extern char *getenv(const char *name);
extern int unsetenv(const char *name);
extern long readlink(const char *path, char *buffer, unsigned long size);

static char app_id_value[128];

/* In Flet's window program, take this fix and the app id out of the environment once they're read,
 * so the programs it opens (a browser for a link, say) are left as they are. The Python side keeps
 * them until the window is up: it's what starts the window program. */
__attribute__((constructor)) static void keep_to_the_window(void) {
    char exe[4096];
    long length = readlink("/proc/self/exe", exe, sizeof exe - 1);
    if (length < 5 || exe[length - 5] != '/' || exe[length - 4] != 'f' || exe[length - 3] != 'l'
        || exe[length - 2] != 'e' || exe[length - 1] != 't')
        return;  /* not Flet's window program ("/flet") */
    const char *id = getenv("FLET_APP_ID");
    for (unsigned long i = 0; id && id[i] && i < sizeof app_id_value - 1; i++)
        app_id_value[i] = id[i];
    unsetenv("LD_PRELOAD");
    unsetenv("FLET_APP_ID");
}

/* On Wayland, Flet gives its window a GNOME-style header bar, and GTK then never asks KDE for a
 * title bar. Without the header bar, KWin draws the same Breeze title bar as for any other app. */
void gtk_window_set_titlebar(void *window, void *titlebar) {
    (void) window;
    (void) titlebar;
}

/* Flet names every app "com.appveyor.flet" (its program name and its application id), which
 * matches no app here, so the dock showed a stray generic icon. Both become FLET_APP_ID
 * ("nanoborealis"), whose app menu entry gives the window its name and icon. */
static const char *app_id(const char *fallback) {
    const char *id = app_id_value[0] ? app_id_value : getenv("FLET_APP_ID");
    return id && *id ? id : fallback;
}

void g_set_prgname(const char *prgname) {
    static void (*real)(const char *);
    if (!real)
        real = (void (*)(const char *)) dlsym(RTLD_NEXT, "g_set_prgname");
    if (real)
        real(app_id(prgname));
}

typedef void (*set_dbus_properties_fn)(void *, const char *, const char *, const char *,
                                       const char *, const char *, const char *);

void gdk_wayland_window_set_dbus_properties_libgtk_only(void *window, const char *application_id,
                                                        const char *app_menu_path,
                                                        const char *menubar_path,
                                                        const char *window_object_path,
                                                        const char *application_object_path,
                                                        const char *unique_bus_name) {
    static set_dbus_properties_fn real;
    if (!real)
        real = (set_dbus_properties_fn) dlsym(RTLD_NEXT, "gdk_wayland_window_set_dbus_properties_libgtk_only");
    if (real)
        real(window, app_id(application_id), app_menu_path, menubar_path, window_object_path,
             application_object_path, unique_bus_name);
}
