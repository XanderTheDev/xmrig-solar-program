# A Nix shell definition that provides Python 3.12 and automatically
# manages a virtual environment inside the project directory.
#
# The first line imports a specific version of nixpkgs (NixOS 25.11).
{ pkgs ? import (fetchTarball "https://github.com/NixOS/nixpkgs/tarball/nixos-25.11") {} }:

pkgs.mkShell {
  # Packages that will be available in the development shell.
  buildInputs = [
    pkgs.python312                       # Python 3.12 interpreter
    pkgs.python312Packages.virtualenv    # virtualenv tool (not strictly needed since Python 3.12 has venv)
  ];

  # shellHook runs each time you enter `nix-shell` or `nix develop`.
  shellHook = ''
    # Create a virtual environment if it doesn't already exist.
    if [ ! -d ".venv" ]; then
      python -m venv .venv
      echo "Created virtual environment in .venv"
    fi

    # Activate the virtual environment so Python / pip work inside it.
    source .venv/bin/activate

    # Ensure pip is up to date.
    pip install --upgrade pip

    # Install any default development dependencies.
    pip install requests

    echo "Python dev shell ready! Python version: $(python --version)"
  '';
}
