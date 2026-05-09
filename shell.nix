{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = [
    pkgs.python312
    pkgs.python312Packages.pip
    pkgs.python312Packages.virtualenv
  ];

  buildInputs = with pkgs; [
    stdenv.cc.cc.lib
    zlib
    mesa
    nss
    nspr
    alsa-lib
    atk
    at-spi2-atk
    at-spi2-core
    cairo
    cups
    dbus
    expat
    fontconfig
    glib
    gtk3
    libdrm
    libxkbcommon
    pango
    xorg.libX11
    xorg.libXcomposite
    xorg.libXdamage
    xorg.libXext
    xorg.libXfixes
    xorg.libXrandr
    xorg.libxcb
  ];

  shellHook = ''
    export LD_LIBRARY_PATH=${pkgs.lib.makeLibraryPath (with pkgs; [
      stdenv.cc.cc.lib
      zlib
      mesa
      nss
      nspr
      alsa-lib
      atk
      at-spi2-atk
      at-spi2-core
      cairo
      cups
      dbus
      expat
      fontconfig
      glib
      gtk3
      libdrm
      libxkbcommon
      pango
      xorg.libX11
      xorg.libXcomposite
      xorg.libXdamage
      xorg.libXext
      xorg.libXfixes
      xorg.libXrandr
      xorg.libxcb
    ])}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}

    if [ ! -d venv ]; then
      python -m venv venv
    fi
    source venv/bin/activate
  '';
}
