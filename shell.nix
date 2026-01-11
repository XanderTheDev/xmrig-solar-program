{ pkgs ? import (fetchTarball "https://github.com/NixOS/nixpkgs/tarball/nixos-25.11") {} }:

pkgs.mkShell {
  buildInputs = [
    pkgs.python312         # Python interpreter
    pkgs.python312Packages.virtualenv
  ];

shellHook = ''
  if [ ! -d ".venv" ]; then
    python -m venv .venv
    echo "Created virtual environment in .venv"
  fi

  source .venv/bin/activate

  pip install --upgrade pip
  pip install requests

  echo "Python dev shell ready! Python version: $(python --version)"
'';
}
