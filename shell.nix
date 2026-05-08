{ pkgs ? import <nixpkgs> {} }:

let
  # Shared lib set: covers Playwright Chromium AND Camoufox (Firefox-based).
  # Camoufox needs the Firefox runtime libs in addition to the Chromium set.
  runtimeLibs = with pkgs; [
    # General
    stdenv.cc.cc.lib
    zlib
    mesa
    libdrm
    libxkbcommon
    fontconfig
    freetype
    expat

    # GTK / accessibility / audio (needed by both Chromium and Firefox)
    glib
    gtk3
    gdk-pixbuf
    pango
    cairo
    atk
    at-spi2-atk
    at-spi2-core
    alsa-lib
    cups
    dbus
    dbus-glib            # Firefox-specific
    libnotify            # Firefox-specific

    # NSS / NSPR (TLS)
    nss
    nspr

    # X11 — Chromium subset
    xorg.libX11
    xorg.libxcb
    xorg.libXcomposite
    xorg.libXdamage
    xorg.libXext
    xorg.libXfixes
    xorg.libXrandr
    # X11 — Firefox additions
    xorg.libXt
    xorg.libXtst
    xorg.libXi
    xorg.libXrender
    xorg.libXcursor
    xorg.libXScrnSaver
  ];
in
pkgs.mkShell {
  packages = [
    pkgs.python312
    pkgs.python312Packages.pip
    pkgs.python312Packages.virtualenv
  ];

  buildInputs = runtimeLibs;

  shellHook = ''
    export LD_LIBRARY_PATH=${pkgs.lib.makeLibraryPath runtimeLibs}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}

    # Camoufox/Playwright create tempfiles via mkdtemp under $TMPDIR.
    # nix-shell defaults TMPDIR to a per-shell dir that may be missing or
    # cleaned, breaking subprocess launches. Fall back to /tmp if absent.
    if [ -n "$TMPDIR" ] && [ ! -d "$TMPDIR" ]; then
      mkdir -p "$TMPDIR" 2>/dev/null || export TMPDIR=/tmp
    fi
    : "''${TMPDIR:=/tmp}"

    if [ ! -d venv ]; then
      python -m venv venv
    fi
    source venv/bin/activate
  '';
}
